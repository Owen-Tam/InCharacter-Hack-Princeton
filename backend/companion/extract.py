"""Ask the model for one chunk's candidates (piece 4).

Two passes per chunk, as the brief asks for a separate dialogue prompt:
dialogue (characters, aliases, utterances), then knowledge (evidence), which
sees the characters the first pass introduced. The merged result is the
candidate format ``resolve.resolve_chunk`` validates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from . import config
from .chunker import Chunk
from .db import Character
from .llm import LLM

_REF = {"type": ["integer", "string", "null"]}
_SPAN = {
    "type": "object",
    "properties": {
        "para": {"type": "integer"},
        "text": {"type": "string"},
        "occurrence": {"type": "integer"},
    },
    "required": ["para", "text"],
}
_SPANS = {"type": "array", "items": _SPAN}

DIALOGUE_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "name": {"type": "string"},
                           "first_mention": _SPAN},
            "required": ["key", "name", "first_mention"],
        }},
        "aliases": {"type": "array", "items": {
            "type": "object",
            "properties": {"character": _REF, "name": {"type": "string"}, "support": _SPANS},
            "required": ["character", "name", "support"],
        }},
        "utterances": {"type": "array", "items": {
            "type": "object",
            "properties": {"speaker": _REF, "addressee": _REF,
                           "quotes": _SPANS, "attribution": _SPANS},
            "required": ["speaker", "addressee", "quotes", "attribution"],
        }},
    },
    "required": ["characters", "aliases", "utterances"],
}

KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "characters": {"type": "array", "items": _REF},
                "claim": {"type": "string"},
                "basis": {"type": "string", "enum": [
                    "observed", "performed_action", "said", "was_told", "private_thought"]},
                "reported_by": _REF,
                "support": _SPANS,
            },
            "required": ["characters", "claim", "basis", "reported_by", "support"],
        }},
    },
    "required": ["evidence"],
}

# Reasoning tokens count against this too; a dense chunk measured ~19k in total.
MAX_OUTPUT_TOKENS = 32000


@dataclass
class Extraction:
    candidates: dict
    dialogue: dict
    knowledge: dict


def render_text(chunk: Chunk) -> str:
    lines = []
    if chunk.context:
        lines.append("CONTEXT (already processed)")
        lines += [f"[¶{p.paragraph_pos}] {p.text}" for p in chunk.context]
    lines.append("TARGET")
    lines += [f"[¶{p.paragraph_pos}] {p.text}" for p in chunk.target]
    return "\n".join(lines)


def render_registry(characters: Sequence[Character], new: Sequence[dict] = (),
                    last_known: dict[int, str] | None = None,
                    pending: Sequence[dict] = ()) -> str:
    """Known characters, plus (for the knowledge pass) each one's latest recorded claim.

    The latest claim lets the model tell who is still in the scene after the
    text that established it has scrolled out of the chunk's context.
    ``pending`` lists characters the previous section introduced that are not
    committed yet; the model refers to them by key, and ``merge_candidates``
    re-declares them so the resolver maps each key to its committed row.
    """
    lines = []
    for c in characters:
        aliases = [a["name"] for a in c.aliases]
        extra = f" (also called: {', '.join(aliases)})" if aliases else ""
        line = f"- id {c.id}: {c.display_name}{extra}"
        if last_known is not None:
            line += f" | last known: {last_known.get(c.id, 'nothing recorded yet')}"
        lines.append(line)
    for item in pending:
        if isinstance(item, dict) and item.get("key"):
            lines.append(f"- key \"{item['key']}\": {item.get('name', '')}"
                         " (from the previous section; refer to them by this key)")
    for item in new:
        if isinstance(item, dict) and item.get("key"):
            lines.append(f"- key \"{item['key']}\": {item.get('name', '')} (new in this section)")
    return "\n".join(lines) if lines else "(none yet)"


def _list(value) -> list:
    return value if isinstance(value, list) else []


def last_known_claims(conn: sqlite3.Connection, book_id: int, through: int) -> dict[int, str]:
    """Each character's most recent claim readable by sentence ``through``."""
    rows = conn.execute(
        "SELECT character_id, claim, available_from FROM evidence"
        " WHERE book_id=? AND available_from<=? ORDER BY available_from, id",
        (book_id, through),
    )
    return {r["character_id"]: f"(sentence {r['available_from']}) {r['claim']}" for r in rows}


def dialogue_pass(llm: LLM, chunk: Chunk, characters: Sequence[Character],
                  pending: Sequence[dict] = (),
                  effort: str | None = config.DIALOGUE_EFFORT) -> dict:
    """Pass A: characters, aliases and spoken lines."""
    registry = render_registry(characters, pending=pending)
    return llm.complete_json(
        name="report_dialogue",
        instructions=config.prompt("dialogue"),
        input=f"KNOWN CHARACTERS\n{registry}\n\n{render_text(chunk)}",
        schema=DIALOGUE_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning_effort=effort,
    )


def knowledge_pass(llm: LLM, chunk: Chunk, characters: Sequence[Character],
                   new: Sequence[dict], last_known: dict[int, str] | None = None,
                   effort: str | None = config.KNOWLEDGE_EFFORT) -> dict:
    """Pass B: evidence, seeing pass A's new characters and who was last seen where."""
    registry = render_registry(characters, new, last_known or {})
    return llm.complete_json(
        name="report_evidence",
        instructions=config.prompt("knowledge"),
        input=f"CHARACTERS\n{registry}\n\n{render_text(chunk)}",
        schema=KNOWLEDGE_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning_effort=effort,
    )


def merge_candidates(dialogue: dict, knowledge: dict, pending: Sequence[dict] = ()) -> dict:
    """One candidates dict for the resolver.

    ``pending`` characters (introduced by the previous chunk, which the model
    referred to by key) are declared first. By the time this chunk resolves
    they are committed, so the resolver maps each one to its row by name.
    """
    carried = [p for p in pending if isinstance(p, dict) and p.get("key")]
    carried_keys = {p["key"] for p in carried}
    new = [c for c in _list(dialogue.get("characters"))
           if not (isinstance(c, dict) and c.get("key") in carried_keys)]
    return {
        "characters": carried + new,
        "aliases": _list(dialogue.get("aliases")),
        "utterances": _list(dialogue.get("utterances")),
        "evidence": _list(knowledge.get("evidence")),
    }


def extract_chunk(
    llm: LLM, chunk: Chunk, characters: Sequence[Character],
    *, last_known: dict[int, str] | None = None,
    dialogue_effort: str | None = config.DIALOGUE_EFFORT,
    knowledge_effort: str | None = config.KNOWLEDGE_EFFORT,
) -> Extraction:
    """Both passes, one after the other, for a single chunk."""
    dialogue = dialogue_pass(llm, chunk, characters, effort=dialogue_effort)
    new = _list(dialogue.get("characters"))
    knowledge = knowledge_pass(llm, chunk, characters, new, last_known, effort=knowledge_effort)
    return Extraction(merge_candidates(dialogue, knowledge), dialogue, knowledge)
