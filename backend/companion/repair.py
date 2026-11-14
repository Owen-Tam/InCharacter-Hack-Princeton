"""Recover records the resolver rejected, after the resolver has improved.

The extraction log keeps the model's raw answers and which records were
rejected. This re-checks only those records against the current resolver and
inserts the ones that now pass, without any model calls. Rows already in the
database are skipped, so running it twice changes nothing.

    python -m companion.repair --book 2 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db
from .commit import _check_sources
from .resolve import resolve_chunk


@dataclass
class RepairSummary:
    chunks: int = 0
    rejected: int = 0
    evidence_added: int = 0
    utterances_added: int = 0
    skipped_existing: int = 0
    still_rejected: list = field(default_factory=list)


def _entries(log_path: Path, book_id: int):
    with open(log_path, encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if entry.get("book_id") == book_id and entry.get("rejects"):
                yield entry


def recover_rejects(conn: sqlite3.Connection, book_id: int, log_path: Path,
                    *, dry_run: bool = False) -> RepairSummary:
    summary = RepairSummary()
    passages = db.load_passages(conn, book_id)
    characters = db.load_characters(conn, book_id)
    known = {c.id for c in characters}

    with db.write_tx(conn):
        for entry in _entries(log_path, book_id):
            summary.chunks += 1
            source = {
                "aliases": entry["dialogue"].get("aliases") or [],
                "utterances": entry["dialogue"].get("utterances") or [],
                "evidence": entry["knowledge"].get("evidence") or [],
            }
            retry = {"characters": entry["dialogue"].get("characters") or [],
                     "aliases": [], "utterances": [], "evidence": []}
            for reject in entry["rejects"]:
                summary.rejected += 1
                if reject["kind"] in ("utterances", "evidence"):
                    retry[reject["kind"]].append(source[reject["kind"]][reject["index"]])
            if not retry["utterances"] and not retry["evidence"]:
                continue
            # This chunk's own text plus everything before it, as the model saw it.
            window = {pos: p for pos, p in passages.items() if p.first_sentence <= entry["through"]}
            resolved = resolve_chunk(retry, window, characters, entry["start"], entry["through"])
            summary.still_rejected += [
                (entry["start"], r.reason) for r in resolved.rejects if r.kind != "characters"]

            for row in resolved.evidence:
                refs = (row.character, row.reported_by)
                if any(isinstance(r, str) or (r is not None and r not in known) for r in refs):
                    continue  # names a character that was never committed
                exists = conn.execute(
                    "SELECT 1 FROM evidence WHERE book_id=? AND character_id=? AND claim=?"
                    " AND available_from=?",
                    (book_id, row.character, row.claim, row.available_from)).fetchone()
                if exists:
                    summary.skipped_existing += 1
                    continue
                _check_sources(conn, book_id, row.sources)
                if not dry_run:
                    conn.execute(
                        "INSERT INTO evidence (book_id, character_id, claim, basis,"
                        " reported_by_character_id, available_from, sources) VALUES (?,?,?,?,?,?,?)",
                        (book_id, row.character, row.claim, row.basis, row.reported_by,
                         row.available_from, json.dumps(row.sources)))
                summary.evidence_added += 1

            for row in resolved.utterances:
                refs = (row.speaker, row.addressee)
                if any(isinstance(r, str) or (r is not None and r not in known) for r in refs):
                    continue
                if conn.execute("SELECT 1 FROM utterances WHERE book_id=? AND sources=?",
                                (book_id, json.dumps(row.sources))).fetchone():
                    summary.skipped_existing += 1
                    continue
                _check_sources(conn, book_id, row.sources)
                if not dry_run:
                    conn.execute(
                        "INSERT INTO utterances (book_id, speaker_id, addressee_id,"
                        " available_from, sources) VALUES (?,?,?,?,?)",
                        (book_id, row.speaker, row.addressee, row.available_from,
                         json.dumps(row.sources)))
                summary.utterances_added += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover previously rejected records.")
    parser.add_argument("--book", type=int, required=True)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--log", type=Path, help="defaults to <db>.extract_log.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    log_path = args.log or Path(f"{args.db}.extract_log.jsonl")
    s = recover_rejects(conn, args.book, log_path, dry_run=args.dry_run)
    verb = "would add" if args.dry_run else "added"
    print(f"{s.chunks} chunks with rejects, {s.rejected} rejected records:"
          f" {verb} {s.evidence_added} evidence rows and {s.utterances_added} lines;"
          f" {s.skipped_existing} already present; {len(s.still_rejected)} still rejected")
    for start, reason in s.still_rejected[:15]:
        print(f"  after sentence {start}: {reason[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
