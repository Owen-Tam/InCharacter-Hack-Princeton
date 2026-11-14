"""Commit one resolved chunk and its checkpoint in a single short transaction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .db import write_tx
from .resolve import ResolvedChunk

_ROLES = {"quote", "attribution", "support"}


class CheckpointMismatch(RuntimeError):
    """The book's checkpoint is not where this chunk expected to start."""


@dataclass
class CommitResult:
    committed: bool  # False when the chunk was already committed and got skipped
    indexed_through: int
    character_ids: dict[str, int] = field(default_factory=dict)  # new key -> characters.id
    utterance_ids: list[int] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)


def _check_sources(conn: sqlite3.Connection, book_id: int, sources: list) -> None:
    """Source references live inside JSON, so the database cannot check them for us."""
    for src in sources:
        lo, hi = src.get("lo"), src.get("hi")
        if src.get("role") not in _ROLES:
            raise ValueError(f"unknown source role {src.get('role')!r}")
        row = conn.execute(
            "SELECT length(text) AS n FROM passages WHERE id=? AND book_id=?",
            (src.get("passage_id"), book_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"passage {src.get('passage_id')!r} is not in book {book_id}")
        if not (isinstance(lo, int) and isinstance(hi, int) and 0 <= lo < hi <= row["n"]):
            raise ValueError(f"range [{lo}, {hi}) is outside passage {src['passage_id']}")


def commit_chunk(
    conn: sqlite3.Connection, book_id: int, chunk: ResolvedChunk, *, final: bool = False
) -> CommitResult:
    """Write the chunk's rows and advance ``indexed_through`` together.

    Safe to retry: a chunk whose window is already behind the checkpoint is
    skipped. Any failure rolls back every row and leaves the checkpoint alone.
    Pass ``final=True`` on the last chunk to mark the book ready.
    """
    with write_tx(conn):
        row = conn.execute(
            "SELECT indexed_through FROM books WHERE id=?", (book_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no book with id {book_id}")
        checkpoint = row["indexed_through"]
        if checkpoint >= chunk.through:
            return CommitResult(False, checkpoint)
        if checkpoint != chunk.start:
            raise CheckpointMismatch(
                f"book {book_id} is indexed through {checkpoint},"
                f" but this chunk starts after {chunk.start}"
            )

        result = CommitResult(True, chunk.through)

        for new in chunk.characters:
            cur = conn.execute(
                "INSERT INTO characters (book_id, display_name, first_seen) VALUES (?,?,?)",
                (book_id, new.display_name, new.first_seen),
            )
            result.character_ids[new.key] = cur.lastrowid

        def cid(ref):
            return result.character_ids[ref] if isinstance(ref, str) else ref

        for row_ in chunk.aliases:
            _check_sources(conn, book_id, row_.alias["sources"])
            current = conn.execute(
                "SELECT aliases FROM characters WHERE id=? AND book_id=?",
                (cid(row_.character), book_id),
            ).fetchone()
            if current is None:
                raise ValueError(f"character {row_.character!r} is not in book {book_id}")
            aliases = json.loads(current["aliases"])
            if any(a["name"] == row_.alias["name"] for a in aliases):
                continue
            aliases.append(row_.alias)
            aliases.sort(key=lambda a: a["available_from"])
            conn.execute(
                "UPDATE characters SET aliases=? WHERE id=?",
                (json.dumps(aliases, ensure_ascii=False), cid(row_.character)),
            )

        for u in chunk.utterances:
            _check_sources(conn, book_id, u.sources)
            cur = conn.execute(
                "INSERT INTO utterances (book_id, speaker_id, addressee_id,"
                " available_from, sources) VALUES (?,?,?,?,?)",
                (book_id, cid(u.speaker), cid(u.addressee), u.available_from,
                 json.dumps(u.sources)),
            )
            result.utterance_ids.append(cur.lastrowid)

        for e in chunk.evidence:
            _check_sources(conn, book_id, e.sources)
            cur = conn.execute(
                "INSERT INTO evidence (book_id, character_id, claim, basis,"
                " reported_by_character_id, available_from, sources) VALUES (?,?,?,?,?,?,?)",
                (book_id, cid(e.character), e.claim, e.basis, cid(e.reported_by),
                 e.available_from, json.dumps(e.sources)),
            )
            result.evidence_ids.append(cur.lastrowid)

        conn.execute(
            "UPDATE books SET indexed_through=?, status=? WHERE id=?",
            (chunk.through, "ready" if final else "indexing", book_id),
        )
        return result
