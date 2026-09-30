"""Local mailbox worker with an optional foreground candidate refresh signal."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .engine import Engine
from .rime_protocol import (
    MAX_BYTES,
    SESSION_PATTERN,
    parse_commit_event,
    parse_rank_request,
    render_response,
    valid_permutation,
)

if TYPE_CHECKING:
    from .rime_refresh import RefreshTarget, WindowsCandidateRefresh

_REQUEST = re.compile(rf"({SESSION_PATTERN})\.request")
_RESPONSE = re.compile(rf"({SESSION_PATTERN})\.response")
_COMMIT = re.compile(rf"({SESSION_PATTERN})\.([1-9][0-9]{{0,15}})\.commit")
_TEMPORARY = re.compile(
    rf"(?:\.smart-im-[a-zA-Z0-9_-]+|"
    rf"{SESSION_PATTERN}\.(?:request|[1-9][0-9]{{0,15}}\.commit))\.tmp"
)
STALE_SECONDS = 30
MAX_SESSIONS = 256
MAX_COMMITS = 4096


class MailboxService:
    """Own one dedicated runtime directory and one engine until ``close``.

    Rank requests replace older requests from their session and wait for an
    80 ms quiet period by default. Commits are separate files, processed without
    that delay, and deduplicated in memory after success; this is deliberately
    not a durable exactly-once queue. Pending commits expire after thirty seconds.
    """

    def __init__(
        self,
        runtime_dir: Path | str,
        engine: Engine,
        poll_interval: float = 0.02,
        debounce_interval: float = 0.08,
        *,
        refresh: WindowsCandidateRefresh | None = None,
    ) -> None:
        if not 0.001 <= poll_interval <= 1.0:
            raise ValueError("poll_interval must be between 0.001 and 1 second")
        if not 0 <= debounce_interval <= 1.0:
            raise ValueError("debounce_interval must be between 0 and 1 second")
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = engine
        self.poll_interval = poll_interval
        self.debounce_interval = debounce_interval
        self.refresh = refresh
        self._closed = False
        self._heartbeat_at = float("-inf")
        self._heartbeat_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._processed: OrderedDict[str, bytes] = OrderedDict()
        self._pending: OrderedDict[str, tuple[bytes, float, RefreshTarget | None]] = OrderedDict()
        self._committed: OrderedDict[tuple[str, int], None] = OrderedDict()
        self._lock_file = (self.runtime_dir / "service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._lock_file.seek(0, os.SEEK_END)
                if self._lock_file.tell() == 0:
                    self._lock_file.write(b"\0")
                    self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise RuntimeError("another Smart IM service is using this runtime directory") from exc

    @staticmethod
    def _remember(cache: OrderedDict, key: object, value: object, limit: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            if path.is_symlink() or not path.is_file():
                return None
            with path.open("rb") as stream:
                return stream.read(MAX_BYTES + 1)
        except OSError:
            return None

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows may temporarily deny deletion while Lua has a file open.
            pass

    def _atomic_write(self, path: Path, data: bytes) -> bool:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".smart-im-", suffix=".tmp", dir=self.runtime_dir, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, path)
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                self._unlink(temporary)

    def _files(self, now: float) -> tuple[list[Path], list[Path]]:
        requests: list[tuple[int, Path]] = []
        commits: list[tuple[int, int, Path]] = []
        for path in self.runtime_dir.iterdir():
            request = _REQUEST.fullmatch(path.name)
            commit = _COMMIT.fullmatch(path.name)
            response = _RESPONSE.fullmatch(path.name)
            temporary = _TEMPORARY.fullmatch(path.name)
            if not (request or commit or response or temporary):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > STALE_SECONDS:
                self._unlink(path)
                self._processed.pop(path.name, None)
                self._pending.pop(path.name, None)
                continue
            if request:
                requests.append((stat.st_mtime_ns, path))
            elif commit:
                commits.append((stat.st_mtime_ns, int(commit.group(2)), path))
        commits.sort(key=lambda item: (item[0], item[1], item[2].name))
        requests.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        return [item[1] for item in requests], [item[2] for item in commits]

    def _rank(self, path: Path) -> bool:
        """Return whether this request should stop the scan of older sessions."""
        try:
            if time.time() - path.stat().st_mtime > STALE_SECONDS:
                self._unlink(path)
                self._processed.pop(path.name, None)
                self._pending.pop(path.name, None)
                return False
        except OSError:
            return False
        data = self._read(path)
        if data is None or self._processed.get(path.name) == data:
            return False
        try:
            request = parse_rank_request(data)
            if path.name != f"{request.session}.request":
                raise ValueError("session does not match filename")
        except ValueError:
            self._remember(self._processed, path.name, data, MAX_SESSIONS)
            return False
        now = time.monotonic()
        pending = self._pending.get(path.name)
        if pending is None or pending[0] != data:
            target = self.refresh.capture() if self.refresh else None
            pending = (data, now, target)
            self._remember(self._pending, path.name, pending, MAX_SESSIONS)
        if now - pending[1] < self.debounce_interval:
            # Coalesce intermediate spellings before starting slow inference.
            # Do not let an older session occupy the model during this wait.
            # Commits and heartbeats still run on every poll.
            return True
        # Bind notification to the focus observed for this request. Allow the
        # final typing key to be released during debounce before taking the
        # last-input baseline; a focus change must never retarget the signal.
        target = self.refresh.ready(pending[2]) if self.refresh else None
        if pending[2] is not None and target is None:
            # A long final keypress can outlast debounce. Keep its request
            # pending until keys are released instead of computing a result
            # that could never notify. Changed focus is never rebound here;
            # a newer request takes priority and idle requests still expire.
            return True
        fallback = False
        try:
            order = self.engine.rerank(
                list(request.candidates),
                context=request.context,
                pinyin=request.pinyin,
                private=not (self.engine.learning and request.learning),
            )
            if not valid_permutation(order, len(request.candidates)):
                raise ValueError("invalid model permutation")
            fallback = bool(self.engine.model_error)
        except Exception:
            # Never expose typed text or exception messages through logs.
            order = list(range(len(request.candidates)))
            fallback = True
        # A newer composition may arrive while inference is running.
        if self._read(path) != data:
            return True
        response = self.runtime_dir / f"{request.session}.response"
        if self._atomic_write(response, render_response(request, order, fallback=fallback)):
            self._remember(self._processed, path.name, data, MAX_SESSIONS)
            self._pending.pop(path.name, None)
            # Publishing can race cancellation as well as inference. Lua also
            # validates the session/revision/fingerprint before touching its menu.
            if self.refresh and self._read(path) == data:
                self.refresh.notify(target)
        return True

    def _commit(self, path: Path) -> bool:
        data = self._read(path)
        if data is None:
            return False
        try:
            event = parse_commit_event(data)
            if path.name != f"{event.session}.{event.sequence}.commit":
                raise ValueError("event does not match filename")
        except ValueError:
            self._unlink(path)
            return True
        key = (event.session, event.sequence)
        if key not in self._committed:
            if self.engine.learning and event.learning:
                try:
                    self.engine.commit_external(
                        event.pinyin, event.text, context=event.context, private=False
                    )
                except Exception:
                    # Leave the event for retry; it still has a bounded lifetime.
                    return False
            self._remember(self._committed, key, None, MAX_COMMITS)
        self._unlink(path)
        return True

    def poll_once(self) -> None:
        if self._closed:
            raise RuntimeError("service is closed")
        now = time.time()
        self._heartbeat()
        try:
            requests, commits = self._files(now)
        except OSError:
            return
        # Confirmed selections should inform the next composition's ranking.
        for path in commits:
            if not self._commit(path):
                break
        for path in requests:
            # Re-scan after every slow inference. A newly active application
            # should not queue behind a whole directory of idle sessions.
            if self._rank(path):
                break

    def _heartbeat(self) -> None:
        # Inference can take several seconds. Liveness must not depend on it.
        with self._heartbeat_lock:
            now = time.time()
            if not self._closed and now - self._heartbeat_at >= 1:
                if self._atomic_write(self.runtime_dir / "heartbeat", f"{int(now)}\n".encode()):
                    self._heartbeat_at = now

    def _keep_alive(self) -> None:
        while not self._heartbeat_stop.is_set():
            self._heartbeat()
            self._heartbeat_stop.wait(0.5)

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join()
            self._heartbeat_thread = None

    def run(self, stop_event: threading.Event | None = None) -> None:
        if self._closed:
            raise RuntimeError("service is closed")
        stop = stop_event if stop_event is not None else threading.Event()
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(target=self._keep_alive, daemon=True)
        self._heartbeat_thread.start()
        try:
            while not stop.is_set():
                self.poll_once()
                stop.wait(self.poll_interval)
        finally:
            self._stop_heartbeat()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_heartbeat()
        try:
            self._unlink(self.runtime_dir / "heartbeat")
            # Drop all queued optional learning on normal exit: a subsequent
            # personal-data reset must not replay these events after restart.
            # Do not remove arbitrary files in a user-specified directory.
            try:
                for path in self.runtime_dir.iterdir():
                    if (
                        _REQUEST.fullmatch(path.name)
                        or _RESPONSE.fullmatch(path.name)
                        or _COMMIT.fullmatch(path.name)
                        or _TEMPORARY.fullmatch(path.name)
                    ):
                        if not path.is_symlink() and path.is_file():
                            self._unlink(path)
            except OSError:
                pass
            self.engine.close()
        finally:
            if os.name == "nt":
                import msvcrt

                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

    def __enter__(self) -> MailboxService:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
