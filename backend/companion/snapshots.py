"""Publish a voice snapshot once the dialogue it cites is committed.

Writing the snapshot's traits is the model's job. This module only checks that
every cited turn is really this character's and readable by the snapshot's
position, then stores it.

Snapshot format::

    {"through_position": 3,
     "traits": [{"pattern": "Replies in a short fragment.",
                 "supporting_utterance_ids": [21]}],
     "recurring_phrases": [{"phrase": "Empty again",
                            "supporting_utterance_ids": [21, 40]}]}
"""

from __future__ import annotations

import json
import sqlite3
from typing import Mapping

from .db import quote_text, write_tx
from .resolve import fold


class SnapshotError(ValueError):
    pass


def _ids(entry: Mapping, minimum: int, what: str) -> list[int]:
    ids = entry.get("supporting_utterance_ids")
    if not isinstance(ids, list) or any(
        isinstance(i, bool) or not isinstance(i, int) for i in ids
    ):
        raise SnapshotError(f"{what}: supporting_utterance_ids must be a list of ids")
    ids = list(dict.fromkeys(ids))
    if len(ids) < minimum:
        raise SnapshotError(f"{what}: needs at least {minimum} distinct supporting turn(s)")
    return ids


def _label(entry, name: str) -> str:
    value = entry.get(name) if isinstance(entry, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(f"{name} must be a non-empty string")
    return value.strip()


def publish_snapshot(
    conn: sqlite3.Connection, book_id: int, character_id: int, snapshot: Mapping
) -> dict:
    """Validate and store one snapshot. Republishing a position replaces it."""
    if not isinstance(snapshot, Mapping):
        raise SnapshotError("snapshot must be an object")
    through = snapshot.get("through_position")
    if isinstance(through, bool) or not isinstance(through, int) or through < 1:
        raise SnapshotError("through_position must be a sentence number")
    traits = snapshot.get("traits")
    phrases = snapshot.get("recurring_phrases")
    traits = [] if traits is None else traits
    phrases = [] if phrases is None else phrases
    if not isinstance(traits, list) or not isinstance(phrases, list):
        raise SnapshotError("traits and recurring_phrases must be lists")

    with write_tx(conn):
        book = conn.execute(
            "SELECT indexed_through FROM books WHERE id=?", (book_id,)
        ).fetchone()
        if book is None:
            raise SnapshotError(f"no book with id {book_id}")
        if through > book["indexed_through"]:
            raise SnapshotError(
                f"through_position {through} is past the committed prefix"
                f" ({book['indexed_through']})"
            )
        character = conn.execute(
            "SELECT first_seen, voice_snapshots FROM characters WHERE id=? AND book_id=?",
            (character_id, book_id),
        ).fetchone()
        if character is None:
            raise SnapshotError(f"character {character_id} is not in book {book_id}")
        if character["first_seen"] > through:
            raise SnapshotError("the character is not introduced by through_position")

        def turn(utterance_id: int, what: str) -> str:
            row = conn.execute(
                "SELECT speaker_id, available_from, sources FROM utterances"
                " WHERE id=? AND book_id=?", (utterance_id, book_id),
            ).fetchone()
            if row is None:
                raise SnapshotError(f"{what}: utterance {utterance_id} is not in this book")
            if row["speaker_id"] != character_id:
                raise SnapshotError(
                    f"{what}: utterance {utterance_id} was not spoken by this character"
                )
            if row["available_from"] > through:
                raise SnapshotError(
                    f"{what}: utterance {utterance_id} is not readable by sentence {through}"
                )
            return quote_text(conn, book_id, json.loads(row["sources"]))

        clean = {"through_position": through, "traits": [], "recurring_phrases": []}
        for entry in traits:
            pattern = _label(entry, "pattern")
            ids = _ids(entry, 1, f"trait {pattern!r}")
            for utterance_id in ids:
                turn(utterance_id, f"trait {pattern!r}")
            clean["traits"].append({"pattern": pattern, "supporting_utterance_ids": ids})
        for entry in phrases:
            phrase = _label(entry, "phrase")
            what = f"phrase {phrase!r}"
            ids = _ids(entry, 2, what)  # a catchphrase must recur across distinct turns
            needle = fold(phrase).casefold()
            for utterance_id in ids:
                if needle not in fold(turn(utterance_id, what)).casefold():
                    raise SnapshotError(
                        f"{what}: not found in utterance {utterance_id}"
                    )
            clean["recurring_phrases"].append(
                {"phrase": phrase, "supporting_utterance_ids": ids}
            )

        stored = [
            s for s in json.loads(character["voice_snapshots"])
            if s["through_position"] != through
        ]
        stored.append(clean)
        stored.sort(key=lambda s: s["through_position"])
        conn.execute(
            "UPDATE characters SET voice_snapshots=? WHERE id=?",
            (json.dumps(stored, ensure_ascii=False), character_id),
        )
        return clean
