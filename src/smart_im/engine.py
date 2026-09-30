"""Platform-independent orchestration. No network, UI, or global keyboard access."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from .correction import CorrectionEngine
from .decoder import PinyinDecoder
from .models import LocalLanguageModel
from .personalization import PersonalStore
from .types import Candidate, Correction, LanguageModel, SuggestionResult


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SmartIM"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "smart-im"


class Engine:
    """One engine per worker; serialized methods also support CLI/service callers.

    ``learning=False`` and ``private=True`` both exclude all personal reads/writes.
    Merely typing never records a selection; only explicit ``commit`` calls learn.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        model: LanguageModel | None = None,
        learning: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.decoder = PinyinDecoder()
        self.model = model if model is not None else LocalLanguageModel()
        self.corrections = CorrectionEngine()
        self.learning = learning
        self._store: PersonalStore | None = None
        self._lock = threading.RLock()
        self._model_error = ""

    def _personal(self) -> PersonalStore:
        if self._store is None:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._store = PersonalStore(self.data_dir / "learning.sqlite3")
        return self._store

    @staticmethod
    def _limit(limit: int) -> int:
        return max(1, min(int(limit), 20))

    def _score(self, context: str, text: str) -> float:
        try:
            score = float(self.model.score(context, text))
            return score if math.isfinite(score) else -12.0
        except Exception as exc:
            # An optional model failure must leave basic input available.
            self._model_error = type(exc).__name__
            return -12.0

    def suggest(
        self, pinyin: str, context: str = "", limit: int = 9, private: bool = False
    ) -> SuggestionResult:
        started = time.perf_counter()
        with self._lock:
            original_context = context
            context = context[-128:]
            limit = self._limit(limit)
            normalized = self.decoder.normalize(pinyin)
            # Refuse an overlong composition instead of silently committing a prefix.
            if len(pinyin) > 96:
                return SuggestionResult(model_name=self.model.name)
            pool = self.decoder.decode(pinyin, limit=40) if normalized else []
            store = self._personal() if self.learning and not private else None
            key = normalized.replace("'", "")
            if store is not None and key:
                pool.extend(
                    candidate
                    for candidate in store.candidates(key, limit=20)
                    if self.decoder.matches(candidate.text, normalized)
                )
            best: dict[str, Candidate] = {}
            for candidate in pool:
                personal_count = store.frequency(key, candidate.text) if store else 0
                # A tiny vocabulary must not overwhelm lexical evidence when
                # an ordinary word is outside its training corpus.
                language_score = max(-6.0, min(0.0, self._score(context, candidate.text)))
                # Frequency is log-scaled, so repeated intentional selections can
                # beat a common dictionary homophone without unbounded score growth.
                source_penalty = {
                    "spelling": 3.0,
                    "initials": 1.0,
                    "partial": 1.2,
                    "completion": 1.2,
                    "composed": 0.3,
                }.get(candidate.source, 0.0)
                lexical_score = (
                    math.log1p(max(0.0, candidate.frequency))
                    if candidate.source == "personal"
                    else candidate.score
                )
                score = (
                    0.60 * lexical_score
                    + 0.85 * language_score
                    + 2.8 * math.log1p(personal_count)
                    - source_penalty
                )
                annotation = candidate.annotation
                if personal_count:
                    annotation = " · ".join(filter(None, [annotation, "个人习惯"]))
                ranked = replace(candidate, score=round(score, 6), annotation=annotation)
                if candidate.text not in best or ranked.score > best[candidate.text].score:
                    best[candidate.text] = ranked
            # An unfinished phrase must not displace a complete dictionary match.
            ranked_candidates = sorted(
                best.values(),
                key=lambda c: (c.source not in {"dictionary", "personal"}, -c.score, c.text),
            )[:limit]
            predictions = self.predict(context, min(limit, 5), private) if not normalized else []
            return SuggestionResult(
                candidates=ranked_candidates,
                predictions=predictions,
                corrections=self.correct(original_context),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                model_name=self.model.name + ("（模型错误，已降级）" if self._model_error else ""),
            )

    def predict(self, context: str, limit: int = 5, private: bool = False) -> list[Candidate]:
        with self._lock:
            if not context.strip():
                return []
            context = context[-128:]
            limit = self._limit(limit)
            try:
                proposals = self.model.predict(context, limit=max(limit, 8))
            except Exception as exc:
                self._model_error = type(exc).__name__
                proposals = []
            personal: list[Candidate] = []
            if self.learning and not private:
                personal = self._personal().predict(context, limit=limit)
            best: dict[str, Candidate] = {}
            for candidate in [*proposals, *personal]:
                if not candidate.text or len(candidate.text) > 40:
                    continue
                if candidate in personal:
                    score = max(-6.0, min(0.0, self._score(context, candidate.text)))
                    score += 2.8 * math.log1p(candidate.frequency)
                else:
                    # Retain proposal evidence (e.g. longest suffix match), not
                    # just likelihood, which favors generic continuations.
                    score = candidate.score
                if not math.isfinite(score):
                    continue
                ranked = replace(candidate, score=score)
                if candidate.text not in best or ranked.score > best[candidate.text].score:
                    best[candidate.text] = ranked
            return sorted(best.values(), key=lambda c: (-c.score, c.text))[:limit]

    def commit(self, pinyin: str, text: str, context: str = "", private: bool = False) -> None:
        if not self.learning or private or not text or len(text) > 64:
            return
        with self._lock:
            # Punctuation can confirm a candidate and append a separator in one
            # action. Learn the accepted word, not a non-decodable pinyin+comma key.
            text = text.rstrip("，。！？；：、,.!?;: \n\t")
            if not text:
                return
            raw = self.decoder.normalize(pinyin)
            key = (
                raw.replace("'", "")
                if raw and self.decoder.matches(text, raw)
                else self.decoder.lookup(text)
            )
            if key and len(key) <= 96:
                self._personal().record(key, text, context[-8:])

    def correct(self, text: str) -> list[Correction]:
        # Corrections are indexed against the supplied string, never its last N chars.
        return self.corrections.suggest(text)

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
