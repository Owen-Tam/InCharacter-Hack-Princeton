"""Rule-based sentence splitting for canonical paragraph text.

``split_sentences`` returns the exclusive end offset of each sentence, counted
in code points, in the form ``passages.sentence_ends`` stores. An end sits just
after the closing punctuation and any closing quotes or brackets; the space
that follows belongs to the next sentence. The last end is always ``len(text)``.

    Theo stepped outside. Mara hid it.   ->   [21, 34]
"""

from __future__ import annotations

import re

# Terminal punctuation, then any closing quotes/brackets, then whitespace.
_BOUNDARY = re.compile(r"[.?!…]+[”’\"')\]]*(?=\s+(\S))")
_OPENERS = "“‘\"'([—"
_WORD_BEFORE = re.compile(r"(\w+)$")

# Words that end in a full stop without ending the sentence.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "messrs", "mme", "mlle", "dr", "st", "jr", "sr", "rev",
    "capt", "col", "gen", "lt", "sgt", "prof", "hon", "esq", "mt", "vs", "viz",
})


def _is_abbreviation(text: str, punct_start: int) -> bool:
    match = _WORD_BEFORE.search(text, 0, punct_start)
    if match is None:
        return False
    word = match.group(1)
    if word.casefold() in ABBREVIATIONS:
        return True
    # A lone capital other than "I" is an initial, as in "J. R. Smith".
    return len(word) == 1 and word.isupper() and word != "I"


def split_sentences(text: str) -> list[int]:
    """End offsets of each sentence in ``text``; never empty for non-empty text."""
    if not text:
        raise ValueError("cannot split empty text")
    ends: list[int] = []
    for match in _BOUNDARY.finditer(text):
        following = match.group(1)
        if not (following.isupper() or following.isdigit() or following in _OPENERS):
            continue  # “Where?” he asked.  — the sentence carries on
        if match.group(0)[0] == "." and match.group(0).rstrip("”’\"')]") == "." \
                and _is_abbreviation(text, match.start()):
            continue
        ends.append(match.end())
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    return ends
