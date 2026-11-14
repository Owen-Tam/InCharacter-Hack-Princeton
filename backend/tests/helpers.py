"""Shared test data: the guide's four-sentence fixture, its retrieval SQL, a fake LLM."""

import copy
import warnings

from companion import db
from companion.resolve import resolve_chunk

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from ebooklib import epub

FIXTURE_HTML = (
    "<h1>Chapter One</h1>\n<p>Theo stepped outside.\n   Mara hid the letter in the atlas."
    " Theo returned and\nsaid, “Empty again.” Mara showed him the letter"
    " inside the atlas.</p>"
)


def make_epub(path, chapters, *, spine=None, nonlinear=(), title="The Missing Letter"):
    """Write an EPUB with ``chapters`` as [(file_name, body_html)]."""
    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title(title)
    book.set_language("en")
    items = {}
    for name, body in chapters:
        chapter = epub.EpubHtml(title=name, file_name=name, lang="en")
        chapter.content = f"<html><head><title>{name}</title></head><body>{body}</body></html>"
        book.add_item(chapter)
        items[name] = chapter
    order = spine or [name for name, _ in chapters]
    book.toc = [epub.Link(name, f"TOC ENTRY {name}", name) for name in order]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + [(items[n], "no") if n in nonlinear else items[n] for n in order]
    epub.write_epub(str(path), book)
    return path


class FakeLLM:
    """Scripted stand-in for AdaptionLLM.

    ``replies`` maps a tool name (report_dialogue, report_evidence, report_voice)
    to a dict, a list of dicts consumed in order, or a function of the call.
    """

    def __init__(self, replies=None, fail_on=None):
        self.replies = replies or {}
        self.fail_on = fail_on  # (name, call number) -> raise on that call
        self.calls = []

    def complete_json(self, *, name, instructions, input, schema, max_output_tokens=8000,
                      reasoning_effort=None):
        self.calls.append({"name": name, "instructions": instructions, "input": input,
                           "reasoning_effort": reasoning_effort})
        count = sum(1 for c in self.calls if c["name"] == name)
        if self.fail_on == (name, count):
            raise RuntimeError(f"scripted failure on {name} #{count}")
        reply = self.replies.get(name, {})
        if callable(reply):
            return copy.deepcopy(reply(input))
        if isinstance(reply, list):
            return copy.deepcopy(reply[count - 1] if count <= len(reply) else {})
        return copy.deepcopy(reply)

    def names(self):
        return [c["name"] for c in self.calls]

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
