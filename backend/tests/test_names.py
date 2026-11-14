"""Full names: completing them from the text, and choosing the name to show."""

import json

from companion import db
from companion.chat import best_name
from companion.names import classify, complete_names


def _book(conn, paragraphs):
    """Insert one passage per paragraph, each paragraph a list of sentences."""
    book = db.create_book(conn, "Names", "names-hash", "v")
    sentence = 1
    for pos, sentences in enumerate(paragraphs, start=1):
        text, ends = "", []
        for s in sentences:
            text = f"{text} {s}" if text else s
            ends.append(len(text))
        db.insert_passage(conn, book, paragraph_pos=pos, href="ch.xhtml", para_no=pos - 1,
                          first_sentence=sentence, sentence_ends=ends, text=text)
        sentence += len(sentences)
    conn.execute("UPDATE books SET indexed_through=?, status='ready' WHERE id=?", (sentence - 1, book))
    return book


def _character(conn, book, display, first_seen, aliases=()):
    rows = [{"name": n, "available_from": at, "sources": []} for n, at in aliases]
    cur = conn.execute("INSERT INTO characters (book_id, display_name, first_seen, aliases)"
                       " VALUES (?,?,?,?)", (book, display, first_seen, json.dumps(rows)))
    return cur.lastrowid


def _row(conn, cid):
    return conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()


def _aliases(conn, cid):
    return {a["name"]: a for a in json.loads(_row(conn, cid)["aliases"])}


def _conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


# -- classify ------------------------------------------------------------------

def test_classify_kinds():
    assert classify("Jay Gatsby") == ("full", "Jay", "Gatsby")
    assert classify("George B. Wilson") == ("full", "George", "Wilson")
    assert classify("Mr. Gatsby") == ("titled", None, "Gatsby")
    assert classify("Miss Baker") == ("titled", None, "Baker")
    assert classify("Daisy") == ("given", "Daisy", None)
    assert classify("the narrator")[0] == "descriptive"
    assert classify("Wilson's wife")[0] == "descriptive"
    assert classify("Gatsby's butler")[0] == "descriptive"
    assert classify("Old Man Warner") == ("titled", None, "Warner")


def test_possessive_counts_toward_the_plain_name():
    conn = _conn()
    book = _book(conn, [["Myrtle laughed.", "It was Myrtle Wilson’s party.", "Myrtle Wilson sang."]])
    myrtle = _character(conn, book, "Myrtle", 1)
    added = complete_names(conn, book)
    assert [(a.name, a.occurrences, a.available_from) for a in added] == [("Myrtle Wilson", 2, 2)]
    assert list(_aliases(conn, myrtle)) == ["Myrtle Wilson"]


# -- complete_names -------------------------------------------------------------

def test_surname_path_adds_full_name_at_first_occurrence():
    conn = _conn()
    book = _book(conn, [
        ["Mr. Gatsby waved.", "Nobody answered."],
        ["They called him Jay Gatsby that night.", "Later, Jay Gatsby smiled."],
    ])
    gatsby = _character(conn, book, "Mr. Gatsby", 1)
    added = complete_names(conn, book)
    assert [(a.character_id, a.name) for a in added] == [(gatsby, "Jay Gatsby")]
    alias = _aliases(conn, gatsby)["Jay Gatsby"]
    assert alias["available_from"] == 3  # the sentence where the phrase first ends
    src = alias["sources"][0]
    passage = conn.execute("SELECT text FROM passages WHERE id=?", (src["passage_id"],)).fetchone()[0]
    assert passage[src["lo"]:src["hi"]] == "Jay Gatsby"
    assert src["role"] == "support"


def test_given_path_attaches_to_the_only_character_with_that_first_name():
    conn = _conn()
    book = _book(conn, [
        ["Myrtle laughed.", "Mr. Wilson wiped his hands."],
        ["Myrtle Wilson was loud.", "Everyone knew Myrtle Wilson."],
    ])
    myrtle = _character(conn, book, "Wilson's wife", 1, [("Myrtle", 1)])
    wilson = _character(conn, book, "Mr. Wilson", 2)
    complete_names(conn, book)
    assert "Myrtle Wilson" in _aliases(conn, myrtle)
    assert "Myrtle Wilson" not in _aliases(conn, wilson)


