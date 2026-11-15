import json

import pytest

from companion import db
from companion.commit import commit_chunk
from companion.epub import book_author, canonical, import_epub, main, paragraphs_from_html, read_epub
from helpers import (FIXTURE_ENDS, FIXTURE_HTML, FIXTURE_TEXT, counts, fixture_candidates,
                     make_epub, resolve)


# -- canonical text -----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("  a \n\t b  ", "a b"),
    ("a  b", "a b"),
    ("con­tinue", "continue"),
    ("zero​width﻿", "zerowidth"),
    ("café", "café"),
    ("“curly” — dash", "“curly” — dash"),
])
def test_canonical(raw, expected):
    assert canonical(raw) == expected


@pytest.mark.parametrize("creators,expected", [
    ([("F. Scott Fitzgerald", {"id": "author_0"})], "F. Scott Fitzgerald"),
    ([("Anne Frank; Otto Frank; Mirjam Pressler", {})], "Anne Frank"),
    ([("Unknown", {}), ("Shirley  Jackson", {})], "Shirley Jackson"),
    ([("Unknown", {})], None),
    ([], None),
])
def test_book_author(creators, expected):
    assert book_author(creators) == expected


# -- paragraphs from one document ---------------------------------------------------

@pytest.mark.parametrize("html,expected", [
    ("<p>One.</p><p>Two.</p>", ["One.", "Two."]),
    ("<div><div><p>A</p><p>B</p></div></div>", ["A", "B"]),
    ("<blockquote><p>Quoted.</p></blockquote>", ["Quoted."]),
    ("<div>Bare text in a div.</div>", ["Bare text in a div."]),
    ("<p>line<br/>break</p>", ["line break"]),
    ("<p><i>Pride</i> and <b>Prejudice</b></p>", ["Pride and Prejudice"]),
    ("<p>a<span>b<br/>c</span>d</p>", ["ab cd"]),
    ("<div>Lead text<p>Inner.</p>Tail text</div>", ["Lead text", "Inner.", "Tail text"]),
    ("<p></p><p>   </p><p/><p>Kept.</p>", ["Kept."]),
    ("<p>Keep<script>drop()</script><style>p{}</style><!-- note --> this.</p>", ["Keep this."]),
    ("<ul><li>First</li><li>Second</li></ul>", ["First", "Second"]),
    ("<p>caf&eacute; &amp; &#8220;tea&#8221;&nbsp;now</p>", ["café & “tea” now"]),
    ("<h2>II</h2><p>Text.</p><hr/><p>More.</p>", ["II", "Text.", "More."]),
])
def test_paragraphs_from_html(html, expected):
    assert paragraphs_from_html(f"<html><body>{html}</body></html>") == expected


def test_xhtml_self_closing_block_does_not_swallow_the_next_paragraph():
    xhtml = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
             "<head><title>t</title></head><body><div/><p>After.</p></body></html>")
    assert paragraphs_from_html(xhtml.encode()) == ["After."]


# -- reading a whole EPUB -----------------------------------------------------------

def test_spine_order_and_anchors(tmp_path):
    path = make_epub(tmp_path / "b.epub", [
        ("Text/ch01.xhtml", "<p>One.</p><p></p><p>Two.</p>"),
        ("Text/ch02.xhtml", "<p>Three.</p>"),
    ], spine=["Text/ch02.xhtml", "Text/ch01.xhtml"])
    parsed = read_epub(path)
    assert parsed.title == "The Missing Letter"
    assert [(p.href, p.para_no, p.text) for p in parsed.paragraphs] == [
        ("Text/ch02.xhtml", 0, "Three."),
        ("Text/ch01.xhtml", 0, "One."),
        ("Text/ch01.xhtml", 1, "Two."),
    ]


def test_nav_and_nonlinear_documents_are_skipped(tmp_path):
    path = make_epub(tmp_path / "b.epub", [
        ("notes.xhtml", "<p>Endnote text.</p>"),
        ("ch01.xhtml", "<p>Story.</p>"),
    ], nonlinear={"notes.xhtml"})
    texts = [p.text for p in read_epub(path).paragraphs]
    assert texts == ["Story."]
    assert not any("TOC ENTRY" in t for t in texts)


def test_hash_depends_only_on_bytes(tmp_path):
    a = make_epub(tmp_path / "a.epub", [("c.xhtml", "<p>Same.</p>")])
    b = tmp_path / "b.epub"
    b.write_bytes(a.read_bytes())
    assert read_epub(a).epub_hash == read_epub(b).epub_hash
    assert len(read_epub(a).epub_hash) == 64


