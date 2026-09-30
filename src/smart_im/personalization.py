"""Bounded, local-only learning from explicitly committed input.

Only committed phrases and their short context suffix are retained. This is not
a transcript database. Callers must skip both reads and writes in private mode.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from smart_im.types import Candidate


class PersonalStore:
    MAX_CONTEXT = 8
    MAX_PHRASE = 64
    MAX_ROWS = 10_000
    MAX_COUNT = 1_000_000

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._closed = False
        if self.path != ":memory:":
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Do not chmod an existing user-selected directory.
            fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            if os.name != "nt":
                target.chmod(0o600)
            self.path = str(target)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        # DELETE mode avoids additional long-lived WAL copies of personal data.
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS words (
                pinyin TEXT NOT NULL, text TEXT NOT NULL,
                count INTEGER NOT NULL, last_used REAL NOT NULL,
                PRIMARY KEY (pinyin, text));
            CREATE TABLE IF NOT EXISTS transitions (
                context TEXT NOT NULL, text TEXT NOT NULL,
                count INTEGER NOT NULL, last_used REAL NOT NULL,
                PRIMARY KEY (context, text));
            CREATE INDEX IF NOT EXISTS words_pinyin ON words(pinyin);
            CREATE INDEX IF NOT EXISTS transitions_context ON transitions(context);
        """)
        self._db.commit()

    @staticmethod
    def _key(pinyin: str) -> str:
        return re.sub(r"[\s']", "", pinyin.lower().replace("ü", "v").replace("u:", "v"))

    @classmethod
    def _context(cls, context: str) -> str:
        return context[-cls.MAX_CONTEXT :]

    def record(self, pinyin: str, text: str, context: str = "") -> None:
        """Record a selection, never an uncommitted suggestion or full document."""
        key = self._key(pinyin)
        if not text.strip() or len(text) > self.MAX_PHRASE or len(key) > 256:
            return
        now = time.time()
        with self._lock, self._db:
            if key:
                self._db.execute(
                    """
                    INSERT INTO words VALUES (?, ?, 1, ?)
                    ON CONFLICT(pinyin, text) DO UPDATE SET
                        count=MIN(count + 1, ?), last_used=excluded.last_used
                """,
                    (key, text, now, self.MAX_COUNT),
                )
            if context:
                self._db.execute(
                    """
                    INSERT INTO transitions VALUES (?, ?, 1, ?)
                    ON CONFLICT(context, text) DO UPDATE SET
                        count=MIN(count + 1, ?), last_used=excluded.last_used
                """,
                    (self._context(context), text, now, self.MAX_COUNT),
                )
            # Bound storage even for a long-running desktop process. Evict the
            # least recently used entries, retaining recent changes of style.
            for table in ("words", "transitions"):
                self._db.execute(
                    f"""
                    DELETE FROM {table} WHERE rowid IN (
                        SELECT rowid FROM {table} ORDER BY last_used DESC, rowid DESC
                        LIMIT -1 OFFSET ?)
                """,
                    (self.MAX_ROWS,),
                )

    def frequency(self, pinyin: str, text: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT count FROM words WHERE pinyin=? AND text=?", (self._key(pinyin), text)
            ).fetchone()
        return int(row[0]) if row else 0

    def candidates(self, pinyin: str, limit: int = 20) -> list[Candidate]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._db.execute(
                """
                SELECT text, count FROM words WHERE pinyin=?
                ORDER BY count DESC, last_used DESC, text LIMIT ?
            """,
                (self._key(pinyin), min(limit, 100)),
            ).fetchall()
        return [
            Candidate(text, self._key(pinyin), count, "personal", annotation="个人词频")
            for text, count in rows
        ]

    def predict(self, context: str, limit: int = 5) -> list[Candidate]:
        if not context or limit <= 0:
            return []
        suffix = self._context(context)
        with self._lock:
            rows = self._db.execute(
                """
                SELECT text, count FROM transitions WHERE context=?
                ORDER BY count DESC, last_used DESC, text LIMIT ?
            """,
                (suffix, min(limit, 100)),
            ).fetchall()
            if not rows and len(suffix) > 1:
                # Equality on a bounded suffix, not LIKE: literal '%' and '_'
                # in user text must not become query wildcards.
                short = suffix[-2:]
                rows = self._db.execute(
                    """
                    SELECT text, SUM(count) AS total FROM transitions
                    WHERE substr(context, -2)=? GROUP BY text
                    ORDER BY total DESC, text LIMIT ?
                """,
                    (short, min(limit, 100)),
                ).fetchall()
        return [
            Candidate(
                text=text,
                frequency=count,
                source="personal",
                score=float(count),
                annotation="个人表达习惯",
            )
            for text, count in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            words, commits = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(count), 0) FROM words"
            ).fetchone()
            transitions = self._db.execute("SELECT COUNT(*) FROM transitions").fetchone()[0]
        return {"phrases": words, "selections": commits, "contexts": transitions}

    def clear(self) -> None:
        """Remove all retained evidence and compact the SQLite file."""
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM words")
                self._db.execute("DELETE FROM transitions")
            self._db.execute("VACUUM")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self) -> PersonalStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
