# Fiction Companion

Open a novel, place a bookmark, and interview a character as they are at that
moment in the story. Replies sound like the character and draw only on what
they could know by that page. See the two PDFs in this folder for the brief and
the backend design.

## Frontend

React + TypeScript + Vite, in `frontend/`. It implements the "InCharacter" flow from
the Claude Design handoff: library → reader (the open spread is the bookmark) →
choose a character → conversation with tappable notes → source panel → the same
question at two bookmarks.

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (expects the API on :8000)
VITE_API_URL=http://host:8000 npm run dev   # point at another backend
```

- `src/book.ts` paginates `/passages` into spreads (by estimated lines, breaking
  mid-sentence like print) and finds chapter headings. The bookmark is the last
  sentence *finished* on the open spread; chat uses `min(bookmark, indexed_through)`.
- A conversation is keyed by book, character and bookmark, so switching either
  starts afresh (the brief's rule). Bookmarks are kept in `localStorage`.
- `src/reply.ts` places each statement's citation notes on the reply sentence it
  matches best.

## Backend

Python 3.11+, in `backend/`. One SQLite database (`backend/data/companion.db`)
holds every book; the import worker writes it and the API reads it.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install ebooklib beautifulsoup4 httpx fastapi "uvicorn[standard]" python-multipart pytest
# API key: ADAPTION_LAB_KEY=... in the repo-root .env

.venv/bin/uvicorn companion.api:app --port 8000          # API; docs at http://localhost:8000/docs
.venv/bin/python -m companion.worker ../test_epub/great_gatsby.epub   # import a book
.venv/bin/python -m pytest                               # offline tests (-m live for real-model tests)
```

Importing a book, from the command line or by uploading it to `POST /books`,
always goes through the same steps:

1. **Stays awake.** On macOS it runs `caffeinate` for the whole import. Keep the
   lid open, though: closing it still sleeps the laptop unless it's plugged in
   with an external display.
2. **Resumes after failures.** Each chunk commits together with a checkpoint.
   After an API failure it waits and carries on (5 times), and rerunning the
   same command continues from the checkpoint. If the API server restarts, it
   resumes unfinished uploads by itself.
3. **Gets past hard passages.** If the model runs out of output tokens on a dense
   scene, it uses smaller chunks for that passage, then returns to normal size.
4. **Recovers rejected records.** At the end it re-checks records the resolver
   rejected, using the model answers saved in the extraction log, and keeps any
   that now pass. To do this by hand: `python -m companion.repair --book N`.

5. **Fills in full names.** It adds "First Last" names the text spells out
   ("Jay Gatsby") as aliases, with no model calls. The API's `name` is the fullest
   name known at the bookmark. For a book imported earlier:
   `python -m companion.names --book N [--dry-run]`.

### Reading positions

A **position** is a sentence number, counted from 1 across the whole book. The
reader's bookmark is the last sentence they have finished. Paragraph text
comes from `GET /books/{id}/passages`; sentence `first_sentence + i` of a
paragraph ends at character offset `sentence_ends[i]` (Unicode code points,
i.e. JavaScript `Array.from(text)` indexes, not UTF-16 `text.length`).

Render the book from `/passages` rather than from the raw EPUB so citation
offsets line up with what is on screen.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/books` | `id, title, status, indexed_through, sentences, version` |
| POST | `/books` | multipart `file` = EPUB. Text is ready at once; characters/chat fill in as `indexed_through` grows |
| GET | `/books/{id}` | poll this during an import |
| GET | `/books/{id}/passages?start=1&limit=200` | paged paragraphs; follow `next_start` until `null` |
| GET | `/books/{id}/characters?position=N` | only characters met by `N`, with only the names revealed by then (`names`) |
| POST | `/chat` | see below |
| GET | `/books/{id}/passages/{passage_id}?position=N` | one paragraph cut at the bookmark ("show passage") |

#### POST /chat

```json
{"book_id": 2, "character_id": 22, "position": 400,
 "question": "What did your father tell you?",
 "history": [{"role": "reader", "text": "..."}, {"role": "character", "text": "..."}]}
```

Response:

```json
{"kind": "imagined_reply",
 "character": {"id": 22, "name": "the narrator"},
 "position": 400,
 "reply": "My father—well, he gave me some advice...",
 "citations": [{"evidence_id": 311, "claim": "...", "basis": "was_told", "available_from": 28,
                "sources": [{"passage_id": 12, "paragraph_pos": 26, "text": "<paragraph up to the bookmark>",
                             "truncated": false, "lo": 0, "hi": 120, "quote": "<cited words>"}]}],
 "statements": [{"text": "...", "evidence_ids": [311]}],
 "unsupported_statements": [],
 "voice_lines": [{"utterance_id": 5, "text": "<a real line they spoke>", "available_from": 28}]}
```

- Label the reply as imagined (`kind`). Each citation is a "show passage" target;
  highlight `[lo, hi)` inside `text`.
- `history` is yours to keep. Send `[]` after switching character or moving the
  bookmark, as the brief requires.
- `unsupported_statements` lists anything the model asserted without evidence
  even after a rewrite; usually empty. Consider showing a warning if not.
- `voice_lines` are genuine lines that shaped the voice (for the pitch's "open a
  real line" beat).

Errors: `400` bad request or character not met yet, `404` unknown book/character,
`409` bookmark past `indexed_through` (still importing), `502` model service
failed (retry). Replies take roughly 5–30 seconds.

### Layout

`backend/companion/`: `epub.py` + `sentences.py` (EPUB to passages), `chunker.py`,
`extract.py` + `prompts/` (model passes), `resolve.py` (proves model output
against the text), `commit.py` (checkpointed writes), `voice.py`, `worker.py`
(import pipeline), `chat.py` (retrieval and replies), `api.py` (HTTP).
