"""Read an EPUB into canonical paragraphs and store them as ``books`` + ``passages``.

Run as a script to import a file and eyeball the sentence splitting::

    python -m companion.epub book.epub --db companion.db --version extract-v1
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from bs4.element import Comment, Declaration, Doctype, ProcessingInstruction, Tag

from . import db
from .sentences import split_sentences

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from ebooklib import epub

# Elements that start a new paragraph. Anything else is inline and joins the
# surrounding text.
BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "dd", "details",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
})
SKIP_TAGS = frozenset({"head", "script", "style", "template", "svg", "math", "rt", "rp"})
_NON_TEXT = (Comment, Declaration, Doctype, ProcessingInstruction)
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))
_SPACES = re.compile(r"\s+")
_HTML_TYPES = {"application/xhtml+xml", "text/html"}


def canonical(text: str) -> str:
    """NFC text with invisible characters removed and all whitespace collapsed."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class Paragraph:
    href: str
    para_no: int
    text: str


@dataclass(frozen=True)
class ParsedBook:
    title: str
    epub_hash: str
    paragraphs: list[Paragraph]


@dataclass(frozen=True)
class ImportResult:
    book_id: int
    title: str
    paragraphs: int
    sentences: int
    created: bool  # False when this file and version were already imported


def paragraphs_from_html(content: bytes | str) -> list[str]:
    """Canonical paragraph texts of one XHTML document, in reading order.

    Each block element starts a paragraph. Text sitting directly in a block that
    also holds other blocks becomes its own paragraph, so nothing is dropped.
    """
    soup = BeautifulSoup(content, "html.parser")
    root = soup.body or soup
    out: list[str] = []

    def flush(run: list[str]) -> None:
        text = canonical("".join(run))
        if text:
            out.append(text)
        run.clear()

    def collect(element: Tag, run: list[str]) -> None:
        for child in element.children:
            if isinstance(child, NavigableString):
                if not isinstance(child, _NON_TEXT):
                    run.append(str(child))
            elif not isinstance(child, Tag) or child.name in SKIP_TAGS:
                continue
            elif child.name == "br":
                run.append(" ")
            elif child.name in BLOCK_TAGS:
                flush(run)
                inner: list[str] = []
                collect(child, inner)
                flush(inner)
            else:
                collect(child, run)

    run: list[str] = []
    collect(root, run)
    flush(run)
    return out


def _is_nav(item) -> bool:
    return isinstance(item, epub.EpubNav) or "nav" in (getattr(item, "properties", None) or [])


def read_epub(path: str | Path) -> ParsedBook:
    """Paragraphs of every linear spine document, in spine order."""
    path = Path(path)
    data = path.read_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        book = epub.read_epub(str(path))

    titles = book.get_metadata("DC", "title")
    title = canonical(titles[0][0]) if titles and titles[0][0] else ""

    paragraphs: list[Paragraph] = []
    for entry in book.spine:
        idref, linear = entry if isinstance(entry, tuple) else (entry, "yes")
        if linear == "no":
            continue
        item = book.get_item_with_id(idref)
        if item is None or _is_nav(item) or item.media_type not in _HTML_TYPES:
            continue
        href = item.get_name()
        for para_no, text in enumerate(paragraphs_from_html(item.get_content())):
            paragraphs.append(Paragraph(href, para_no, text))

    return ParsedBook(title or path.stem, hashlib.sha256(data).hexdigest(), paragraphs)


def import_epub(conn: sqlite3.Connection, path: str | Path, version: str) -> ImportResult:
    """Create the book row and its passages. Re-importing the same file and version is a no-op."""
    parsed = read_epub(path)
    if not parsed.paragraphs:
        raise ValueError(f"{path} has no readable text in its spine")

    book_id = db.create_book(conn, parsed.title, parsed.epub_hash, version)
    with db.write_tx(conn):
        existing = conn.execute(
            "SELECT count(*) AS n,"
            " coalesce(max(first_sentence + json_array_length(sentence_ends) - 1), 0) AS s"
            " FROM passages WHERE book_id=?", (book_id,),
        ).fetchone()
        if existing["n"]:
            return ImportResult(book_id, parsed.title, existing["n"], existing["s"], False)

        next_sentence = 1
        for position, paragraph in enumerate(parsed.paragraphs, start=1):
            ends = split_sentences(paragraph.text)
            db.insert_passage(
                conn, book_id,
                paragraph_pos=position, href=paragraph.href, para_no=paragraph.para_no,
                first_sentence=next_sentence, sentence_ends=ends, text=paragraph.text,
            )
            next_sentence += len(ends)

    return ImportResult(book_id, parsed.title, len(parsed.paragraphs), next_sentence - 1, True)


def _show(passage: db.Passage) -> str:
    pieces, start = [], 0
    for number, end in enumerate(passage.sentence_ends, start=passage.first_sentence):
        pieces.append(f"[{number}]{passage.text[start:end]}")
        start = end
    return f"¶{passage.paragraph_pos} {passage.href}#{passage.para_no}: " + "".join(pieces)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an EPUB into the passages table.")
    parser.add_argument("epub", type=Path)
    parser.add_argument("--db", default="companion.db")
    parser.add_argument("--version", default="extract-v1")
    parser.add_argument("--show", type=int, default=8, help="passages to print")
    parser.add_argument("--skip", type=int, default=0, help="passages to skip before printing")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    db.init_schema(conn)
    result = import_epub(conn, args.epub, args.version)
    print(f"book {result.book_id}: {result.title!r}")
    print(f"{result.paragraphs} paragraphs, {result.sentences} sentences"
          f" ({'imported' if result.created else 'already imported, nothing written'})")
    passages = list(db.load_passages(conn, result.book_id).values())
    for passage in passages[args.skip:args.skip + args.show]:
        print(_show(passage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
