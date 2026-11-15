# InCharacter

*Step into the story. Start a conversation.*

Open an EPUB, read to any page, and talk to a character as they are at that
moment. Replies stay in character, use only what that character could know by
your bookmark, and cite the passages they rely on. Built at HackPrinceton Fall 2026.

**How it works:** the EPUB is split into numbered sentences. DeepSeek V4 Flash
reads it in chunks: one pass for who said what, another for what each character
learned. Every claim has to quote a span that's verified against the text.
At chat time, the evidence is filtered to `position <= bookmark` and ranked.
Qwen 3.5 397B (both served through Adaption Labs) writes the reply. Unsupported
statements get rewritten or flagged. *The model proposes, the book decides.*

## Quick start

Needs Python 3.11+ and Node 18+. Put the keys in a repo-root `.env`:

```
ADAPTION_LAB_KEY=...      # required: extraction and chat
ELEVENLABS_API_KEY=...    # optional: spoken replies
```

```bash
# backend → http://localhost:8000 (docs at /docs)
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn companion.api:app --port 8000

# frontend → http://localhost:5173
cd frontend && npm install && npm run dev
# VITE_API_URL=http://host:8000 npm run dev   to point at another backend
```

`backend/data/companion.db` ships as a committed snapshot, with books already
extracted, so chat works with no import. To add a book:

```bash
.venv/bin/python -m companion.worker ../test_epub/great_gatsby.epub   # or upload via POST /books
.venv/bin/python -m pytest            # offline tests; add -m live for real-model tests
```

## Frontend (`frontend/`)

React 19, TypeScript, and Vite. The hash routes are library `#/`, reader
`#/book/:id`, cast `#/book/:id/cast`, chat `#/book/:id/talk/:cid`, and compare
`…/compare`.

- **Reader:** two-page spreads paginated from `/passages` (`src/book.ts`). The
  bookmark is the last sentence finished on the open spread. Chat uses
  `min(bookmark, indexed_through)`. Bookmarks are saved in `localStorage`.
- **Cast:** shows only characters met by the bookmark, with only the names
  revealed by then.
- **Conversation:** replies have tappable citation notes, placed on the
  sentence they support (`src/reply.ts`), that open the source panel. A chat is
  keyed by book, character, and bookmark, so changing either one starts fresh.
- **Compare:** the same question at two bookmarks, side by side.
- **Settings:** dark or light theme, contrast, and book font size from 14 to 22 px (`src/store.ts`).
- **Speech:** an on-demand voice button for assigned characters (`SpeechButton.tsx`).

## Backend (`backend/companion/`)

FastAPI and SQLite. One database holds every book. The import worker writes it
and the API reads it.

| Module | Role |
|---|---|
| `epub.py`, `sentences.py` | EPUB to paragraphs and numbered sentences |
| `chunker.py`, `extract.py`, `prompts/` | model passes for dialogue, knowledge, and voice |
| `resolve.py` | checks model output against the text and rejects unquoted claims |
| `commit.py`, `worker.py` | checkpointed import pipeline |
| `voice.py`, `snapshots.py` | voice traits backed by real lines |
| `names.py` | full-name aliases ("Jay Gatsby") with no model calls |
| `repair.py` | re-checks rejected records from the extraction log |
| `chat.py` | retrieval, reply, and citation check |
| `speech.py` | ElevenLabs voices for explicitly assigned book and character pairs |
| `api.py` | HTTP |

**Imports:**
- They keep the Mac awake with `caffeinate`.
- Each chunk commits with a checkpoint, and API errors get retried up to 5 times.
  Rerunning a command, or restarting the API, resumes from the checkpoint.
- If a passage is too dense, the import shrinks its chunk size and keeps going.
- It finishes with a repair pass and the full-name pass. To run those by hand:
  `python -m companion.repair --book N` and `python -m companion.names --book N [--dry-run]`.

**Positions:** a position is a sentence number counted from 1 across the book.
In `/passages`, sentence `first_sentence + i` ends at `sentence_ends[i]`. That's
measured in code points (JS `Array.from(text)`), not UTF-16 length. Render from
`/passages`, not the raw EPUB, so citation offsets line up.

### API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| GET / POST | `/books` | list books, or upload an EPUB (multipart `file`). Text is ready at once, and characters fill in as `indexed_through` grows |
| GET | `/books/{id}` | poll this during an import |
| GET | `/books/{id}/passages?start=1&limit=200` | paged paragraphs; follow `next_start` |
| GET | `/books/{id}/characters?position=N` | characters met by `N` |
| GET | `/books/{id}/passages/{pid}?position=N` | one paragraph cut at the bookmark |
| POST | `/chat` | `{book_id, character_id, position, question, history}` |
| POST | `/speech` | `{book_id, character_id, position, text}` returns MP3 audio. Assigned voices only (404 otherwise) |

A `/chat` response contains:
- `reply`, labeled `kind: "imagined_reply"`
- `citations`, each with a source paragraph and a highlight range `[lo, hi)`
- `statements`, each mapped to its `evidence_ids`
- `unsupported_statements`, usually empty
- `voice_lines`, the real quotes that shaped the voice

Send `history: []` after changing the character or the bookmark.

Errors are `400` for a bad request or a character not met yet, `404` for an
unknown ID, `409` for a bookmark past `indexed_through`, and `502` when the
model fails (retry). Replies take about 5 to 30 seconds.

## Notes

- Anne Frank has a generated voice (ElevenLabs), not a historical recording.
- `test_epub/` holds sample books for local testing. *The Great Gatsby* is in
  the US public domain, but the others are still under copyright, so don't
  redistribute them.

## Team Members
- Emily Mei
- Emily Fung
- Owen Tam
- 
