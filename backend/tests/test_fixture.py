"""The four-sentence fixture run from page 13 of the guide, end to end.

Candidates -> resolve -> commit -> snapshot, then the guide's own retrieval SQL
at bookmarks 3 and 4.
"""

import json

import pytest

from companion import db
from companion.commit import commit_chunk
from companion.snapshots import publish_snapshot
from helpers import (EVIDENCE_SQL, PASSAGE_ID, SNAPSHOT_SQL, UTTERANCE_SQL, checkpoint,
                     counts, fixture_candidates, resolve)

LETTER_LOCATION = "Theo was shown the letter inside the atlas."


@pytest.fixture
def imported(conn, book):
    chunk = resolve(conn, book, fixture_candidates(), 0, 4)
    result = commit_chunk(conn, book, chunk, final=True)
    theo = result.character_ids["theo"]
    publish_snapshot(conn, book, theo, {
        "through_position": 3,
        "traits": [{"pattern": "Replies in a short fragment.",
                    "supporting_utterance_ids": result.utterance_ids}],
        "recurring_phrases": [],
    })
    return {"book": book, "theo": theo, "mara": result.character_ids["mara"], "chunk": chunk}


def claims(conn, imported, who, pos):
    rows = conn.execute(
        EVIDENCE_SQL, {"book": imported["book"], "who": imported[who], "pos": pos})
    return {row["claim"]: row for row in rows}


def test_stored_rows_match_the_guides_example_records(conn, imported):
    book = conn.execute("SELECT * FROM books").fetchone()
    assert (book["title"], book["status"], book["indexed_through"]) == (
        "The Missing Letter", "ready", 4)

    utterance = conn.execute("SELECT * FROM utterances").fetchone()
    assert utterance["speaker_id"] == imported["theo"]
    assert utterance["addressee_id"] is None
    assert utterance["available_from"] == 3
    assert json.loads(utterance["sources"]) == [
        {"passage_id": 11, "lo": 81, "hi": 93, "role": "quote"},
        {"passage_id": 11, "lo": 56, "hi": 78, "role": "attribution"},
    ]

    evidence = conn.execute(
        "SELECT * FROM evidence WHERE claim=?", (LETTER_LOCATION,)).fetchone()
    assert evidence["character_id"] == imported["theo"]
    assert evidence["basis"] == "observed"
    assert evidence["reported_by_character_id"] is None
    assert evidence["available_from"] == 4
    assert json.loads(evidence["sources"]) == [
        {"passage_id": 11, "lo": 95, "hi": 139, "role": "support"},
        {"passage_id": 11, "lo": 56, "hi": 69, "role": "support"},
    ]

    theo = db.load_characters(conn, imported["book"])[0]
    assert (theo.display_name, theo.first_seen, theo.aliases) == ("Theo", 1, [])
    assert theo.voice_snapshots == [{
        "through_position": 3,
        "traits": [{"pattern": "Replies in a short fragment.",
                    "supporting_utterance_ids": [utterance["id"]]}],
        "recurring_phrases": [],
    }]


def test_at_3_theo_has_a_voice_sample_but_not_the_letters_location(conn, imported):
    params = {"book": imported["book"], "who": imported["theo"], "pos": 3}
    (utterance,) = conn.execute(UTTERANCE_SQL, params).fetchall()
    assert db.quote_text(conn, imported["book"], json.loads(utterance["sources"])) == "Empty again."
    assert json.loads(conn.execute(SNAPSHOT_SQL, params).fetchone()[0])["through_position"] == 3
    assert LETTER_LOCATION not in claims(conn, imported, "theo", 3)
    assert set(claims(conn, imported, "theo", 3)) == {"Theo said it was empty again."}


def test_at_4_the_letters_location_is_admitted_and_rewinding_hides_it(conn, imported):
    assert LETTER_LOCATION in claims(conn, imported, "theo", 4)
    rewound = claims(conn, imported, "theo", 3)
    assert LETTER_LOCATION not in rewound
    # nothing Theo can cite at 3 reaches into sentence 4
    passage = db.load_passages(conn, imported["book"])[1]
    for row in rewound.values():
        for source in json.loads(row["sources"]):
            assert passage.sentence_at(source["hi"]) <= 3


@pytest.mark.parametrize("pos", [3, 4])
def test_theo_has_no_witness_account_of_who_hid_the_letter(conn, imported, pos):
    for row in claims(conn, imported, "theo", pos).values():
        assert "hid" not in row["claim"]
        for source in json.loads(row["sources"]):
            assert source["passage_id"] == PASSAGE_ID
            assert not (source["lo"] < 55 and source["hi"] > 22)  # never sentence 2
    assert "Mara hid the letter in the atlas." in claims(conn, imported, "mara", pos)


def test_nothing_is_retrievable_before_it_is_read(conn, imported):
    assert claims(conn, imported, "theo", 2) == {}
    assert claims(conn, imported, "mara", 1) == {}
    params = {"book": imported["book"], "who": imported["theo"], "pos": 2}
    assert conn.execute(UTTERANCE_SQL, params).fetchall() == []
    assert conn.execute(SNAPSHOT_SQL, params).fetchone() is None


def test_repeating_the_chunk_commit_keeps_record_counts_stable(conn, imported):
    before = counts(conn)
    again = commit_chunk(conn, imported["book"], imported["chunk"], final=True)
    assert not again.committed
    assert counts(conn) == before
    assert checkpoint(conn, imported["book"]) == (4, "ready")
