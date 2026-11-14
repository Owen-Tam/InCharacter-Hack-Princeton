"""Write voice snapshots from committed dialogue (the model half of piece 6)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from . import config
from .db import Character, load_characters, quote_text
from .llm import LLM
from .resolve import fold
from .snapshots import publish_snapshot

VOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "traits": {"type": "array", "items": {
            "type": "object",
            "properties": {"pattern": {"type": "string"},
                           "supporting_utterance_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["pattern", "supporting_utterance_ids"],
        }},
        "recurring_phrases": {"type": "array", "items": {
            "type": "object",
            "properties": {"phrase": {"type": "string"},
                           "supporting_utterance_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["phrase", "supporting_utterance_ids"],
        }},
    },
    "required": ["traits", "recurring_phrases"],
}


@dataclass(frozen=True)
class VoiceUpdate:
    character_id: int
    snapshot: dict


def _ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    return [i for i in dict.fromkeys(value) if isinstance(i, int) and not isinstance(i, bool)]


def clean_snapshot(raw: dict, lines: dict[int, str]) -> dict:
    """Keep only entries the published snapshot can prove from ``lines``."""
    traits = []
    for entry in raw.get("traits") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
            continue
        ids = [i for i in _ids(entry.get("supporting_utterance_ids")) if i in lines]
        if entry["pattern"].strip() and ids:
            traits.append({"pattern": entry["pattern"].strip(), "supporting_utterance_ids": ids})
    phrases = []
    for entry in raw.get("recurring_phrases") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("phrase"), str):
            continue
        phrase = entry["phrase"].strip().strip("“”\"'")
        needle = fold(phrase).casefold()
        ids = [i for i in _ids(entry.get("supporting_utterance_ids"))
               if i in lines and needle and needle in fold(lines[i]).casefold()]
        if len(ids) >= 2:
            phrases.append({"phrase": phrase, "supporting_utterance_ids": ids})
    return {"traits": traits, "recurring_phrases": phrases}


def _render(character: Character, lines: dict[int, str]) -> str:
    previous = character.voice_snapshots[-1] if character.voice_snapshots else None
    notes = "\n".join(f"- {t['pattern']}" for t in previous["traits"]) if previous else "(none)"
    body = "\n".join(f"[{i}] {text}" for i, text in lines.items())
    return f"CHARACTER: {character.display_name}\n\nPREVIOUS NOTES\n{notes}\n\nLINES\n{body}"


@dataclass(frozen=True)
class VoiceJob:
    """Everything one snapshot request needs, read from the database up front
    so the model call can run on another thread."""
    character_id: int
    through: int
    lines: dict[int, str]
    input: str


def plan_voice_jobs(
    conn: sqlite3.Connection, book_id: int, through: int,
    *, min_new: int = 3, max_lines: int = 40,
) -> list[VoiceJob]:
    """One job per character with at least ``min_new`` lines since their last snapshot."""
    jobs = []
    for character in load_characters(conn, book_id):
        if character.first_seen > through:
            continue
        last = max((s["through_position"] for s in character.voice_snapshots), default=0)
        rows = conn.execute(
            "SELECT id, available_from, sources FROM utterances"
            " WHERE book_id=? AND speaker_id=? AND available_from<=? ORDER BY id",
            (book_id, character.id, through),
        ).fetchall()
        if sum(1 for r in rows if r["available_from"] > last) < min_new:
            continue
        lines = {r["id"]: quote_text(conn, book_id, json.loads(r["sources"]))
                 for r in rows[-max_lines:]}
        jobs.append(VoiceJob(character.id, through, lines, _render(character, lines)))
    return jobs


def ask_voice(llm: LLM, job: VoiceJob) -> dict:
    """The model call for one job. Safe to run off the main thread."""
    return llm.complete_json(
        name="report_voice", instructions=config.prompt("voice"),
        input=job.input, schema=VOICE_SCHEMA, max_output_tokens=8000,
        reasoning_effort=config.VOICE_EFFORT,
    )


def publish_voice(conn: sqlite3.Connection, book_id: int, job: VoiceJob,
                  raw: dict) -> VoiceUpdate | None:
    snapshot = clean_snapshot(raw, job.lines)
    if not snapshot["traits"] and not snapshot["recurring_phrases"]:
        return None
    stored = publish_snapshot(conn, book_id, job.character_id,
                              {"through_position": job.through, **snapshot})
    return VoiceUpdate(job.character_id, stored)


def update_voices(
    conn: sqlite3.Connection, llm: LLM, book_id: int, through: int,
    *, min_new: int = 3, max_lines: int = 40,
) -> list[VoiceUpdate]:
    """Snapshot every character with at least ``min_new`` lines since their last snapshot."""
    updates = []
    for job in plan_voice_jobs(conn, book_id, through, min_new=min_new, max_lines=max_lines):
        update = publish_voice(conn, book_id, job, ask_voice(llm, job))
        if update:
            updates.append(update)
    return updates
