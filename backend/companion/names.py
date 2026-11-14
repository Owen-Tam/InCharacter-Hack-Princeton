"""Full names from the text, so the reader sees "Jay Gatsby" rather than "Mr. Gatsby".

The extractor records the names it notices, but often misses a full name that
the text spells out ("Jay Gatsby", "Myrtle Wilson"). This pass finds
"<Given> <Surname>" phrases in the indexed text and adds them as aliases when
they belong unambiguously to one character. No model calls: every alias cites
the words it came from, and becomes available only once the reader could link
it to the character.

    python -m companion.names --book 2 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import config, db
from .commit import add_alias

TITLES = frozenset(
    "mr mrs miss ms mister dr doctor lord lady sir madam madame captain major colonel"
    " professor father mother aunt uncle old".split())
_ARTICLES = frozenset({"the", "a", "an"})
_WORD = re.compile(r"^[A-Z][\w'’-]*$")
_INITIAL = re.compile(r"^[A-Z]\.$")
# Two adjacent capitalized words, e.g. "Jay Gatsby" (or "Jay Gatsby’s": the possessive
# is left out of the match). The look-behind skips "Mr. Gatsby".
_PHRASE = re.compile(r"(?<![\w.’'-])([A-Z][a-z]+) ([A-Z][a-z]+(?:-[A-Z][a-z]+)?)(?![\w-]|[’'][a-rt-z])")
MIN_OCCURRENCES = 2


def classify(name: str) -> tuple[str, str | None, str | None]:
    """("full", given, surname) | ("titled", None, surname) | ("given", given, None)
    | ("descriptive", None, None)."""
    words = name.split()
    if not words or words[0].lower() in _ARTICLES:
        return ("descriptive", None, None)
    if words[0].rstrip(".").lower() in TITLES:
        rest = words[1:]
        if rest and all(_WORD.match(w) or _INITIAL.match(w) for w in rest) and _WORD.match(rest[-1]):
            return ("titled", None, rest[-1])
        return ("descriptive", None, None)
    if not all(_WORD.match(w) or _INITIAL.match(w) for w in words) or any(w.endswith(("'s", "’s")) for w in words):
        return ("descriptive", None, None)
    if len(words) == 1:
        return ("given", words[0], None)
    if _INITIAL.match(words[-1]):
        return ("descriptive", None, None)
    return ("full", words[0], words[-1])


@dataclass
class AddedName:
    character_id: int
    name: str
    available_from: int
    occurrences: int


@dataclass
class _Name:
    character_id: int
    name: str
    available_from: int  # when the reader can tie this name to the character


def _known_names(conn: sqlite3.Connection, book_id: int) -> list[_Name]:
    names = []
    for r in conn.execute("SELECT id, display_name, first_seen, aliases FROM characters WHERE book_id=?",
                          (book_id,)):
        names.append(_Name(r["id"], r["display_name"], r["first_seen"]))
        for a in json.loads(r["aliases"]):
            names.append(_Name(r["id"], a["name"], a.get("available_from", r["first_seen"])))
    return names


def _sentence_at(passage: sqlite3.Row, ends: list[int], offset: int) -> int:
    """The book-wide sentence that contains code point ``offset - 1`` (where a phrase ends)."""
    for i, end in enumerate(ends):
        if offset <= end:
            return passage["first_sentence"] + i
    return passage["first_sentence"] + len(ends) - 1


def find_full_names(conn: sqlite3.Connection, book_id: int, through: int | None = None) -> list[tuple[int, dict, int]]:
    """Candidate (character_id, alias, occurrences) for full names found in the text."""
    if through is None:
        through = conn.execute("SELECT indexed_through FROM books WHERE id=?", (book_id,)).fetchone()[0]
    known = _known_names(conn, book_id)
    names_of: dict[int, set[str]] = defaultdict(set)
    given_of: dict[str, dict[int, int]] = defaultdict(dict)  # given -> {character: revealed at}
    titled_surname: dict[str, dict[int, int]] = defaultdict(dict)  # surname from "Mr. X"
    surname_owners: dict[str, set[int]] = defaultdict(set)  # any name ending in this surname
    full_given: dict[int, set[str]] = defaultdict(set)
    for n in known:
        names_of[n.character_id].add(n.name)
        kind, given, surname = classify(n.name)
        if given:
            prev = given_of[given].get(n.character_id)
            given_of[given][n.character_id] = min(n.available_from, prev) if prev else n.available_from
        if kind == "full":
            full_given[n.character_id].add(given)
        if surname:
            surname_owners[surname].add(n.character_id)
            if kind == "titled":
                prev = titled_surname[surname].get(n.character_id)
                titled_surname[surname][n.character_id] = min(n.available_from, prev) if prev else n.available_from
    every_name = {name for names in names_of.values() for name in names}

    passages = conn.execute("SELECT * FROM passages WHERE book_id=? AND first_sentence<=?"
                            " ORDER BY paragraph_pos", (book_id, through)).fetchall()
    # Words that also appear in lower case are ordinary words ("When Gatsby ..."), not first names.
    lowercase_words = set()
    for p in passages:
        lowercase_words.update(re.findall(r"(?<![\w’'])[a-z]+", p["text"]))

    found: dict[tuple[int, str], dict] = {}
    for p in passages:
        ends = json.loads(p["sentence_ends"])
        for m in _PHRASE.finditer(p["text"]):
            given, surname = m.group(1), m.group(2)
            phrase = f"{given} {surname}"
            if given.lower() in TITLES or given.lower() in lowercase_words or phrase in every_name:
                continue
            at = _sentence_at(p, ends, m.end())
            if at > through:
                continue
            owner, linked_at = None, None
            if len(given_of.get(given, {})) == 1:
                # Given-name path: exactly one character goes by this first name.
                (owner, linked_at), = given_of[given].items()
            elif not given_of.get(given) and len(titled_surname.get(surname, {})) == 1 \
                    and surname_owners[surname] == set(titled_surname[surname]):
                # Surname path: one character is "Mr. <Surname>" and no one else shares it.
                (owner, linked_at), = titled_surname[surname].items()
                if full_given[owner] - {given}:
                    continue  # they already have a different full name: not guessing
            if owner is None or phrase in names_of[owner]:
                continue
            entry = found.setdefault((owner, phrase), {"count": 0, "first": None})
            entry["count"] += 1
            if entry["first"] is None:
                entry["first"] = {
                    "name": phrase,
                    "available_from": max(at, linked_at),
                    "sources": [{"passage_id": p["id"], "lo": m.start(), "hi": m.end(), "role": "support"}],
                }
    return [(owner, e["first"], e["count"]) for (owner, _), e in found.items() if e["count"] >= MIN_OCCURRENCES]


def complete_names(conn: sqlite3.Connection, book_id: int, through: int | None = None,
                   *, dry_run: bool = False) -> list[AddedName]:
    """Add full names found in the text as aliases. Safe to run again: names a
    character already has are skipped. ``dry_run`` reports without writing."""
    candidates = find_full_names(conn, book_id, through)
    added = []
    if dry_run:
        added = [AddedName(owner, alias["name"], alias["available_from"], count)
                 for owner, alias, count in candidates]
    else:
        with db.write_tx(conn):
            for owner, alias, count in candidates:
                if add_alias(conn, book_id, owner, alias):
                    added.append(AddedName(owner, alias["name"], alias["available_from"], count))
    return sorted(added, key=lambda a: (a.available_from, a.character_id))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add full names found in the text as aliases.")
    parser.add_argument("--book", type=int, required=True)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    added = complete_names(conn, args.book, dry_run=args.dry_run)
    names = {r["id"]: r["display_name"] for r in conn.execute(
        "SELECT id, display_name FROM characters WHERE book_id=?", (args.book,))}
    verb = "would add" if args.dry_run else "added"
    print(f"{verb} {len(added)} full names:")
    for a in added:
        print(f"  {names.get(a.character_id, a.character_id)!s:<28} → {a.name}"
              f"  (from sentence {a.available_from}, {a.occurrences}× in the text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
