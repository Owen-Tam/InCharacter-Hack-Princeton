import json
import sqlite3

import pytest

from companion import db
from helpers import FIXTURE_ENDS, FIXTURE_TEXT, PASSAGE_ID, counts


def test_foreign_keys_are_enabled(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_can_be_applied_twice(conn, book):
    db.init_schema(conn)
    assert counts(conn)["passages"] == 1


def test_file_database_is_shared_between_connections(tmp_path):
    path = tmp_path / "companion.db"
    worker = db.connect(path)
    db.init_schema(worker)
    book_id = db.create_book(worker, "T", "h", "v1")
    api = db.connect(path)
    assert api.execute("SELECT id FROM books").fetchone()["id"] == book_id
    assert api.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    worker.close()
    api.close()


def test_create_book_is_keyed_by_hash_and_version(conn):
    first = db.create_book(conn, "T", "hash", "extract-v1")
    assert db.create_book(conn, "T", "hash", "extract-v1") == first
    assert db.create_book(conn, "T", "hash", "extract-v2") != first
    row = conn.execute("SELECT status, indexed_through FROM books WHERE id=?", (first,)).fetchone()
    assert (row["status"], row["indexed_through"]) == ("pending", 0)


def test_status_is_constrained(conn, book):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE books SET status='done' WHERE id=?", (book,))


def test_json_columns_must_hold_arrays(conn, book):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO utterances (book_id, available_from, sources) VALUES (?,1,'{}')",
            (book,),
        )


def test_character_must_belong_to_the_same_book(conn, book):
    other = db.create_book(conn, "Other", "other-hash", "extract-v1")
    stranger = conn.execute(
        "INSERT INTO characters (book_id, display_name, first_seen) VALUES (?,?,1)",
        (other, "Stranger"),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO utterances (book_id, speaker_id, available_from, sources)"
            " VALUES (?,?,1,'[]')", (book, stranger),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evidence (book_id, character_id, claim, basis, available_from, sources)"
            " VALUES (?,?,'c','observed',1,'[]')", (book, stranger),
        )


def test_basis_is_constrained(conn, book):
    who = conn.execute(
        "INSERT INTO characters (book_id, display_name, first_seen) VALUES (?,?,1)",
        (book, "Theo"),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evidence (book_id, character_id, claim, basis, available_from, sources)"
            " VALUES (?,?,'c','guessed',1,'[]')", (book, who),
        )


def test_load_passages_decodes_rows(conn, book):
    passages = db.load_passages(conn, book)
    passage = passages[1]
    assert passage.id == PASSAGE_ID
    assert passage.sentence_ends == tuple(FIXTURE_ENDS)
    assert passage.text == FIXTURE_TEXT
    assert passage.last_sentence == 4
    assert db.load_passages(conn, book, through=0) == {}


@pytest.mark.parametrize("hi,sentence", [(1, 1), (21, 1), (22, 2), (93, 3), (94, 3), (95, 4), (139, 4)])
def test_sentence_at_uses_the_sentence_end(conn, book, hi, sentence):
    assert db.load_passages(conn, book)[1].sentence_at(hi) == sentence


@pytest.mark.parametrize("hi", [0, 140])
def test_sentence_at_rejects_offsets_outside_the_paragraph(conn, book, hi):
    with pytest.raises(ValueError):
        db.load_passages(conn, book)[1].sentence_at(hi)


@pytest.mark.parametrize("ends", [[], [5, 5, 9], [9, 5], [4], [0, 9], [10]])
def test_insert_passage_rejects_bad_sentence_ends(conn, book, ends):
    with pytest.raises(ValueError):
        db.insert_passage(
            conn, book, paragraph_pos=2, href="x", para_no=1,
            first_sentence=5, sentence_ends=ends, text="Two. Four",
        )
    assert counts(conn)["passages"] == 1


def test_paragraph_pos_is_unique_per_book(conn, book):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_passage(
            conn, book, paragraph_pos=1, href="x", para_no=1,
            first_sentence=5, sentence_ends=[4], text="Two.",
        )


def test_quote_text_joins_quote_ranges_in_order(conn, book):
    sources = [
        {"passage_id": PASSAGE_ID, "lo": 87, "hi": 93, "role": "quote"},
        {"passage_id": PASSAGE_ID, "lo": 56, "hi": 78, "role": "attribution"},
        {"passage_id": PASSAGE_ID, "lo": 81, "hi": 86, "role": "quote"},
    ]
    assert db.quote_text(conn, book, json.loads(json.dumps(sources))) == "again. Empty"
