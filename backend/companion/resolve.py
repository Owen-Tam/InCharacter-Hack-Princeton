"""Turn extractor candidates into validated rows. Pure: no database, no model.

The extractor proposes verbatim strings. This module proves them against the
stored paragraphs and derives every number (offsets, ``available_from``,
``first_seen``) itself.

Candidate format for one chunk covering sentences ``start < s <= through``::

    {
      "characters": [   # only characters not already in the registry
        {"key": "theo", "name": "Theo",
         "first_mention": {"para": 1, "text": "Theo stepped outside"}}
      ],
      "aliases": [
        {"character": 7, "name": "the stranger", "support": [SPAN, ...]}
      ],
      "utterances": [
        {"speaker": "theo", "addressee": null,
         "quotes": [SPAN, ...],          # in reading order, at least one
         "attribution": [SPAN, ...]}     # optional
      ],
      "evidence": [
        {"character": "theo", "claim": "Theo was shown the letter.",
         "basis": "observed", "reported_by": null, "support": [SPAN, ...]}
      ]
    }

SPAN is ``{"para": paragraph_pos, "text": "verbatim words", "occurrence": 2}``.
``occurrence`` (1-based) is only needed when the words repeat in the paragraph.

A character reference is an integer id from the registry, a string ``key``
declared under ``characters`` in the same candidates, or null where allowed.

A bad record is rejected on its own and reported in ``ResolvedChunk.rejects``;
the rest of the chunk still resolves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence, Union

from .db import Character, Passage

BASES = ("observed", "performed_action", "said", "was_told", "private_thought")

Ref = Union[int, str]  # existing characters.id, or the key of a character new in this chunk


class CandidateError(ValueError):
    """The candidates are not shaped like candidates at all."""


class Rejected(ValueError):
    """One candidate record cannot be proven against the text."""


@dataclass(frozen=True)
class Reject:
    kind: str
    index: int
    reason: str


@dataclass(frozen=True)
class NewCharacter:
    key: str
    display_name: str
    first_seen: int


@dataclass(frozen=True)
class AliasRow:
    character: Ref
    alias: dict  # {"name", "available_from", "sources"}


@dataclass(frozen=True)
class UtteranceRow:
    speaker: Ref | None
    addressee: Ref | None
    available_from: int
    sources: list


@dataclass(frozen=True)
class EvidenceRow:
    character: Ref
    claim: str
    basis: str
    reported_by: Ref | None
    available_from: int
    sources: list


@dataclass
class ResolvedChunk:
    start: int
    through: int
    characters: list[NewCharacter] = field(default_factory=list)
    aliases: list[AliasRow] = field(default_factory=list)
    utterances: list[UtteranceRow] = field(default_factory=list)
    evidence: list[EvidenceRow] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)


# One-to-one character folds, so a match in folded text has the same offsets.
_FOLD = str.maketrans({
    "‘": "'", "’": "'", "′": "'",
    "“": '"', "”": '"', "″": '"',
    " ": " ", "–": "-", "—": "-",
})


def fold(text: str) -> str:
    """Normalise quote marks, dashes and non-breaking spaces without changing length."""
    return text.translate(_FOLD)


def _find_all(haystack: str, needle: str) -> list[int]:
    hits, at = [], haystack.find(needle)
    while at != -1:
        hits.append(at)
        at = haystack.find(needle, at + 1)
    return hits


def locate(haystack: str, needle: str, occurrence: int | None = None) -> tuple[int, int]:
    """Find ``needle`` in ``haystack`` and return its ``[lo, hi)`` code-point range.

    Tries an exact match first, then one that ignores curly/straight quote and
    dash differences. Repeated text needs ``occurrence`` to pick one.
    """
    if not needle:
        raise Rejected("span text is empty")
    hits = _find_all(haystack, needle) or _find_all(fold(haystack), fold(needle))
    if not hits:
        raise Rejected(f"text not found in the paragraph: {needle[:60]!r}")
    if occurrence is None:
        if len(hits) > 1:
            raise Rejected(
                f"text appears {len(hits)} times in the paragraph; set occurrence: {needle[:60]!r}"
            )
        lo = hits[0]
    else:
        if isinstance(occurrence, bool) or not isinstance(occurrence, int) \
                or not 1 <= occurrence <= len(hits):
            raise Rejected(f"occurrence {occurrence!r} is not one of the {len(hits)} matches")
        lo = hits[occurrence - 1]
    return lo, lo + len(needle)


def _unquote(text: str) -> str:
    """Drop the quotation marks around a quote so the range covers the words only."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "‘'" and text[-1] in "’'":
        text = text[1:-1]
    return text.strip('“”"').strip()


def _name_key(name: str) -> str:
    return " ".join(fold(name).casefold().split())


def _text(item: Mapping, name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"{name} must be a non-empty string")
    return value.strip()


