"""Model contract for reranking Rime candidates."""

from typing import Protocol


class CandidateReranker(Protocol):
    """A batch model returns indices rather than inventing candidate text."""

    name: str

    def rerank(self, context: str, texts: list[str], pinyin: str = "") -> list[int]: ...
