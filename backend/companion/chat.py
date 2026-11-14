"""From bookmark to reply: retrieval, composition and citation checks.

Everything a reply may state comes from evidence the character could have at
the reader's bookmark (the guide's page 13 queries). The model must say which
evidence backs each factual statement; ids outside the retrieved set are
dropped, and a reply with unsupported statements is rewritten once.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from . import config
from .db import Passage, quote_text
from .llm import LLM
from .resolve import fold

EVIDENCE_SQL = (
    "SELECT id, claim, basis, sources, available_from FROM evidence"
    " WHERE book_id=:book AND character_id=:who AND available_from<=:pos"
)
UTTERANCE_SQL = (
    "SELECT id, sources, addressee_id, available_from FROM utterances"
    " WHERE book_id=:book AND speaker_id=:who AND available_from<=:pos"
)
SNAPSHOT_SQL = (
    "SELECT j.value FROM characters AS c, json_each(c.voice_snapshots) AS j"
    " WHERE c.book_id=:book AND c.id=:who AND c.first_seen<=:pos"
    " AND json_extract(j.value,'$.through_position')<=:pos"
    " ORDER BY json_extract(j.value,'$.through_position') DESC LIMIT 1"
)

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "statements": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["text", "evidence_ids"],
        }},
    },
    "required": ["reply", "statements"],
}

_STOPWORDS = frozenset("""
a about after again all am an and any are as at be been before being but by can could
did do does doing don't for from had has have having he her here hers him his how i if
in into is it its just know me more most my no nor not of off on once only or other our
out over own said same she should so some such tell than that the their them then there
these they this those through to too under until up very was we were what when where
which while who whom why will with would you your think do did does going get got
""".split())


class ChatError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class EvidenceItem:
    id: int
    claim: str
    basis: str
    available_from: int
    sources: list


@dataclass
class VoiceLine:
    id: int
    text: str
    available_from: int


@dataclass
class Retrieved:
    book_id: int
    position: int
    character: dict
    evidence: list[EvidenceItem]
    lines: list[VoiceLine]
    snapshot: dict | None
    evidence_total: int


@dataclass
class ChatResult:
    reply: str
    citations: list[dict]
    statements: list[dict]
    unsupported: list[str]
    voice_lines: list[dict]
    kind: str = "imagined_reply"
    retrieved_evidence_ids: list[int] = field(default_factory=list)


# -- names and positions ------------------------------------------------------

def known_names(row: sqlite3.Row, position: int) -> list[str]:
    """The character's names the reader has met by ``position``."""
    names = [row["display_name"]]
    for alias in json.loads(row["aliases"]):
        if alias.get("available_from", 0) <= position and alias["name"] not in names:
            names.append(alias["name"])
    return names


def best_name(row: sqlite3.Row, position: int) -> str:
    """The fullest name the reader knows for this character at ``position``:
    a full name ("Jay Gatsby") if one has been revealed, else a first name joined
    to a titled surname ("Nick" + "Mr. Carraway"), else a first or titled name,
    else the descriptive label ("the narrator")."""
    from .names import classify

    revealed = [(row["first_seen"], row["display_name"])] + sorted(
        (a.get("available_from", 0), a["name"]) for a in json.loads(row["aliases"])
        if a.get("available_from", 0) <= position)
    kinds = [(classify(name), name) for _, name in revealed]  # in reveal order
    for (kind, _, _), name in kinds:
        if kind == "full":
            return name  # the first one revealed, so the name doesn't change later
    given = next((g for (kind, g, _), _ in kinds if kind == "given"), None)
    surname = next((s for (kind, _, s), _ in kinds if kind == "titled"), None)
    if given and surname:
        return f"{given} {surname}"
    for (kind, _, _), name in kinds:
        if kind in ("given", "titled"):
            return name
    return row["display_name"]