def test_full_name_waits_until_the_link_to_the_character_is_revealed():
    conn = _conn()
    book = _book(conn, [
        ["Myrtle Wilson was loud.", "Everyone knew Myrtle Wilson."],
        ["The woman was called Myrtle.", "It was late."],
    ])
    myrtle = _character(conn, book, "the woman", 1, [("Myrtle", 3)])
    complete_names(conn, book)
    # The phrase ends in sentence 1, but only sentence 3 ties "Myrtle" to her.
    assert _aliases(conn, myrtle)["Myrtle Wilson"]["available_from"] == 3


def test_shared_surname_is_not_guessed():
    conn = _conn()
    book = _book(conn, [
        ["Henry C. Gatz arrived.", "Mr. Gatz was tired."],
        ["James Gatz was the old name.", "James Gatz, he said again."],
    ])
    son = _character(conn, book, "Mr. Gatsby", 1)
    father = _character(conn, book, "Henry C. Gatz", 1, [("Mr. Gatz", 2)])
    complete_names(conn, book)
    assert "James Gatz" not in _aliases(conn, son)
    # The father is known as "Mr. Gatz", but "James" is not his given name and
    # another of his names already spells out a different full name.
    assert "James Gatz" not in _aliases(conn, father)


def test_one_off_phrase_and_sentence_starts_are_skipped():
    conn = _conn()
    book = _book(conn, [
        ["Daisy smiled.", "Once she was Daisy Fay of Louisville."],
        ["When Buchanan came, she left.", "When Buchanan called, she left again.",
         "Mr. Buchanan was rich.", "And when it rained they stayed."],
    ])
    daisy = _character(conn, book, "Daisy", 1)
    tom = _character(conn, book, "Mr. Buchanan", 5)
    complete_names(conn, book)
    assert _aliases(conn, daisy) == {}  # "Daisy Fay" occurs once
    assert _aliases(conn, tom) == {}  # "when" is an ordinary word, not a first name


def test_complete_names_is_idempotent():
    conn = _conn()
    book = _book(conn, [["Jay Gatsby came.", "Jay Gatsby left.", "Mr. Gatsby slept."]])
    gatsby = _character(conn, book, "Mr. Gatsby", 1)
    assert len(complete_names(conn, book)) == 1
    assert complete_names(conn, book) == []
    assert list(_aliases(conn, gatsby)) == ["Jay Gatsby"]


# -- best_name -------------------------------------------------------------------

def test_best_name_combines_first_name_and_titled_surname():
    conn = _conn()
    book = _book(conn, [["a"] * 10])
    nick = _character(conn, book, "the narrator", 1, [("Nick", 4), ("Mr. Carraway", 7)])
    row = _row(conn, nick)
    assert best_name(row, 3) == "the narrator"
    assert best_name(row, 5) == "Nick"
    assert best_name(row, 7) == "Nick Carraway"


def test_best_name_prefers_the_first_full_name_revealed():
    conn = _conn()
    book = _book(conn, [["a"] * 10])
    gatsby = _character(conn, book, "Mr. Gatsby", 1, [("Jay Gatsby", 4), ("Jimmy", 6), ("James Gatz", 8)])
    row = _row(conn, gatsby)
    assert best_name(row, 2) == "Mr. Gatsby"
    assert best_name(row, 4) == "Jay Gatsby"
    assert best_name(row, 9) == "Jay Gatsby"  # stays put rather than flipping


def test_best_name_keeps_single_and_titled_names():
    conn = _conn()
    book = _book(conn, [["a"] * 4])
    assert best_name(_row(conn, _character(conn, book, "Daisy", 1)), 4) == "Daisy"
    assert best_name(_row(conn, _character(conn, book, "Mr. Wolfshiem", 1)), 4) == "Mr. Wolfshiem"
    wife = _character(conn, book, "Wilson's wife", 1, [("Myrtle", 3)])
    assert best_name(_row(conn, wife), 2) == "Wilson's wife"
    assert best_name(_row(conn, wife), 3) == "Myrtle"
