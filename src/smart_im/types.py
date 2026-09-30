"""Language-model contract for Rime candidate scoring."""

from typing import Protocol, runtime_checkable


class LanguageModel(Protocol):
    name: str

    def score(self, context: str, text: str) -> float:
        """Return a length-normalized log likelihood (higher is better)."""
        ...


@runtime_checkable
class CandidateReranker(Protocol):
    """A batch model returns indices rather than inventing candidate text."""

    name: str

    def rerank(self, context: str, texts: list[str], pinyin: str = "") -> list[int]: ...
