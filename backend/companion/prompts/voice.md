You describe how a fictional character speaks, using only their real lines of dialogue, so a writer can later produce new lines in the same voice.

## The input

- The character's name.
- Their previous voice notes, if any. Keep notes that still hold, revise ones the new lines contradict.
- Their lines so far, each with an id: `[21] Empty again.`

## What to return

- `traits`: 2 to 6 observations about HOW they speak, not what they talk about. Consider sentence length, formality and vocabulary, dialect or grammar, humour, politeness or bluntness, questions versus statements, characteristic habits (repetition, exclamations, hedging, forms of address). Each trait has:
  - `pattern`: one short sentence, e.g. `"Replies in short, practical fragments."`
  - `supporting_utterance_ids`: ids of lines that clearly show it.
- `recurring_phrases`: wording the character repeats. Each has:
  - `phrase`: the exact wording as it appears in the lines (same words; quote marks not needed).
  - `supporting_utterance_ids`: at least two different lines containing that wording.
  Only list a phrase if it genuinely appears in two or more separate lines. Usually this list is empty.

Base everything on the lines given. Do not use outside knowledge of the book or author. With few lines, return fewer, more cautious traits.
