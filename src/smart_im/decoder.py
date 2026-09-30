"""A small, bounded, offline pinyin decoder with an original bundled vocabulary.

This is deliberately independent of ranking and user storage. Frequencies are
hand-set priors, not claims about population usage. Explicit apostrophes constrain
syllable boundaries (``xi'an`` must not decode as ``先``).
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .types import Candidate


class PinyinDecoder:
    MAX_INPUT_LENGTH = 64
    MAX_RESULTS = 100
    _INDEX_LIMIT = 80
    _BEAM_WIDTH = 8

    def __init__(self, lexicon_path: str | Path | None = None) -> None:
        path = Path(lexicon_path) if lexicon_path else Path(__file__).parent / "data/lexicon.tsv"
        self.entries: list[Candidate] = []
        self._syllables: dict[tuple[str, str], tuple[str, ...]] = {}
        self._text_syllables: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        self._reverse: dict[str, str] = {}
        self._exact: dict[str, list[Candidate]] = defaultdict(list)
        self._prefix: dict[str, list[Candidate]] = defaultdict(list)
        self._initials: dict[str, list[Candidate]] = defaultdict(list)
        self._deletions: dict[str, set[str]] = defaultdict(set)
        self._trie: dict = {}
        seen: set[tuple[str, str]] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            text, pronunciation, frequency = line.split("\t")
            syllables = tuple(pronunciation.split())
            pinyin = "".join(syllables)
            if (text, pinyin) in seen:
                continue
            seen.add((text, pinyin))
            candidate = Candidate(
                text=text,
                pinyin=pinyin,
                frequency=float(frequency),
                score=math.log1p(float(frequency)),
            )
            self.entries.append(candidate)
            self._syllables[(text, pinyin)] = syllables
            self._text_syllables[text].append(syllables)
        self.entries.sort(key=lambda item: (-item.frequency, len(item.text), item.text))
        for candidate in self.entries:
            key = candidate.pinyin
            self._reverse.setdefault(candidate.text, key)
            self._exact[key].append(candidate)
            for length in range(1, len(key) + 1):
                bucket = self._prefix[key[:length]]
                if len(bucket) < self._INDEX_LIMIT:
                    bucket.append(candidate)
            initials = "".join(s[0] for s in self._syllables[(candidate.text, key)])
            if len(initials) > 1:
                for length in range(2, len(initials) + 1):
                    bucket = self._initials[initials[:length]]
                    if len(bucket) < self._INDEX_LIMIT:
                        bucket.append(candidate)
            node = self._trie
            for char in key:
                node = node.setdefault(char, {})
            node.setdefault("", []).append(candidate)
            if 4 <= len(key) <= 24:
                for index in range(len(key)):
                    self._deletions[key[:index] + key[index + 1 :]].add(key)

    @staticmethod
    def normalize(raw: str) -> str:
        """Normalize tones and ü/u: to v, retaining explicit word boundaries.

        Invalid characters and oversized buffers produce no composition. Spaces,
        hyphens and typographic apostrophes are accepted as explicit separators.
        Tone-number input (ni3 hao3) is also accepted.
        """
        if not isinstance(raw, str) or len(raw) > PinyinDecoder.MAX_INPUT_LENGTH * 2:
            return ""
        value = raw.lower().replace("u:", "v")
        for char in "üǖǘǚǜ":
            value = value.replace(char, "v")
        value = "".join(
            char for char in unicodedata.normalize("NFD", value) if not unicodedata.combining(char)
        )
        normalized: list[str] = []
        for char in value:
            if "a" <= char <= "z":
                normalized.append(char)
            elif char in "12345":
                continue
            elif char.isspace() or char in "'’ʼ-":
                if normalized and normalized[-1] != "'":
                    normalized.append("'")
            else:
                return ""
        result = "".join(normalized).strip("'")
        return result if len(result.replace("'", "")) <= PinyinDecoder.MAX_INPUT_LENGTH else ""

    def lookup(self, text: str) -> str:
        """Return compact pinyin, including unseen Chinese text via pypinyin."""
        if text in self._reverse:
            return self._reverse[text]
        if not text or not all("\u3400" <= char <= "\u9fff" for char in text):
            return ""
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(text, errors="ignore")).replace("ü", "v")

    def matches(self, text: str, raw: str) -> bool:
        """Whether raw is a complete pronunciation of text, with valid boundaries.

        Used for learned phrases and selected polyphonic readings. Abbreviations,
        unfinished syllables, and spelling corrections are not complete matches.
        Heteronym matching uses bounded offset sets instead of enumerating the
        Cartesian product of all character readings.
        """
        normalized = self.normalize(raw)
        if (
            not normalized
            or not text
            or len(text) > self.MAX_INPUT_LENGTH
            or not all("\u3400" <= char <= "\u9fff" for char in text)
        ):
            return False
        compact = normalized.replace("'", "")
        boundaries: set[int] = set()
        offset = 0
        for part in normalized.split("'")[:-1]:
            offset += len(part)
            boundaries.add(offset)

        for syllables in self._text_syllables.get(text, ()):
            if "".join(syllables) != compact:
                continue
            offset = 0
            ends: set[int] = set()
            for syllable in syllables:
                offset += len(syllable)
                ends.add(offset)
            if boundaries <= ends:
                return True

        from pypinyin import Style, pinyin

        readings = pinyin(text, style=Style.NORMAL, heteronym=True, errors="ignore")
        if len(readings) != len(text):
            return False
        positions = {0}
        for variants in readings:
            following: set[int] = set()
            for start in positions:
                for syllable in variants[:8]:
                    syllable = syllable.replace("ü", "v")
                    end = start + len(syllable)
                    if compact.startswith(syllable, start) and not any(
                        start < point < end for point in boundaries
                    ):
                        following.add(end)
            if not following:
                return False
            positions = following
        return len(compact) in positions

    def _boundaries_fit(
        self, candidate: Candidate, start: int, end: int, boundaries: set[int]
    ) -> bool:
        interior = {point - start for point in boundaries if start < point < end}
        if not interior:
            return True
        syllable_ends: set[int] = set()
        offset = 0
        for syllable in self._syllables[(candidate.text, candidate.pinyin)]:
            offset += len(syllable)
            syllable_ends.add(offset)
        return interior <= syllable_ends

    def _partial_fit(self, candidate: Candidate, normalized: str) -> bool:
        if "'" not in normalized:
            return True
        parts = normalized.split("'")
        # An explicit separator can follow a whole word containing several
        # syllables, so enforce positions rather than requiring one part/syllable.
        offset = 0
        boundaries: set[int] = set()
        for part in parts[:-1]:
            offset += len(part)
            boundaries.add(offset)
        return self._boundaries_fit(candidate, 0, len(candidate.pinyin), boundaries)

    def decode(self, raw: str, limit: int = 30) -> list[Candidate]:
        if limit <= 0:
            return []
        limit = min(limit, self.MAX_RESULTS)
        normalized = self.normalize(raw)
        if not normalized:
            return []
        compact = normalized.replace("'", "")
        boundaries: set[int] = set()
        offset = 0
        for part in normalized.split("'")[:-1]:
            offset += len(part)
            boundaries.add(offset)
        ranked: list[tuple[int, Candidate]] = []
        exact = [
            candidate
            for candidate in self._exact.get(compact, ())
            if self._boundaries_fit(candidate, 0, len(compact), boundaries)
        ]
        ranked.extend((0, candidate) for candidate in exact)

        # Complete a trailing partial syllable or a longer phrase. Exact matches
        # retain a separate priority regardless of dictionary frequency.
        partials: list[Candidate] = []
        for candidate in self._prefix.get(compact, ()):
            if candidate.pinyin == compact or not self._partial_fit(candidate, normalized):
                continue
            completed = replace(
                candidate,
                source="partial",
                annotation="拼音补全",
                score=candidate.score - 0.12 * (len(candidate.pinyin) - len(compact)) - 1.0,
            )
            partials.append(completed)
            ranked.append((2, completed))

        if 2 <= len(compact) <= 12:
            for candidate in self._initials.get(compact, ()):
                syllables = self._syllables[(candidate.text, candidate.pinyin)]
                if "'" in normalized:
                    parts = normalized.split("'")
                    if len(parts) > len(syllables) or not all(
                        syllable.startswith(part) for part, syllable in zip(parts, syllables)
                    ):
                        continue
                ranked.append(
                    (
                        3,
                        replace(
                            candidate,
                            source="initials",
                            annotation="首字母简拼",
                            score=candidate.score - 2.0 - 0.15 * (len(syllables) - len(compact)),
                        ),
                    )
                )

        # Compose only when the buffer can contain more than a single syllable.
        if len(compact) >= 4:
            ranked.extend((1, candidate) for candidate in self._compose(compact, boundaries))

        # Avoid showing corrections while the user is entering a valid prefix.
        if not exact and not partials and not boundaries and 4 <= len(compact) <= 24:
            for candidate in self._spelling(compact):
                ranked.append((4, candidate))

        ranked.sort(key=lambda item: (item[0], -item[1].score, item[1].text))
        result: list[Candidate] = []
        seen: set[str] = set()
        for _, candidate in ranked:
            if candidate.text not in seen:
                result.append(candidate)
                seen.add(candidate.text)
                if len(result) == limit:
                    break
        return result

    def _compose(self, compact: str, boundaries: set[int]) -> list[Candidate]:
        # State: (text, accumulated weighted log-frequency, syllables, chunks).
        states: list[list[tuple[str, float, int, int]]] = [[] for _ in range(len(compact) + 1)]
        states[0] = [("", 0.0, 0, 0)]
        expansions = 0
        for start in range(len(compact)):
            if not states[start]:
                continue
            states[start].sort(key=self._path_score, reverse=True)
            paths = states[start][: self._BEAM_WIDTH]
            node = self._trie
            for end in range(start, min(len(compact), start + 40)):
                node = node.get(compact[end])
                if node is None:
                    break
                matches = [
                    item
                    for item in node.get("", ())
                    if self._boundaries_fit(item, start, end + 1, boundaries)
                ][:3]
                for candidate in matches:
                    syllable_count = len(self._syllables[(candidate.text, candidate.pinyin)])
                    for text, score, count, chunks in paths:
                        states[end + 1].append(
                            (
                                text + candidate.text,
                                score + candidate.score * syllable_count,
                                count + syllable_count,
                                chunks + 1,
                            )
                        )
                        expansions += 1
                if len(states[end + 1]) > self._BEAM_WIDTH * 8:
                    states[end + 1].sort(key=self._path_score, reverse=True)
                    states[end + 1] = states[end + 1][: self._BEAM_WIDTH]
                if expansions > 4096:
                    break
            if expansions > 4096:
                break
        final = sorted(states[-1], key=self._path_score, reverse=True)
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for path in final:
            text, total, count, chunks = path
            if chunks <= 1 or text in seen:
                continue
            seen.add(text)
            candidates.append(
                Candidate(
                    text=text,
                    pinyin=compact,
                    frequency=math.expm1(total / max(count, 1)),
                    source="composed",
                    score=self._path_score(path),
                    annotation="拼音组句",
                )
            )
            if len(candidates) == self._BEAM_WIDTH:
                break
        return candidates

    @staticmethod
    def _path_score(path: tuple[str, float, int, int]) -> float:
        _, total, count, chunks = path
        return total / max(count, 1) - 0.65 * max(chunks - 1, 0)

    def _spelling(self, compact: str) -> list[Candidate]:
        alternatives = set(self._deletions.get(compact, ()))
        for index in range(len(compact)):
            deleted = compact[:index] + compact[index + 1 :]
            if deleted in self._exact:
                alternatives.add(deleted)
            alternatives.update(self._deletions.get(deleted, ()))
        for index in range(len(compact) - 1):
            swapped = compact[:index] + compact[index + 1] + compact[index] + compact[index + 2 :]
            if swapped in self._exact:
                alternatives.add(swapped)
        result = []
        for key in sorted(alternatives):
            if key == compact or not self._one_typo(compact, key):
                continue
            for candidate in self._exact[key][:3]:
                result.append(
                    replace(
                        candidate,
                        source="spelling",
                        score=candidate.score - 3.0,
                        annotation=f"拼写纠正：{compact} → {key}",
                    )
                )
        return result

    @staticmethod
    def _one_typo(raw: str, target: str) -> bool:
        """One substitution, insertion, deletion, or adjacent transposition."""
        if abs(len(raw) - len(target)) > 1:
            return False
        if len(raw) == len(target):
            differences = [i for i, (left, right) in enumerate(zip(raw, target)) if left != right]
            if len(differences) == 1:
                return True
            if len(differences) != 2:
                return False
            left, right = differences
            return right == left + 1 and raw[left] == target[right] and raw[right] == target[left]
        shorter, longer = (raw, target) if len(raw) < len(target) else (target, raw)
        for index, (left, right) in enumerate(zip(shorter, longer)):
            if left != right:
                return shorter[index:] == longer[index + 1 :]
        return True