def test_title_falls_back_to_file_name(tmp_path):
    path = make_epub(tmp_path / "untitled-story.epub", [("c.xhtml", "<p>x</p>")], title="")
    assert read_epub(path).title == "untitled-story"


# -- importing into the database ----------------------------------------------------

def test_import_numbers_paragraphs_and_sentences_across_documents(conn, tmp_path):
    path = make_epub(tmp_path / "b.epub", [
        ("Text/ch01.xhtml", FIXTURE_HTML),
        ("Text/ch02.xhtml", "<p>Mr. Darcy bowed. She left.</p><p>Fin</p>"),
    ])
    result = import_epub(conn, path, "extract-v1")
    assert (result.paragraphs, result.sentences, result.created) == (4, 8, True)

    passages = list(db.load_passages(conn, result.book_id).values())
    assert [(p.paragraph_pos, p.href, p.para_no, p.first_sentence) for p in passages] == [
        (1, "Text/ch01.xhtml", 0, 1),
        (2, "Text/ch01.xhtml", 1, 2),
        (3, "Text/ch02.xhtml", 0, 6),
        (4, "Text/ch02.xhtml", 1, 8),
    ]
    assert passages[0].text == "Chapter One"
    assert passages[1].text == FIXTURE_TEXT
    assert list(passages[1].sentence_ends) == FIXTURE_ENDS
    assert list(passages[2].sentence_ends) == [16, 26]

    book = conn.execute("SELECT * FROM books").fetchone()
    assert (book["status"], book["indexed_through"]) == ("pending", 0)


def test_reimport_writes_nothing_and_new_version_makes_a_new_book(conn, tmp_path):
    path = make_epub(tmp_path / "b.epub", [("c.xhtml", FIXTURE_HTML)])
    first = import_epub(conn, path, "extract-v1")
    before = counts(conn)
    again = import_epub(conn, path, "extract-v1")
    assert (again.book_id, again.created) == (first.book_id, False)
    assert (again.paragraphs, again.sentences) == (first.paragraphs, first.sentences)
    assert counts(conn) == before

    other = import_epub(conn, path, "extract-v2")
    assert other.book_id != first.book_id and other.created
    assert counts(conn)["books"] == 2


def test_epub_without_text_is_refused(conn, tmp_path):
    path = make_epub(tmp_path / "b.epub", [("c.xhtml", "<p>  </p><img src='x.png'/>")])
    with pytest.raises(ValueError, match="no readable text"):
        import_epub(conn, path, "extract-v1")
    assert counts(conn)["books"] == 0


def test_imported_fixture_resolves_to_the_guides_offsets(conn, tmp_path):
    path = make_epub(tmp_path / "b.epub", [("Text/ch01.xhtml", FIXTURE_HTML)])
    book = import_epub(conn, path, "extract-v1").book_id
    fixture_id = db.load_passages(conn, book)[2].id

    candidates = fixture_candidates()
    for section in ("utterances", "evidence"):
        for record in candidates[section]:
            for key in ("quotes", "attribution", "support"):
                for span in record.get(key) or []:
                    span["para"] = 2  # the heading is paragraph 1
    for character in candidates["characters"]:
        character["first_mention"]["para"] = 2

    # the heading is sentence 1, so the fixture's sentences 1-4 become 2-5
    chunk = resolve(conn, book, candidates, 0, 5)
    assert chunk.rejects == []
    commit_chunk(conn, book, chunk, final=True)

    utterance = conn.execute("SELECT * FROM utterances").fetchone()
    assert utterance["available_from"] == 4
    assert json.loads(utterance["sources"]) == [
        {"passage_id": fixture_id, "lo": 81, "hi": 93, "role": "quote"},
        {"passage_id": fixture_id, "lo": 56, "hi": 78, "role": "attribution"},
    ]
    shown = conn.execute(
        "SELECT available_from FROM evidence WHERE claim LIKE 'Theo was shown%'").fetchone()
    assert shown["available_from"] == 5


def test_cli(tmp_path, capsys):
    path = make_epub(tmp_path / "b.epub", [("Text/ch01.xhtml", FIXTURE_HTML)])
    database = tmp_path / "c.db"
    assert main([str(path), "--db", str(database)]) == 0
    out = capsys.readouterr().out
    assert "2 paragraphs, 5 sentences (imported)" in out
    assert "[2]Theo stepped outside.[3] Mara hid" in out
    main([str(path), "--db", str(database)])
    assert "already imported, nothing written" in capsys.readouterr().out
