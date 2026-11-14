"""SQLite layer: connections, schema, and typed reads of passages and characters.

Offsets everywhere are Unicode code points into ``passages.text``, which is how
Python indexes ``str`` and how SQLite's ``length()`` counts text.
"""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with foreign keys on and explicit transaction control."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets FastAPI read while the import worker writes. No-op for :memory:.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[None]:
    """A short write transaction: takes the write lock up front, rolls back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@dataclass(frozen=True)
class Passage:
    id: int
    book_id: int
    paragraph_pos: int
    href: str
    para_no: int
    first_sentence: int
    sentence_ends: tuple[int, ...]
    text: str

    @property
    def last_sentence(self) -> int:
        return self.first_sentence + len(self.sentence_ends) - 1

    def sentence_at(self, hi: int) -> int:
        """Book-wide sentence the reader must finish to have read ``text[:hi]``."""
        if not 0 < hi <= len(self.text):
            raise ValueError(f"offset {hi} is outside paragraph {self.paragraph_pos}")
        return self.first_sentence + bisect_left(self.sentence_ends, hi)


@dataclass(frozen=True)
class Character:
    id: int
    book_id: int
    display_name: str
    first_seen: int
    aliases: list
    voice_snapshots: list


def check_passage(first_sentence: int, sentence_ends: Sequence[int], text: str) -> None:
    """Raise ValueError unless the sentence boundaries exactly tile the paragraph."""
    if first_sentence < 1:
        raise ValueError("first_sentence is numbered from 1")
    if not sentence_ends:
        raise ValueError("a paragraph needs at least one sentence")
    previous = 0
    for end in sentence_ends:
        if isinstance(end, bool) or not isinstance(end, int) or end <= previous:
            raise ValueError("sentence_ends must be strictly increasing positive integers")
        previous = end
    if previous != len(text):
        raise ValueError(
            f"last sentence end {previous} must equal the paragraph length {len(text)}"
        )


def create_book(conn: sqlite3.Connection, title: str, epub_hash: str, version: str) -> int:
    """Return the id for this (epub_hash, version) import, creating it if needed."""
    with write_tx(conn):
        row = conn.execute(
            "SELECT id FROM books WHERE epub_hash=? AND version=?", (epub_hash, version)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO books (title, epub_hash, version, status) VALUES (?,?,?,'pending')",
            (title, epub_hash, version),
        )
        return cur.lastrowid


def insert_passage(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    paragraph_pos: int,
    href: str,
    para_no: int,
    first_sentence: int,
    sentence_ends: Sequence[int],
    text: str,
    id: int | None = None,
) -> int:
    check_passage(first_sentence, sentence_ends, text)
    cur = conn.execute(
        "INSERT INTO passages (id, book_id, paragraph_pos, href, para_no,"
        " first_sentence, sentence_ends, text) VALUES (?,?,?,?,?,?,?,?)",
        (id, book_id, paragraph_pos, href, para_no, first_sentence,
         json.dumps(list(sentence_ends)), text),
    )
    return cur.lastrowid


def load_passages(
    conn: sqlite3.Connection, book_id: int, through: int | None = None
) -> dict[int, Passage]:
    """Passages keyed by paragraph_pos, optionally only those starting by ``through``."""
    sql = "SELECT * FROM passages WHERE book_id=?"
    args: list = [book_id]
    if through is not None:
        sql += " AND first_sentence<=?"
        args.append(through)
    out = {}
    for row in conn.execute(sql + " ORDER BY paragraph_pos", args):
        out[row["paragraph_pos"]] = Passage(
            id=row["id"],
            book_id=row["book_id"],
            paragraph_pos=row["paragraph_pos"],
            href=row["href"],
            para_no=row["para_no"],
            first_sentence=row["first_sentence"],
            sentence_ends=tuple(json.loads(row["sentence_ends"])),
            text=row["text"],
        )
    return out


def load_characters(conn: sqlite3.Connection, book_id: int) -> list[Character]:
    return [
        Character(
            id=row["id"],
            book_id=row["book_id"],
            display_name=row["display_name"],
            first_seen=row["first_seen"],
            aliases=json.loads(row["aliases"]),
            voice_snapshots=json.loads(row["voice_snapshots"]),
        )
        for row in conn.execute(
            "SELECT * FROM characters WHERE book_id=? ORDER BY id", (book_id,)
        )
    ]


def quote_text(conn: sqlite3.Connection, book_id: int, sources: Sequence[dict]) -> str:
    """Rebuild a spoken turn from its quote ranges, in array order."""
    parts = []
    for src in sources:
        if src.get("role") != "quote":
            continue
        row = conn.execute(
            "SELECT text FROM passages WHERE id=? AND book_id=?",
            (src["passage_id"], book_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"passage {src['passage_id']} is not in book {book_id}")
        parts.append(row["text"][src["lo"]:src["hi"]])
    return " ".join(parts)
