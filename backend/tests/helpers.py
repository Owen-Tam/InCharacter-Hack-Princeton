"""Shared test data: the guide's four-sentence fixture and its retrieval SQL."""

import copy

from companion import db
from companion.resolve import resolve_chunk

FIXTURE_TEXT = (
    "Theo stepped outside. Mara hid the letter in the atlas. Theo returned and "
    "said, “Empty again.” Mara showed him the letter inside the atlas."
)
FIXTURE_ENDS = [21, 55, 94, 139]
PASSAGE_ID = 11

_CANDIDATES = {
    "characters": [
        {"key": "theo", "name": "Theo",
         "first_mention": {"para": 1, "text": "Theo stepped outside"}},
        {"key": "mara", "name": "Mara",
         "first_mention": {"para": 1, "text": "Mara hid"}},
    ],
    "utterances": [
        {"speaker": "theo", "addressee": None,
         "quotes": [{"para": 1, "text": "Empty again."}],
         "attribution": [{"para": 1, "text": "Theo returned and said"}]},
    ],
    "evidence": [
        {"character": "mara", "claim": "Mara hid the letter in the atlas.",
         "basis": "performed_action", "reported_by": None,
         "support": [{"para": 1, "text": "Mara hid the letter in the atlas."}]},
        {"character": "theo", "claim": "Theo said it was empty again.",
         "basis": "said", "reported_by": None,
         "support": [{"para": 1, "text": "Theo returned and said, “Empty again.”"}]},
        {"character": "theo", "claim": "Theo was shown the letter inside the atlas.",
         "basis": "observed", "reported_by": None,
         "support": [
             {"para": 1, "text": "Mara showed him the letter inside the atlas."},
             {"para": 1, "text": "Theo returned"},
         ]},
    ],
}

# The three retrieval queries, verbatim from page 13 of the guide.
EVIDENCE_SQL = (
    "SELECT id,claim,basis,sources FROM evidence "
    "WHERE book_id=:book AND character_id=:who AND available_from<=:pos"
)
UTTERANCE_SQL = (
    "SELECT id,sources FROM utterances "
    "WHERE book_id=:book AND speaker_id=:who AND available_from<=:pos"
)
SNAPSHOT_SQL = (
    "SELECT j.value FROM characters AS c, json_each(c.voice_snapshots) AS j "
    "WHERE c.book_id=:book AND c.id=:who AND c.first_seen<=:pos "
    "AND json_extract(j.value,'$.through_position')<=:pos "
    "ORDER BY json_extract(j.value,'$.through_position') DESC LIMIT 1"
)


def fixture_candidates():
    return copy.deepcopy(_CANDIDATES)


def resolve(conn, book_id, candidates, start, through):
    return resolve_chunk(
        candidates, db.load_passages(conn, book_id), db.load_characters(conn, book_id),
        start, through,
    )


def counts(conn):
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("books", "passages", "characters", "utterances", "evidence")
    }


def checkpoint(conn, book_id):
    row = conn.execute(
        "SELECT indexed_through, status FROM books WHERE id=?", (book_id,)
    ).fetchone()
    return row["indexed_through"], row["status"]
