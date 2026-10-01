"""Local candidate reranking and opt-in learning for Rime."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .ollama_model import OllamaReranker
from .personalization import PersonalStore
from .ranking import external_pinyin_key, rank_external
from .types import CandidateReranker


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SmartIM"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "smart-im"


class Engine:
    """One engine per worker; serialized methods also support CLI/service callers.

    ``learning=False`` and ``private=True`` both exclude all personal reads/writes.
    Merely typing never records a selection; only explicit ``commit_external`` calls learn.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        model: CandidateReranker | None = None,
        learning: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.model = model if model is not None else OllamaReranker()
        self.learning = learning
        self._store: PersonalStore | None = None
        self._lock = threading.RLock()
        self._model_error = ""

    @property
    def model_error(self) -> str:
        """Error type from the last model operation, without input or exception text."""
        with self._lock:
            return self._model_error

    def rerank(
        self,
        texts: list[str],
        context: str = "",
        pinyin: str = "",
        private: bool = False,
        context_after: str = "",
    ) -> list[int]:
        """Return only indices into an external engine's original candidate pool.

        At most 32 candidates of 1–64 characters and 96 raw pinyin characters
        are accepted; invalid inputs raise ValueError. Context uses its last 128
        characters before and first 128 after the insertion point. Duplicate texts
        remain separate indices. This method neither decodes pinyin nor generates
        candidates, and never records a selection.
        Model failures restore the whole original order, without partial boosts.
        """
        if (
            not isinstance(texts, list)
            or len(texts) > 32
            or any(not isinstance(text, str) or not 1 <= len(text) <= 64 for text in texts)
            or not isinstance(context, str)
            or not isinstance(context_after, str)
            or not isinstance(pinyin, str)
            or len(pinyin) > 96
        ):
            raise ValueError("Invalid external candidate pool, context, or pinyin")
        original = list(range(len(texts)))
        with self._lock:
            self._model_error = ""
            if len(texts) < 2:
                return original
            context = context[-128:]
            context_after = context_after[:128]
            word_counts: dict[str, int] = {}
            context_counts: dict[str, float] = {}
            if self.learning and not private:
                store = self._personal()
                key = external_pinyin_key(pinyin)
                if key:
                    word_counts = {text: store.frequency(key, text) for text in texts}
                if context.strip():
                    context_counts = store.context_counts(context)
            try:
                result = rank_external(
                    texts, context, self.model, word_counts, context_counts, pinyin, context_after
                )
            except Exception as exc:
                self._model_error = type(exc).__name__
                return original
            self._model_error = ""
            return result

    def commit_external(
        self, pinyin: str, text: str, context: str = "", private: bool = False
    ) -> None:
        """Learn an explicitly selected external candidate under its raw spelling.

        Rime owns pronunciation and candidate construction. Preserve its exact
        text, including mixed Chinese/Latin text and punctuation, so subsequent
        reranking queries use the same key. Invalid/empty spellings are ignored.
        """
        if (
            not self.learning
            or private
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 64
            or not isinstance(context, str)
        ):
            return
        key = external_pinyin_key(pinyin)
        if key:
            with self._lock:
                self._personal().record(key, text, context[-8:])

    def _personal(self) -> PersonalStore:
        if self._store is None:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._store = PersonalStore(self.data_dir / "learning.sqlite3")
        return self._store

    def stats(self) -> dict:
        with self._lock:
            if self._store is None and (self.data_dir / "learning.sqlite3").exists():
                self._personal()
            values = (
                self._store.stats()
                if self._store
                else {"selections": 0, "phrases": 0, "contexts": 0}
            )
            return {
                **values,
                "learning": self.learning,
                "model": self.model.name,
                "model_error": self._model_error or None,
                "data_dir": str(self.data_dir),
            }

    def clear_learning(self) -> None:
        with self._lock:
            if self._store is not None or (self.data_dir / "learning.sqlite3").exists():
                self._personal().clear()

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
