You extract spoken dialogue from a section of a novel or story so that each character's real lines can later be used as voice examples. Accuracy matters more than coverage: a missing line is fine, a misattributed line is not.

## The input

1. KNOWN CHARACTERS: people found in earlier sections, each with a numeric id and any aliases.
2. The text, one paragraph per line, each starting with its paragraph number, e.g. `[¶12] ...`.
   - CONTEXT paragraphs were already processed. Read them to understand who is present and who is speaking, but do not report anything that is complete within CONTEXT.
   - TARGET paragraphs are the ones to process. Report every spoken line that ends inside TARGET, even if it began in CONTEXT.

## Citing text

Every piece of text you report is a span: `{"para": <paragraph number>, "text": "<exact words>"}`.
- `text` must be copied character for character from that paragraph: same spelling, punctuation and capitalisation, including typos and odd punctuation in the source. Never fix, paraphrase, shorten with "...", or join words from two paragraphs.
- If those exact words appear more than once in the paragraph, add `"occurrence": n` (1 for the first appearance, 2 for the second, ...). Prefer citing a longer, unique stretch instead.

## What to return

### characters
People who first appear in this section and are not already in KNOWN CHARACTERS: anyone who speaks, is spoken to, or acts in the story and is identified by a name or a unique description ("the postmaster").
- `key`: a short lowercase id you invent, e.g. `"mara"`. Use it to refer to this person elsewhere in your answer.
- `name`: the most specific name the text has given so far, e.g. `"Mr. Summers"`.
- `first_mention`: a span containing their first mention in the text you were given.
- Do not create characters for groups ("the crowd", "several people") or for anonymous one-off speakers ("a woman", "a voice", "someone").
- Do not create a new character for someone already in KNOWN CHARACTERS under another name; add an alias instead.
- Skip text that is not part of the story itself (title pages, licences, tables of contents, prefaces, notes, study or discussion questions). For such text return empty lists.

### aliases
Another name for a known or new character, only when the text itself makes the link clear (for example, a character is addressed by first name while the narration calls her by surname).
- `character`: the numeric id of a known character, or the `key` of a new one.
- `name`: the other name exactly as written.
- `support`: spans showing the connection.
- If you are not sure two names are the same person, leave them separate.

### utterances
One entry per spoken turn: a continuous stretch of speech by one speaker, even if a speech tag interrupts it.
- `quotes`: the spoken words, in reading order, as spans. Do not include the quotation marks. When a tag interrupts the speech (`"Well," he said, "maybe."`), give each piece as its own span in order.
- `attribution`: spans of the words that tell you who spoke, e.g. `"Mr. Summers said"`. Use an empty list if the speaker is clear only from turn-taking.
- `speaker`: id of a known character, `key` of a new one, or `null`.
- `addressee`: who is being spoken to, or `null` when unclear or when speaking to a group.
- Set `speaker` only when the text makes it certain: an explicit tag, or an unambiguous two-person exchange. If the speaker is anonymous ("a voice said") or you would be guessing, use `null` and still report the line.
- Include only speech said aloud in the story. Skip written notes, songs, sayings quoted inside speech, and thoughts.

## Example

Text:
```
TARGET
[¶1] Theo stepped outside. Mara hid the letter in the atlas. Theo returned and said, “Empty again.” Mara showed him the letter inside the atlas.
```

Answer:
```json
{
  "characters": [
    {"key": "theo", "name": "Theo", "first_mention": {"para": 1, "text": "Theo stepped outside"}},
    {"key": "mara", "name": "Mara", "first_mention": {"para": 1, "text": "Mara hid"}}
  ],
  "aliases": [],
  "utterances": [
    {"speaker": "theo", "addressee": null,
     "quotes": [{"para": 1, "text": "Empty again."}],
     "attribution": [{"para": 1, "text": "Theo returned and said"}]}
  ]
}
```
