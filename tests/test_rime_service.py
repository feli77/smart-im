from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from smart_im.rime_protocol import (
    MAX_BYTES,
    parse_commit_event,
    parse_rank_request,
    valid_permutation,
)
from smart_im.rime_service import MailboxService


def message(
    operation="RANK",
    session="test-session",
    sequence=1,
    learning=False,
    pinyin="shijie",
    context="你好\n",
    texts=("世界", "视界", "世界"),
):
    header = f"SMARTIM1\t{operation}\t{session}\t{sequence}\t{int(learning)}\n"
    fields = [pinyin, context, *texts]
    return (header + "".join(text.encode().hex() + "\n" for text in fields)).encode()


class FakeEngine:
    def __init__(self, learning=False, order=None):
        self.learning = learning
        self.model_error = ""
        self.order = [2, 0, 1] if order is None else order
        self.ranks = []
        self.commits = []
        self.closed = False

    def rerank(self, texts, **kwargs):
        self.ranks.append((texts, kwargs))
        return self.order

    def commit_external(self, pinyin, text, **kwargs):
        self.commits.append((pinyin, text, kwargs))

    def close(self):
        self.closed = True


class FakeRefresh:
    def __init__(self):
        self.foreground = object()
        self.ready_target = object()
        self.captures = []
        self.ready_calls = []
        self.notifications = []
        self.result = True

    def capture(self):
        self.captures.append(self.foreground)
        return self.foreground

    def ready(self, target):
        self.ready_calls.append(target)
        return self.ready_target if target is self.foreground else None

    def notify(self, target):
        self.notifications.append(target)
        return self.result


def test_protocol_round_trip_preserves_unicode_duplicates_and_context():
    request = parse_rank_request(message())
    assert request.session == "test-session"
    assert request.revision == 1
    assert request.context == "你好\n"
    assert request.candidates == ("世界", "视界", "世界")
    assert parse_rank_request(message().replace(b"\n", b"\r\n")) == request
    event = parse_commit_event(message("COMMIT", texts=("世界",)))
    assert event.text == "世界"
    assert not event.learning


@pytest.mark.parametrize(
    "data",
    [
        message(session="../escape"),
        message(session="a" * 65),
        message(sequence=0),
        message(sequence=-1),
        message(sequence=2**53),
        message(pinyin="a" * 97),
        message(context="你" * 129),
        message(texts=("你" * 65,)),
        message(texts=("",)),
        message(texts=()),
        message(texts=("你",) * 10),
        message()[:-1],
        message().replace(b"\t0\n", b"\t2\n"),
        b"SMARTIM1\tRANK\ts\t1\t0\n61\n\nff\n",
        b"SMARTIM1\tRANK\ts\t1\t0\n61\n\n1\n",
        b"SMARTIM1\tRANK\ts\t1\t0\n61\n\n61 62\n",
        b"x" * MAX_BYTES + b"\n",
    ],
)
def test_protocol_rejects_malformed_or_unbounded_requests(data):
    with pytest.raises(ValueError):
        parse_rank_request(data)


@pytest.mark.parametrize("order", [[True, 0], [0, 0], [1], [1, 2], [0.0, 1], "0,1"])
def test_permutation_validation_is_strict(order):
    assert not valid_permutation(order, 2)


def test_worker_ranks_once_and_returns_only_one_based_indices(tmp_path):
    engine = FakeEngine()
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        service.poll_once()
        service.poll_once()
        assert len(engine.ranks) == 1
        assert (tmp_path / "test-session.response").read_text() == (
            "SMARTIM1\tRESULT\ttest-session\t1\tok\n3,1,2\n"
        )
        assert engine.ranks[0] == (
            ["世界", "视界", "世界"],
            {"context": "你好\n", "pinyin": "shijie", "private": True},
        )
        assert abs(int((tmp_path / "heartbeat").read_text()) - time.time()) < 2
    assert engine.closed
    assert not (tmp_path / "heartbeat").exists()
    assert not (tmp_path / "test-session.request").exists()
    assert not (tmp_path / "test-session.response").exists()


