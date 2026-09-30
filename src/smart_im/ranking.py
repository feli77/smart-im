"""Small ranking helpers for candidate pools supplied by an external input engine."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping

from .types import CandidateReranker


def external_pinyin_key(raw: str) -> str:
    """Canonicalize a Rime spelling without guessing a candidate's pronunciation.

    Accept letters, tone marks/numbers and common syllable separators. Invalid
    spellings are not learned; candidate text itself is always retained exactly.
    This also supports Rime abbreviations without maintaining a separate lexicon.
    """
    if not isinstance(raw, str) or len(raw) > 96:
        return ""
    value = raw.lower().replace("u:", "v")
    for char in "üǖǘǚǜ":
        value = value.replace(char, "v")
    value = unicodedata.normalize("NFD", value)
    result: list[str] = []
    for char in value:
        if "a" <= char <= "z":
            result.append(char)
        elif unicodedata.combining(char) or char.isspace() or char in "12345'’ʼ-":
            continue
        else:
            return ""
    return "".join(result)


def rank_external(
    texts: list[str],
    context: str,
    model: CandidateReranker,
    word_counts: Mapping[str, int],
    context_counts: Mapping[str, float],
    pinyin: str = "",
) -> list[int]:
    """Combine a model's candidate order with opt-in personal statistics.

    Without context, Rime's original order supplies the prior and no model is
    called. Duplicate texts remain separate indices. Model errors propagate so the
    caller can discard the entire attempted reranking, including personal boosts.
    """
    scores: list[float] = []
    positions = list(range(len(texts)))
    if context.strip():
        order = model.rerank(context, list(texts), pinyin)
        if (
            not isinstance(order, list)
            or any(type(index) is not int for index in order)
            or sorted(order) != list(range(len(texts)))
        ):
            raise ValueError("Invalid candidate permutation")
        for position, index in enumerate(order):
            positions[index] = position
    for index, text in enumerate(texts):
        score = -0.35 * positions[index]
        score += min(8.0, 2.8 * math.log1p(word_counts.get(text, 0)))
        score += min(4.0, 1.4 * math.log1p(context_counts.get(text, 0)))
        scores.append(score)
    # Python's sort is stable: ties retain the external engine's order.
    return sorted(range(len(texts)), key=lambda index: -scores[index])
