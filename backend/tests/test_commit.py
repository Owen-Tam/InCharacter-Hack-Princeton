import json
import sqlite3

import pytest

from companion import db
from companion.commit import CheckpointMismatch, commit_chunk
from companion.resolve import (AliasRow, EvidenceRow, NewCharacter, ResolvedChunk,
                               UtteranceRow)
from helpers import PASSAGE_ID, checkpoint, counts, fixture_candidates, resolve

EMPTY = {"books": 1, "passages": 1, "characters": 0, "utterances": 0, "evidence": 0}
FULL = {"books": 1, "passages": 1, "characters": 2, "utterances": 1, "evidence": 3}


def quote(passage_id=PASSAGE_ID, lo=81, hi=93, role="quote"):
    return {"passage_id": passage_id, "lo": lo, "hi": hi, "role": role}


def test_commit_writes_rows_and_advances_the_checkpoint(conn, book):
    result = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4))
    assert result.committed and result.indexed_through == 4
    assert counts(conn) == FULL
    assert checkpoint(conn, book) == (4, "indexing")
    assert set(result.character_ids) == {"theo", "mara"}
    assert len(result.utterance_ids) == 1 and len(result.evidence_ids) == 3

    row = conn.execute("SELECT * FROM utterances").fetchone()
    assert row["speaker_id"] == result.character_ids["theo"]
    assert row["addressee_id"] is None
    assert json.loads(row["sources"])[0] == quote()
    names = {c.display_name: c.first_seen for c in db.load_characters(conn, book)}
    assert names == {"Theo": 1, "Mara": 2}


def test_final_chunk_marks_the_book_ready(conn, book):
    commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4), final=True)
    assert checkpoint(conn, book) == (4, "ready")


def test_repeating_a_commit_changes_nothing(conn, book):
    chunk = resolve(conn, book, fixture_candidates(), 0, 4)
    commit_chunk(conn, book, chunk)
    again = commit_chunk(conn, book, chunk)
    assert not again.committed and again.indexed_through == 4
    assert again.utterance_ids == []
    assert counts(conn) == FULL
    assert checkpoint(conn, book) == (4, "indexing")


def test_chunk_starting_at_the_wrong_checkpoint_is_refused(conn, book):
    for start, through in [(2, 4), (1, 2)]:
        with pytest.raises(CheckpointMismatch):
            commit_chunk(conn, book, ResolvedChunk(start, through))
    conn.execute("UPDATE books SET indexed_through=2 WHERE id=?", (book,))
    with pytest.raises(CheckpointMismatch):
        commit_chunk(conn, book, ResolvedChunk(0, 4))  # overlaps committed sentences
    assert counts(conn) == EMPTY


def test_unknown_book_is_refused(conn, book):
    with pytest.raises(LookupError):
        commit_chunk(conn, book + 1, ResolvedChunk(0, 4))


def test_empty_chunk_still_advances_the_checkpoint(conn, book):
    assert commit_chunk(conn, book, ResolvedChunk(0, 2)).committed
    assert checkpoint(conn, book) == (2, "indexing")


@pytest.mark.parametrize("source", [
    quote(passage_id=999),
    quote(lo=130, hi=140),
    quote(lo=90, hi=90),
    quote(lo=-1, hi=4),
    quote(role="vibes"),
])
def test_bad_source_rolls_back_the_whole_chunk(conn, book, source):
    chunk = ResolvedChunk(
        0, 4,
        characters=[NewCharacter("theo", "Theo", 1)],
        utterances=[UtteranceRow("theo", None, 3, [quote()]),
                    UtteranceRow("theo", None, 3, [source])],
    )
    with pytest.raises(ValueError):
        commit_chunk(conn, book, chunk)
    assert counts(conn) == EMPTY
    assert checkpoint(conn, book) == (0, "pending")


def test_source_in_another_books_passage_is_refused(conn, book):
    other = db.create_book(conn, "Other", "other-hash", "extract-v1")
    db.insert_passage(conn, other, id=50, paragraph_pos=1, href="a", para_no=0,
                      first_sentence=1, sentence_ends=[20], text="x" * 20)
    chunk = ResolvedChunk(0, 4, utterances=[UtteranceRow(None, None, 3, [quote(50, 0, 5)])])
    with pytest.raises(ValueError):
        commit_chunk(conn, book, chunk)
    assert counts(conn)["utterances"] == 0


def test_foreign_key_failure_rolls_back(conn, book):
    chunk = ResolvedChunk(
        0, 4,
        characters=[NewCharacter("theo", "Theo", 1)],
        evidence=[EvidenceRow(999, "claim", "observed", None, 4, [quote(role="support")])],
    )
    with pytest.raises(sqlite3.IntegrityError):
        commit_chunk(conn, book, chunk)
    assert counts(conn) == EMPTY
    assert checkpoint(conn, book) == (0, "pending")
    # the connection is usable afterwards
    assert commit_chunk(conn, book, ResolvedChunk(0, 4)).committed


def test_two_chunks_share_one_character_registry(conn, book):
    first = fixture_candidates()
    first["utterances"] = []
    first["evidence"] = first["evidence"][:1]
    one = commit_chunk(conn, book, resolve(conn, book, first, 0, 2))
    theo = one.character_ids["theo"]

    second = fixture_candidates()
    second["characters"] = second["characters"][:1]  # re-declares Theo
    second["evidence"] = second["evidence"][1:]
    chunk = resolve(conn, book, second, 2, 4)
    assert chunk.rejects == [] and chunk.characters == []
    two = commit_chunk(conn, book, chunk, final=True)

    assert two.character_ids == {}
    assert counts(conn) == FULL
    assert checkpoint(conn, book) == (4, "ready")
    speakers = [r[0] for r in conn.execute("SELECT speaker_id FROM utterances")]
    assert speakers == [theo]


def test_aliases_are_appended_in_order_without_duplicates(conn, book):
    commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 2))
    theo = db.load_characters(conn, book)[0].id
    late = {"name": "him", "available_from": 4, "sources": [quote(lo=100, hi=110, role="support")]}
    early = {"name": "the visitor", "available_from": 3, "sources": [quote(lo=56, hi=60, role="support")]}
    commit_chunk(conn, book, ResolvedChunk(2, 4, aliases=[
        AliasRow(theo, late), AliasRow(theo, early), AliasRow(theo, dict(late))]))
    assert db.load_characters(conn, book)[0].aliases == [early, late]


def test_alias_for_a_character_created_in_the_same_chunk(conn, book):
    alias = {"name": "him", "available_from": 4, "sources": [quote(lo=100, hi=110, role="support")]}
    commit_chunk(conn, book, ResolvedChunk(
        0, 4, characters=[NewCharacter("theo", "Theo", 1)], aliases=[AliasRow("theo", alias)]))
    assert db.load_characters(conn, book)[0].aliases == [alias]


def test_writer_waits_out_no_one_and_fails_fast_when_locked(tmp_path):
    path = tmp_path / "companion.db"
    first = db.connect(path)
    db.init_schema(first)
    book_id = db.create_book(first, "T", "h", "v1")
    second = sqlite3.connect(str(path), isolation_level=None, timeout=0)
    second.row_factory = sqlite3.Row
    first.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        commit_chunk(second, book_id, ResolvedChunk(0, 1))
    first.execute("ROLLBACK")
    assert commit_chunk(second, book_id, ResolvedChunk(0, 1)).committed
    first.close()
    second.close()