@pytest.mark.parametrize("service_learns", [True, False])
@pytest.mark.parametrize("client_learns", [True, False])
def test_personal_reads_and_commits_require_both_opt_ins(tmp_path, service_learns, client_learns):
    engine = FakeEngine(learning=service_learns)
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        (tmp_path / "test-session.request").write_bytes(message(learning=client_learns))
        commit = tmp_path / "test-session.1.commit"
        commit.write_bytes(message("COMMIT", learning=client_learns, texts=("世界",)))
        service.poll_once()
        allowed = service_learns and client_learns
        assert engine.ranks[0][1]["private"] is not allowed
        assert len(engine.commits) == int(allowed)
        assert not commit.exists()


def test_model_failure_and_invalid_order_fall_back_without_raw_error(tmp_path):
    engine = FakeEngine(order=[0, 0, 0])
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        path = tmp_path / "test-session.request"
        path.write_bytes(message())
        service.poll_once()
        assert (tmp_path / "test-session.response").read_text().endswith("fallback\n1,2,3\n")

        def fail(*args, **kwargs):
            raise RuntimeError("private typed content")

        engine.rerank = fail
        path.write_bytes(message(sequence=2))
        service.poll_once()
        assert (tmp_path / "test-session.response").read_text() == (
            "SMARTIM1\tRESULT\ttest-session\t2\tfallback\n1,2,3\n"
        )


def test_old_inference_cannot_overwrite_a_new_composition(tmp_path):
    engine = FakeEngine()
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        path = tmp_path / "test-session.request"
        path.write_bytes(message())
        original = engine.rerank

        def replace_while_ranking(*args, **kwargs):
            path.write_bytes(message(sequence=2))
            return original(*args, **kwargs)

        engine.rerank = replace_while_ranking
        service.poll_once()
        assert not (tmp_path / "test-session.response").exists()
        engine.rerank = original
        service.poll_once()
        assert "\t2\tok\n" in (tmp_path / "test-session.response").read_text()


def test_failed_atomic_response_is_retried(tmp_path, monkeypatch):
    refresh = FakeRefresh()
    with MailboxService(tmp_path, FakeEngine(), debounce_interval=0, refresh=refresh) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        original = os.replace

        def sharing_failure(source, destination):
            if Path(destination).suffix == ".response":
                raise PermissionError("simulated Windows file sharing")
            original(source, destination)

        monkeypatch.setattr(os, "replace", sharing_failure)
        service.poll_once()
        assert not (tmp_path / "test-session.response").exists()
        assert not list(tmp_path.glob(".smart-im-*.tmp"))
        assert not refresh.notifications
        monkeypatch.setattr(os, "replace", original)
        service.poll_once()
        assert (tmp_path / "test-session.response").exists()
        assert refresh.notifications == [refresh.ready_target]
        assert len(refresh.captures) == 1


def test_commit_deletion_failure_does_not_learn_twice(tmp_path, monkeypatch):
    engine = FakeEngine(learning=True)
    with MailboxService(tmp_path, engine) as service:
        event = tmp_path / "test-session.1.commit"
        event.write_bytes(message("COMMIT", learning=True, texts=("世界",)))
        original = service._unlink
        monkeypatch.setattr(service, "_unlink", lambda path: None)
        service.poll_once()
        service.poll_once()
        assert len(engine.commits) == 1
        monkeypatch.setattr(service, "_unlink", original)
        service.poll_once()
        assert not event.exists()
        event.write_bytes(message("COMMIT", learning=True, texts=("世界",)))
        service.poll_once()
        assert len(engine.commits) == 1


def test_failed_commit_retries_and_processes_events_in_order(tmp_path):
    engine = FakeEngine(learning=True)
    with MailboxService(tmp_path, engine) as service:
        for seq in [2, 1]:
            path = tmp_path / f"test-session.{seq}.commit"
            path.write_bytes(message("COMMIT", sequence=seq, learning=True, texts=(f"词{seq}",)))
            os.utime(path, (time.time(), time.time() - 3 + seq))
        original = engine.commit_external

        def fail_once(*args, **kwargs):
            engine.commit_external = original
            raise OSError("temporary store error")

        engine.commit_external = fail_once
        service.poll_once()
        assert (tmp_path / "test-session.1.commit").exists()
        assert not engine.commits
        service.poll_once()
        assert [item[1] for item in engine.commits] == ["词1", "词2"]
        assert not list(tmp_path.glob("*.commit"))


