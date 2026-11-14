"""Split a book's passages into extraction chunks of whole paragraphs.

A chunk covers sentences ``start < s <= through`` (the window ``resolve_chunk``
checks) and carries a few preceding paragraphs as read-only context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .db import Passage

_TERMINAL = ".?!…”’\"')]:;"


@dataclass(frozen=True)
class Chunk:
    start: int
    through: int
    target: tuple[Passage, ...]
    context: tuple[Passage, ...]

    @property
    def passages(self) -> dict[int, Passage]:
        """Everything the model saw, keyed by paragraph_pos, for the resolver."""
        return {p.paragraph_pos: p for p in (*self.context, *self.target)}


def looks_like_heading(passage: Passage) -> bool:
    """Short paragraph that reads like a title: ``Chapter I.``, ``II``, ``PART TWO``."""
    text = passage.text
    if len(text) > 60 or len(passage.sentence_ends) > 2:
        return False
    lowered = text.casefold()
    if lowered.startswith(("chapter", "part ", "book ", "volume", "act ", "scene")):
        return True
    return text[-1] not in _TERMINAL or text.isupper()


def plan_chunks(
    passages: Iterable[Passage],
    after: int = 0,
    *,
    target_chars: int = 3500,
    prefix_chars: int = 1500,
) -> list[Chunk]:
    """Chunks covering every sentence after ``after``, in order.

    A paragraph that straddles ``after`` (only possible if chunking changed
    between runs) is included whole; its earlier sentences are context only,
    because the window starts at ``after``.
    """
    ordered = sorted(passages, key=lambda p: p.paragraph_pos)
    todo = [p for p in ordered if p.last_sentence > after]
    done = [p for p in ordered if p.last_sentence <= after]

    chunks: list[Chunk] = []
    current: list[Passage] = []
    size = 0
    start = after

    def close() -> None:
        nonlocal current, size, start
        if not current:
            return
        context = _context(done, prefix_chars)
        through = current[-1].last_sentence
        chunks.append(Chunk(start, through, tuple(current), tuple(context)))
        done.extend(current)
        start, current, size = through, [], 0

    for passage in todo:
        length = len(passage.text)
        if current:
            full = size + length > target_chars
            heading_break = looks_like_heading(passage) and size >= target_chars // 2
            if full or heading_break:
                close()
        current.append(passage)
        size += length
    close()
    return chunks


def _context(done: list[Passage], prefix_chars: int) -> list[Passage]:
    context: list[Passage] = []
    used = 0
    for passage in reversed(done):
        if context and used + len(passage.text) > prefix_chars:
            break
        if not context and len(passage.text) > prefix_chars:
            break  # one huge paragraph would swamp the prompt
        context.append(passage)
        used += len(passage.text)
    context.reverse()
    return context
