import pytest

from companion import db
from helpers import FIXTURE_ENDS, FIXTURE_TEXT, PASSAGE_ID


@pytest.fixture
def conn():
    connection = db.connect()
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def book(conn):
    """The Missing Letter: one paragraph, four sentences, nothing extracted yet."""
    book_id = db.create_book(conn, "The Missing Letter", "demo-epub-hash", "extract-v1")
    db.insert_passage(
        conn, book_id, id=PASSAGE_ID, paragraph_pos=1, href="Text/ch01.xhtml", para_no=0,
        first_sentence=1, sentence_ends=FIXTURE_ENDS, text=FIXTURE_TEXT,
    )
    return book_id