class _Resolver:
    def __init__(self, passages, characters, start, through):
        self.passages = passages
        self.start = start
        self.through = through
        self.known_ids = {c.id for c in characters}
        self.names: dict[str, Ref] = {}
        for c in characters:
            self.names.setdefault(_name_key(c.display_name), c.id)
            for alias in c.aliases:
                self.names.setdefault(_name_key(alias["name"]), c.id)
        self.keys: dict[str, Ref] = {}
        self.out = ResolvedChunk(start, through)

    def run(self, kind: str, items: list, handler) -> None:
        for index, item in enumerate(items):
            try:
                if not isinstance(item, dict):
                    raise Rejected("record must be an object")
                handler(item)
            except Rejected as exc:
                self.out.rejects.append(Reject(kind, index, str(exc)))

    # -- shared pieces ------------------------------------------------------

    def ref(self, value, name: str, required: bool = False) -> Ref | None:
        if value is None:
            if required:
                raise Rejected(f"{name} is required")
            return None
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise Rejected(f"{name} must be a character id, a key, or null")
        if isinstance(value, int):
            if value not in self.known_ids:
                raise Rejected(f"{name}: no character with id {value} in this book")
            return value
        if value not in self.keys:
            raise Rejected(f"{name}: unknown character key {value!r}")
        return self.keys[value]

    def span(self, item, role: str, unquote: bool = False) -> tuple[dict, int]:
        if not isinstance(item, dict):
            raise Rejected("span must be an object with para and text")
        para = item.get("para")
        if isinstance(para, bool) or not isinstance(para, int):
            raise Rejected("span para must be a paragraph number")
        passage = self.passages.get(para)
        if passage is None:
            raise Rejected(f"paragraph {para} is not part of this chunk's text")
        text = item.get("text")
        if not isinstance(text, str):
            raise Rejected("span text must be a string")
        needle = _unquote(text) if unquote else text.strip()
        lo, hi = locate(passage.text, needle, item.get("occurrence"))
        sentence = passage.sentence_at(hi)
        if sentence > self.through:
            raise Rejected(f"cites sentence {sentence}, past the chunk end {self.through}")
        return {"passage_id": passage.id, "lo": lo, "hi": hi, "role": role}, sentence

    def spans(self, items, role: str, name: str, required: bool = True,
              unquote: bool = False) -> tuple[list, int]:
        if items is None:
            items = []
        if not isinstance(items, list):
            raise Rejected(f"{name} must be a list of spans")
        if required and not items:
            raise Rejected(f"{name} needs at least one span")
        sources, latest = [], 0
        for item in items:
            source, sentence = self.span(item, role, unquote)
            sources.append(source)
            latest = max(latest, sentence)
        return sources, latest

    def available(self, sentence: int) -> int:
        if sentence <= self.start:
            raise Rejected(
                f"fully readable by sentence {sentence}, which an earlier chunk"
                f" (through {self.start}) already covers"
            )
        return sentence

    # -- record kinds -------------------------------------------------------

    def character(self, item: dict) -> None:
        key = _text(item, "key")
        if key in self.keys:
            raise Rejected(f"duplicate character key {key!r}")
        name = _text(item, "name")
        existing = self.names.get(_name_key(name))
        if existing is not None:  # already known: point the key at it, add no row
            self.keys[key] = existing
            return
        _, first_seen = self.span(item.get("first_mention"), "support")
        self.out.characters.append(NewCharacter(key, name, first_seen))
        self.keys[key] = key
        self.names[_name_key(name)] = key

    def alias(self, item: dict) -> None:
        character = self.ref(item.get("character"), "character", required=True)
        name = _text(item, "name")
        owner = self.names.get(_name_key(name))
        if owner == character:
            return  # nothing new
        if owner is not None:
            raise Rejected(f"name {name!r} already belongs to another character")
        sources, latest = self.spans(item.get("support"), "support", "support")
        self.out.aliases.append(AliasRow(character, {
            "name": name, "available_from": self.available(latest), "sources": sources,
        }))
        self.names[_name_key(name)] = character

    def utterance(self, item: dict) -> None:
        speaker = self.ref(item.get("speaker"), "speaker")
        addressee = self.ref(item.get("addressee"), "addressee")
        quotes, latest_quote = self.spans(item.get("quotes"), "quote", "quotes", unquote=True)
        tags, latest_tag = self.spans(
            item.get("attribution"), "attribution", "attribution", required=False
        )
        available = self.available(max(latest_quote, latest_tag))
        self.out.utterances.append(UtteranceRow(speaker, addressee, available, quotes + tags))

    def evidence(self, item: dict) -> None:
        character = self.ref(item.get("character"), "character", required=True)
        claim = _text(item, "claim")
        basis = item.get("basis")
        if basis not in BASES:
            raise Rejected(f"basis must be one of {', '.join(BASES)}")
        reported_by = self.ref(item.get("reported_by"), "reported_by")
        sources, latest = self.spans(item.get("support"), "support", "support")
        self.out.evidence.append(EvidenceRow(
            character, claim, basis, reported_by, self.available(latest), sources
        ))


def resolve_chunk(
    candidates: Mapping,
    passages: Mapping[int, Passage],
    characters: Sequence[Character],
    start: int,
    through: int,
) -> ResolvedChunk:
    """Validate one chunk's candidates against the text.

    ``passages`` is keyed by paragraph_pos and should hold the chunk plus any
    context prefix the extractor saw. ``characters`` is the book's registry so
    far. The chunk covers sentences ``start < s <= through``; a record must
    become readable inside that window, though it may also cite earlier context.
    """
    if not isinstance(candidates, Mapping):
        raise CandidateError("candidates must be a JSON object")
    for bound in (start, through):
        if isinstance(bound, bool) or not isinstance(bound, int):
            raise ValueError("start and through must be integers")
    if not 0 <= start < through:
        raise ValueError(f"empty or negative chunk window ({start}, {through}]")

    lists = {}
    for name in ("characters", "aliases", "utterances", "evidence"):
        value = candidates.get(name)
        if value is None:
            value = []
        if not isinstance(value, list):
            raise CandidateError(f"{name} must be a list")
        lists[name] = value

    resolver = _Resolver(passages, characters, start, through)
    resolver.run("characters", lists["characters"], resolver.character)
    resolver.run("aliases", lists["aliases"], resolver.alias)
    resolver.run("utterances", lists["utterances"], resolver.utterance)
    resolver.run("evidence", lists["evidence"], resolver.evidence)
    return resolver.out
