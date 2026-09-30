"""Stable contracts shared by the decoder, models and platform adapters."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    text: str
    pinyin: str = ""
    frequency: float = 1.0
    source: str = "dictionary"
    score: float = 0.0
    annotation: str = ""


@dataclass(frozen=True)
class Correction:
    original: str
    replacement: str
    start: int
    end: int
    reason: str
    confidence: float


@dataclass(frozen=True)
class SuggestionResult:
    candidates: list[Candidate] = field(default_factory=list)
    predictions: list[Candidate] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    elapsed_ms: float = 0.0
    model_name: str = ""


class LanguageModel(Protocol):
    name: str

    def score(self, context: str, text: str) -> float:
        """Return a length-normalized log likelihood (higher is better)."""
        ...

    def predict(self, context: str, limit: int = 5) -> list[Candidate]:
        """Return continuations only, without repeating the context."""
        ...