def test_old_commits_are_processed_before_new_requests(tmp_path):
    engine = FakeEngine(learning=True)
    order = []
    engine.commit_external = lambda pinyin, text, **kwargs: order.append(text)
    engine.rerank = lambda *args, **kwargs: order.append("rank") or [0, 1, 2]
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        stamp = time.time()
        for seq in [2, 1]:
            path = tmp_path / f"test-session.{seq}.commit"
            path.write_bytes(message("COMMIT", sequence=seq, learning=True, texts=(f"词{seq}",)))
            os.utime(path, (stamp, stamp))
        (tmp_path / "test-session.request").write_bytes(message(learning=True))
        service.poll_once()
        assert order == ["词1", "词2", "rank"]


def test_stale_managed_files_removed_but_unrelated_files_untouched(tmp_path):
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep me")
    for name in ["stale.request", "stale.response", "stale.1.commit", ".smart-im-old.tmp"]:
        path = tmp_path / name
        path.write_bytes(message())
        os.utime(path, (1, 1))
    with MailboxService(tmp_path, FakeEngine()) as service:
        service.poll_once()
        assert unrelated.read_text() == "keep me"
        assert not list(tmp_path.glob("stale.*"))
        assert not (tmp_path / ".smart-im-old.tmp").exists()
    assert unrelated.exists()


@pytest.mark.parametrize("name", ["lua-session.request.tmp", "lua-session.42.commit.tmp"])
def test_stale_lua_temporary_files_are_removed_without_processing(tmp_path, name):
    temporary = tmp_path / name
    temporary.write_bytes(message("COMMIT", learning=True, texts=("世界",)))
    os.utime(temporary, (1, 1))
    engine = FakeEngine(learning=True)
    with MailboxService(tmp_path, engine) as service:
        service.poll_once()
        assert not temporary.exists()
        assert not engine.ranks
        assert not engine.commits


@pytest.mark.parametrize("name", ["lua-session.request.tmp", "lua-session.42.commit.tmp"])
def test_fresh_lua_temporary_files_survive_poll_but_are_removed_on_exit(tmp_path, name):
    temporary = tmp_path / name
    temporary.write_bytes(message())
    engine = FakeEngine()
    with MailboxService(tmp_path, engine) as service:
        service.poll_once()
        assert temporary.exists()
        assert not engine.ranks
        assert not engine.commits
    assert not temporary.exists()


@pytest.mark.parametrize(
    "name",
    [
        "notes.tmp",
        "lua-session.response.tmp",
        "lua-session.0.commit.tmp",
        "lua-session.01.commit.tmp",
        "lua-session.1.commit.tmp.backup",
        "lua-session.extra.request.tmp",
        "a" * 65 + ".request.tmp",
    ],
)
def test_temporary_cleanup_matches_only_exact_protocol_names(tmp_path, name):
    unrelated = tmp_path / name
    unrelated.write_text("keep me")
    os.utime(unrelated, (1, 1))
    with MailboxService(tmp_path, FakeEngine()) as service:
        service.poll_once()
        assert unrelated.exists()
    assert unrelated.read_text() == "keep me"


def test_malformed_and_mismatched_files_never_reach_engine(tmp_path):
    engine = FakeEngine(learning=True)
    with MailboxService(tmp_path, engine) as service:
        (tmp_path / "bad.request").write_bytes(b"x" * (MAX_BYTES + 10))
        (tmp_path / "different.request").write_bytes(message())
        (tmp_path / "test-session.2.commit").write_bytes(
            message("COMMIT", learning=True, texts=("世界",))
        )
        service.poll_once()
        assert not engine.ranks
        assert not engine.commits
        assert not list(tmp_path.glob("*.response"))
        assert not list(tmp_path.glob("*.commit"))


def test_single_service_lock_released_on_close(tmp_path):
    service = MailboxService(tmp_path, FakeEngine())
    try:
        with pytest.raises(RuntimeError, match="another Smart IM service"):
            MailboxService(tmp_path, FakeEngine())
    finally:
        service.close()
    service.close()
    with MailboxService(tmp_path, FakeEngine()) as replacement:
        replacement.poll_once()


