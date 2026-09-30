"""Language-model contract for Rime candidate scoring."""

from typing import Protocol


class LanguageModel(Protocol):
    name: str

    def score(self, context: str, text: str) -> float:
        """Return a length-normalized log likelihood (higher is better)."""
        ...