def book_row(conn: sqlite3.Connection, book_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if row is None:
        raise ChatError(404, f"no book with id {book_id}")
    return row


def check_position(book: sqlite3.Row, position: int) -> None:
    if position < 1:
        raise ChatError(400, "bookmark must be a sentence number from 1")
    if position > book["indexed_through"]:
        raise ChatError(409, f"the book is only indexed through sentence"
                             f" {book['indexed_through']}; try again shortly")


def character_row(conn: sqlite3.Connection, book_id: int, character_id: int,
                  position: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM characters WHERE id=? AND book_id=?",
                       (character_id, book_id)).fetchone()
    if row is None:
        raise ChatError(404, f"no character {character_id} in book {book_id}")
    if row["first_seen"] > position:
        raise ChatError(400, "the reader has not met this character yet")
    return row


# -- retrieval ----------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", fold(text).casefold())
    out = []
    for word in words:
        word = word.removesuffix("'s")
        if word in _STOPWORDS or len(word) < 2:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def rank_evidence(items: Sequence[EvidenceItem], question: str) -> list[tuple[float, EvidenceItem]]:
    """Keyword ranking with idf weights over the character's own claims."""
    query = set(_tokens(question))
    docs = [set(_tokens(item.claim)) for item in items]
    frequency = Counter(token for doc in docs for token in doc)
    total = max(len(docs), 1)
    scored = []
    for item, doc in zip(items, docs):
        overlap = query & doc
        score = sum(math.log(1 + total / frequency[token]) for token in overlap)
        scored.append((score, item))
    return scored


def retrieve(
    conn: sqlite3.Connection, book_id: int, character_id: int, position: int,
    question: str, *, relevant: int = 10, recent: int = 6, max_lines: int = 8,
) -> Retrieved:
    book = book_row(conn, book_id)
    check_position(book, position)
    row = character_row(conn, book_id, character_id, position)
    params = {"book": book_id, "who": character_id, "pos": position}

    items = [
        EvidenceItem(r["id"], r["claim"], r["basis"], r["available_from"], json.loads(r["sources"]))
        for r in conn.execute(EVIDENCE_SQL, params)
    ]
    chosen: dict[int, EvidenceItem] = {}
    for score, item in sorted(rank_evidence(items, question), key=lambda s: (-s[0], -s[1].available_from)):
        if score <= 0 or len(chosen) >= relevant:
            break
        chosen[item.id] = item
    for item in sorted(items, key=lambda i: (i.available_from, i.id))[-recent:]:
        chosen.setdefault(item.id, item)
    evidence = sorted(chosen.values(), key=lambda i: (i.available_from, i.id))

    lines = []
    for r in conn.execute(UTTERANCE_SQL + " ORDER BY available_from DESC, id DESC", params):
        text = quote_text(conn, book_id, json.loads(r["sources"]))
        if len(text) >= 12 or len(lines) < 2:
            lines.append(VoiceLine(r["id"], text, r["available_from"]))
        if len(lines) >= max_lines:
            break
    lines.reverse()

    snapshot_row = conn.execute(SNAPSHOT_SQL, params).fetchone()
    snapshot = json.loads(snapshot_row[0]) if snapshot_row else None
    character = {"id": row["id"], "name": best_name(row, position),
                 "names": known_names(row, position), "first_seen": row["first_seen"]}
    return Retrieved(book_id, position, character, evidence, lines, snapshot, len(items))


# -- citations -----------------------------------------------------------------

def load_passage(conn: sqlite3.Connection, book_id: int, passage_id: int) -> Passage:
    r = conn.execute("SELECT * FROM passages WHERE id=? AND book_id=?",
                     (passage_id, book_id)).fetchone()
    if r is None:
        raise ChatError(404, f"no passage {passage_id} in book {book_id}")
    return Passage(r["id"], r["book_id"], r["paragraph_pos"], r["href"], r["para_no"],
                   r["first_sentence"], tuple(json.loads(r["sentence_ends"])), r["text"])


def clamp(passage: Passage, position: int) -> dict:
    """The paragraph as far as the reader has read: never past ``position``."""
    if passage.first_sentence > position:
        raise ChatError(403, "that passage is past the reader's bookmark")
    if passage.last_sentence <= position:
        end, truncated = len(passage.text), False
    else:
        end, truncated = passage.sentence_ends[position - passage.first_sentence], True
    return {
        "passage_id": passage.id, "paragraph_pos": passage.paragraph_pos,
        "href": passage.href, "para_no": passage.para_no,
        "first_sentence": passage.first_sentence,
        "text": passage.text[:end], "truncated": truncated,
    }


def cite(conn: sqlite3.Connection, book_id: int, item: EvidenceItem, position: int) -> dict:
    sources = []
    for source in item.sources:
        passage = load_passage(conn, book_id, source["passage_id"])
        view = clamp(passage, position)
        hi = min(source["hi"], len(view["text"]))
        sources.append({**view, "lo": source["lo"], "hi": hi, "role": source["role"],
                        "quote": view["text"][source["lo"]:hi]})
    return {"evidence_id": item.id, "claim": item.claim, "basis": item.basis,
            "available_from": item.available_from, "sources": sources}


# -- composition ---------------------------------------------------------------

def render(retrieved: Retrieved, question: str, history: Sequence[dict]) -> str:
    c = retrieved.character
    parts = [f"CHARACTER: {c['name']}"]
    others = [n for n in c["names"] if n != c["name"]]
    if others:
        parts.append(f"ALSO CALLED: {', '.join(others)}")
    notes = []
    if retrieved.snapshot:
        notes += [f"- {t['pattern']}" for t in retrieved.snapshot.get("traits", [])]
        notes += [f"- Often says: \"{p['phrase']}\"" for p in retrieved.snapshot.get("recurring_phrases", [])]
    parts.append("VOICE NOTES\n" + ("\n".join(notes) if notes else "(none yet)"))
    lines = "\n".join(f"- \"{line.text}\"" for line in retrieved.lines)
    parts.append("REAL LINES\n" + (lines or "(they have not spoken yet; infer a plausible voice from what they know)"))
    known = "\n".join(f"[{e.id}] {e.claim}" for e in retrieved.evidence)
    parts.append("WHAT THEY KNOW\n" + (known or "(nothing recorded yet)"))
    if history:
        turns = []
        for turn in history[-10:]:
            who = "Reader" if turn.get("role") == "reader" else c["name"]
            turns.append(f"{who}: {turn.get('text', '')}")
        parts.append("CONVERSATION SO FAR\n" + "\n".join(turns))
    parts.append(f"THE READER ASKS\n{question}")
    return "\n\n".join(parts)


def _check(raw: dict, allowed: set[int]) -> tuple[list[dict], list[str]]:
    statements, unsupported = [], []
    for entry in raw.get("statements") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        ids = [i for i in entry.get("evidence_ids") or []
               if isinstance(i, int) and not isinstance(i, bool) and i in allowed]
        statements.append({"text": entry["text"], "evidence_ids": list(dict.fromkeys(ids))})
        if not ids:
            unsupported.append(entry["text"])
    return statements, unsupported


def compose(llm: LLM, retrieved: Retrieved, question: str,
            history: Sequence[dict] = ()) -> tuple[str, list[dict], list[str]]:
    allowed = {e.id for e in retrieved.evidence}
    prompt_input = render(retrieved, question, history)
    raw = llm.complete_json(name="reply_in_character", instructions=config.prompt("chat"),
                            input=prompt_input, schema=CHAT_SCHEMA, max_output_tokens=config.CHAT_MAX_TOKENS,
                            reasoning_effort=config.CHAT_EFFORT)
    statements, unsupported = _check(raw, allowed)
    if unsupported:
        note = "\n".join(f"- {s}" for s in unsupported)
        raw = llm.complete_json(
            name="reply_in_character", instructions=config.prompt("chat"),
            input=(f"{prompt_input}\n\nREWRITE NEEDED\nYour previous reply stated these as facts,"
                   f" but nothing in WHAT THEY KNOW supports them:\n{note}\nWrite the reply again"
                   " without stating them; if they matter, the character is unsure or does not know."),
            schema=CHAT_SCHEMA, max_output_tokens=config.CHAT_MAX_TOKENS, reasoning_effort=config.CHAT_EFFORT)
        statements, unsupported = _check(raw, allowed)
    reply = raw.get("reply") if isinstance(raw.get("reply"), str) else ""
    return reply.strip(), statements, unsupported


def chat(conn: sqlite3.Connection, llm: LLM, book_id: int, character_id: int,
         position: int, question: str, history: Sequence[dict] = ()) -> ChatResult:
    question = question.strip()
    if not question:
        raise ChatError(400, "the question is empty")
    retrieved = retrieve(conn, book_id, character_id, position, question)
    reply, statements, unsupported = compose(llm, retrieved, question, history)
    by_id = {e.id: e for e in retrieved.evidence}
    cited = list(dict.fromkeys(i for s in statements for i in s["evidence_ids"]))
    return ChatResult(
        reply=reply,
        citations=[cite(conn, book_id, by_id[i], position) for i in cited],
        statements=statements,
        unsupported=unsupported,
        voice_lines=[{"utterance_id": l.id, "text": l.text, "available_from": l.available_from}
                     for l in retrieved.lines],
        retrieved_evidence_ids=[e.id for e in retrieved.evidence],
    )