def test_shutdown_discards_pending_commits_and_run_accepts_stop_event(tmp_path):
    engine = FakeEngine()
    pending = tmp_path / "test-session.1.commit"
    pending.write_bytes(message("COMMIT", learning=True, texts=("世界",)))
    with MailboxService(tmp_path, engine) as service:
        stop = threading.Event()
        stop.set()
        service.run(stop)
    assert not pending.exists()
    with pytest.raises(RuntimeError, match="closed"):
        service.poll_once()


def test_heartbeat_not_rewritten_on_every_poll(tmp_path, monkeypatch):
    with MailboxService(tmp_path, FakeEngine()) as service:
        monkeypatch.setattr(time, "time", lambda: 1000.2)
        service.poll_once()
        heartbeat = tmp_path / "heartbeat"
        first_stat = heartbeat.stat()
        monkeypatch.setattr(time, "time", lambda: 1000.8)
        service.poll_once()
        assert heartbeat.stat().st_mtime_ns == first_stat.st_mtime_ns
        monkeypatch.setattr(time, "time", lambda: 1001.3)
        service.poll_once()
        assert heartbeat.read_text() == "1001\n"


def test_slow_model_does_not_block_heartbeat_and_shutdown_stops_thread(tmp_path, monkeypatch):
    entered, release, pulse, stop = (threading.Event() for _ in range(4))
    engine = FakeEngine()
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    path = tmp_path / "test-session.request"
    path.write_bytes(message())
    os.utime(path, (1000, 1000))

    def slow_rank(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return [2, 0, 1]

    engine.rerank = slow_rank
    with MailboxService(tmp_path, engine) as service:
        write = service._atomic_write

        def observe(destination, data):
            success = write(destination, data)
            if destination.name == "heartbeat" and data == b"1004\n" and success:
                pulse.set()
            return success

        monkeypatch.setattr(service, "_atomic_write", observe)
        worker = threading.Thread(target=service.run, args=(stop,))
        worker.start()
        try:
            assert entered.wait(3)
            clock[0] = 1004.0  # Beyond Lua's 3-second liveness window.
            assert pulse.wait(3)
            assert not release.is_set()
        finally:
            stop.set()
            release.set()
            worker.join(5)
        assert not worker.is_alive()
        assert service._heartbeat_thread is None
    assert not (tmp_path / "heartbeat").exists()


def test_latest_active_session_gets_priority_and_requests_are_rescanned(tmp_path):
    engine = FakeEngine()
    with MailboxService(tmp_path, engine, debounce_interval=0) as service:
        stamp = time.time()
        for session, age in [("older", 2), ("active", 1)]:
            path = tmp_path / f"{session}.request"
            path.write_bytes(message(session=session, pinyin=session))
            os.utime(path, (stamp - age, stamp - age))
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["active"]
        (tmp_path / "newest.request").write_bytes(message(session="newest", pinyin="newest"))
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["active", "newest"]
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["active", "newest", "older"]


def test_debounce_ranks_only_the_latest_spelling_after_a_quiet_period(tmp_path, monkeypatch):
    engine = FakeEngine()
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with MailboxService(tmp_path, engine) as service:
        path = tmp_path / "test-session.request"
        for revision, pinyin in enumerate(["sh", "shij", "shijie"], 1):
            clock[0] += 0.04
            path.write_bytes(message(sequence=revision, pinyin=pinyin))
            service.poll_once()
            assert not engine.ranks
        clock[0] += 0.07
        service.poll_once()
        assert not engine.ranks
        assert not (tmp_path / "test-session.response").exists()
        clock[0] += 0.02
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["shijie"]
        assert "\t3\tok\n" in (tmp_path / "test-session.response").read_text()


def test_debounce_uses_observed_content_not_file_modification_time(tmp_path, monkeypatch):
    engine = FakeEngine()
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with MailboxService(tmp_path, engine) as service:
        path = tmp_path / "test-session.request"
        path.write_bytes(message())
        os.utime(path, (time.time() + 1000, time.time() + 1000))
        service.poll_once()
        assert not engine.ranks
        clock[0] += 0.1
        path.write_bytes(message())
        service.poll_once()
        assert len(engine.ranks) == 1


def test_new_composition_debounce_prevents_older_session_blocking_it(tmp_path, monkeypatch):
    engine = FakeEngine()
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with MailboxService(tmp_path, engine) as service:
        older = tmp_path / "older.request"
        older.write_bytes(message(session="older", pinyin="older"))
        stamp = time.time() - 1
        os.utime(older, (stamp, stamp))
        service.poll_once()
        clock[0] += 0.1
        (tmp_path / "active.request").write_bytes(message(session="active", pinyin="active"))
        service.poll_once()
        assert not engine.ranks
        clock[0] += 0.1
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["active"]
        service.poll_once()
        assert [call[1]["pinyin"] for call in engine.ranks] == ["active", "older"]


def test_debounce_does_not_delay_commits_or_heartbeat(tmp_path, monkeypatch):
    engine = FakeEngine(learning=True)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    with MailboxService(tmp_path, engine) as service:
        (tmp_path / "test-session.request").write_bytes(message(learning=True))
        commit = tmp_path / "test-session.1.commit"
        commit.write_bytes(message("COMMIT", learning=True, texts=("世界",)))
        service.poll_once()
        assert not engine.ranks
        assert len(engine.commits) == 1
        assert not commit.exists()
        assert (tmp_path / "heartbeat").exists()


@pytest.mark.parametrize("debounce_interval", [-0.01, 1.01, float("nan")])
def test_debounce_interval_validation(tmp_path, debounce_interval):
    with pytest.raises(ValueError, match="debounce_interval"):
        MailboxService(tmp_path, FakeEngine(), debounce_interval=debounce_interval)


def test_refresh_runs_once_only_after_atomic_response_is_available(tmp_path, monkeypatch):
    engine = FakeEngine()
    refresh = FakeRefresh()
    response = tmp_path / "test-session.response"
    with MailboxService(tmp_path, engine, debounce_interval=0, refresh=refresh) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        rank = engine.rerank
        write = service._atomic_write
        notify = refresh.notify
        stages = []

        def observe_rank(*args, **kwargs):
            assert not response.exists()
            assert not refresh.notifications
            stages.append("rank")
            return rank(*args, **kwargs)

        def observe_write(path, data):
            if path == response:
                assert not refresh.notifications
                stages.append("publish")
            return write(path, data)

        def observe_notify(target):
            assert response.read_text() == "SMARTIM1\tRESULT\ttest-session\t1\tok\n3,1,2\n"
            stages.append("notify")
            return notify(target)

        monkeypatch.setattr(engine, "rerank", observe_rank)
        monkeypatch.setattr(service, "_atomic_write", observe_write)
        monkeypatch.setattr(refresh, "notify", observe_notify)
        service.poll_once()
        service.poll_once()
        assert stages == ["rank", "publish", "notify"]
        assert refresh.captures == [refresh.foreground]
        assert refresh.ready_calls == [refresh.foreground]
        assert refresh.notifications == [refresh.ready_target]
        assert len(engine.ranks) == 1
        assert engine.ranks[0][1]["private"] is True
        assert not engine.commits


@pytest.mark.parametrize("failure", ["invalid_order", "exception", "model_error"])
def test_refresh_notifies_fallback_with_original_order(tmp_path, failure):
    engine = FakeEngine(order=[0, 1, 2])
    refresh = FakeRefresh()
    if failure == "invalid_order":
        engine.order = [0, 0, 0]
    elif failure == "exception":

        def fail(*args, **kwargs):
            raise RuntimeError("private typed content")

        engine.rerank = fail
    else:
        engine.model_error = "unavailable"
    with MailboxService(tmp_path, engine, debounce_interval=0, refresh=refresh) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        service.poll_once()
        assert (tmp_path / "test-session.response").read_text() == (
            "SMARTIM1\tRESULT\ttest-session\t1\tfallback\n1,2,3\n"
        )
        assert refresh.notifications == [refresh.ready_target]


@pytest.mark.parametrize("mutation", ["replace", "cancel"])
@pytest.mark.parametrize("stage", ["inference", "publication"])
def test_refresh_skips_requests_changed_during_inference_or_publication(
    tmp_path, monkeypatch, mutation, stage
):
    engine = FakeEngine()
    refresh = FakeRefresh()
    request = tmp_path / "test-session.request"
    response = tmp_path / "test-session.response"
    with MailboxService(tmp_path, engine, debounce_interval=0, refresh=refresh) as service:
        request.write_bytes(message())

        def change_request():
            if mutation == "replace":
                request.write_bytes(message(sequence=2))
            else:
                request.unlink()

        rank = engine.rerank
        write = service._atomic_write

        def change_during_rank(*args, **kwargs):
            change_request()
            return rank(*args, **kwargs)

        def change_after_publish(path, data):
            success = write(path, data)
            if path == response:
                assert success
                change_request()
            return success

        if stage == "inference":
            monkeypatch.setattr(engine, "rerank", change_during_rank)
        else:
            monkeypatch.setattr(service, "_atomic_write", change_after_publish)
        service.poll_once()
        assert not refresh.notifications
        assert response.exists() is (stage == "publication")

        monkeypatch.setattr(engine, "rerank", rank)
        monkeypatch.setattr(service, "_atomic_write", write)
        service.poll_once()
        if mutation == "replace":
            assert refresh.notifications == [refresh.ready_target]
            assert "\t2\tok\n" in response.read_text()
        else:
            assert not refresh.notifications


@pytest.mark.parametrize("focus_changed", [False, True])
def test_refresh_debounce_preserves_original_foreground(tmp_path, monkeypatch, focus_changed):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    refresh = FakeRefresh()
    original_foreground = refresh.foreground
    engine = FakeEngine()
    with MailboxService(tmp_path, engine, refresh=refresh) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        service.poll_once()
        assert refresh.captures == [original_foreground]
        assert not refresh.ready_calls
        assert not engine.ranks
        assert not refresh.notifications
        if focus_changed:
            refresh.foreground = object()
        clock[0] += 0.04
        service.poll_once()
        assert not refresh.ready_calls
        clock[0] += 0.05
        service.poll_once()
        assert len(engine.ranks) == (0 if focus_changed else 1)
        assert refresh.captures == [original_foreground]
        assert refresh.ready_calls == [original_foreground]
        assert refresh.notifications == ([] if focus_changed else [refresh.ready_target])


def test_refresh_waits_for_last_typing_key_to_release_before_inference(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    refresh = FakeRefresh()
    released_target = refresh.ready_target
    refresh.ready_target = None
    engine = FakeEngine()
    with MailboxService(tmp_path, engine, refresh=refresh) as service:
        request = tmp_path / "test-session.request"
        response = tmp_path / "test-session.response"
        request.write_bytes(message())
        service.poll_once()
        clock[0] += 0.09  # Debounce elapsed, but the last character is still held.
        service.poll_once()
        assert not engine.ranks
        assert not response.exists()
        assert request.name in service._pending
        assert request.name not in service._processed
        assert not refresh.notifications

        refresh.ready_target = released_target
        clock[0] += 0.04
        service.poll_once()
        assert len(engine.ranks) == 1
        assert response.read_text().endswith("ok\n3,1,2\n")
        assert request.name not in service._pending
        assert refresh.captures == [refresh.foreground]
        assert refresh.ready_calls == [refresh.foreground, refresh.foreground]
        assert refresh.notifications == [released_target]
        service.poll_once()
        assert len(engine.ranks) == 1
        assert refresh.notifications == [released_target]


def test_rejected_refresh_does_not_repeat_inference_or_notification(tmp_path):
    engine = FakeEngine()
    refresh = FakeRefresh()
    refresh.result = False
    with MailboxService(tmp_path, engine, debounce_interval=0, refresh=refresh) as service:
        (tmp_path / "test-session.request").write_bytes(message())
        service.poll_once()
        service.poll_once()
        assert len(engine.ranks) == 1
        assert refresh.notifications == [refresh.ready_target]
        assert (tmp_path / "test-session.response").read_text().endswith("ok\n3,1,2\n")
