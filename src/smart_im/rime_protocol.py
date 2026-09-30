"""Small, bounded file protocol shared with the Rime Lua adapter.

Text fields are UTF-8 encoded as hex so tabs and line breaks cannot become
protocol delimiters. This is transport framing, not encryption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_BYTES = 16 * 1024
MAX_PINYIN = 96
MAX_CONTEXT = 128
MAX_TEXT = 64
MAX_CANDIDATES = 9
MAX_SEQUENCE = 2**53 - 1  # Exactly representable by Lua's number type.
SESSION_PATTERN = r"[a-zA-Z0-9_-]{1,64}"


@dataclass(frozen=True)
class RankRequest:
    session: str
    revision: int
    learning: bool
    pinyin: str
    context: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class CommitEvent:
    session: str
    sequence: int
    learning: bool
    pinyin: str
    context: str
    text: str


def _lines(data: bytes) -> list[str]:
    if len(data) > MAX_BYTES or not data.endswith(b"\n"):
        raise ValueError("invalid message size or framing")
    try:
        text = data.decode("ascii").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid message encoding") from exc
    if "\r" in text:
        raise ValueError("invalid line endings")
    return text.split("\n")[:-1]


def _header(line: str, operation: str) -> tuple[str, int, bool]:
    fields = line.split("\t")
    if len(fields) != 5 or fields[:2] != ["SMARTIM1", operation]:
        raise ValueError("invalid header")
    session, sequence, learning = fields[2:]
    if not re.fullmatch(SESSION_PATTERN, session):
        raise ValueError("invalid session")
    if not re.fullmatch(r"[1-9][0-9]{0,15}", sequence):
        raise ValueError("invalid sequence")
    if int(sequence) > MAX_SEQUENCE or learning not in {"0", "1"}:
        raise ValueError("invalid sequence or learning flag")
    return session, int(sequence), learning == "1"


def _text(value: str, limit: int, *, required: bool = False) -> str:
    if len(value) > limit * 8 or not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("invalid text encoding")
    try:
        decoded = bytes.fromhex(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid text encoding") from exc
    if len(decoded) > limit or (required and not decoded):
        raise ValueError("invalid text length")
    return decoded


def parse_rank_request(data: bytes) -> RankRequest:
    lines = _lines(data)
    if not 4 <= len(lines) <= 3 + MAX_CANDIDATES:
        raise ValueError("invalid candidate count")
    session, revision, learning = _header(lines[0], "RANK")
    return RankRequest(
        session,
        revision,
        learning,
        _text(lines[1], MAX_PINYIN),
        _text(lines[2], MAX_CONTEXT),
        tuple(_text(line, MAX_TEXT, required=True) for line in lines[3:]),
    )


def parse_commit_event(data: bytes) -> CommitEvent:
    lines = _lines(data)
    if len(lines) != 4:
        raise ValueError("invalid commit fields")
    session, sequence, learning = _header(lines[0], "COMMIT")
    return CommitEvent(
        session,
        sequence,
        learning,
        _text(lines[1], MAX_PINYIN),
        _text(lines[2], MAX_CONTEXT),
        _text(lines[3], MAX_TEXT, required=True),
    )


def valid_permutation(order: object, count: int) -> bool:
    return (
        isinstance(order, (list, tuple))
        and len(order) == count
        and all(type(index) is int for index in order)
        and sorted(order) == list(range(count))
    )


def render_response(request: RankRequest, order: list[int], *, fallback: bool = False) -> bytes:
    if not valid_permutation(order, len(request.candidates)):
        raise ValueError("invalid permutation")
    status = "fallback" if fallback else "ok"
    header = f"SMARTIM1\tRESULT\t{request.session}\t{request.revision}\t{status}\n"
    return (header + ",".join(str(index + 1) for index in order) + "\n").encode("ascii")
