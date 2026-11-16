# :books: InCharacter

*Step into the story. Start a conversation.*

InCharacter lets you open an EPUB, read to any page, and talk to a character as
they are at that moment. Replies stay in character, use only what that
character could know by your bookmark, and cite the passages they rely on.
Built at HackPrinceton Fall 2026.

## :magic_wand: Technologies
- React 19, TypeScript, Vite
- Python, FastAPI, SQLite
- DeepSeek V4 Flash (extraction) and Qwen 3.5 397B (chat), both served through Adaption Labs
- ElevenLabs (spoken replies)

## :sparkles: Features
- Spoiler-safe chat: characters only know what has happened up to your bookmark
- Tappable citation notes on every reply that open the source passage
- A cast page that shows only the characters you've met, with only the names revealed so far
- Compare mode: ask the same question at two bookmarks, side by side
- Character voices shaped by their real lines, plus optional spoken replies
- A two-page reader with dark and light themes, a contrast setting, and font sizes from 14 to 22 px
- Checkpointed EPUB import that resumes after a crash

## :brain: Process
We wanted to talk to characters from a book without the risk of spoilers or
made-up facts. Each EPUB is split into numbered sentences. DeepSeek V4 Flash
then reads it in chunks, with one pass for who said what and another for what
each character learned. Every claim has to quote a span, and we check that span
against the text, so unquoted claims are rejected. At chat time, the evidence is
filtered to `position <= bookmark` and ranked. Qwen 3.5 397B writes the reply,
and a final check rewrites or flags any statement that the evidence doesn't
support. *The model proposes, the book decides.*

## :vertical_traffic_light: Running the project
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

`backend/data/companion.db` ships as a committed snapshot with books already
extracted, so chat works without importing anything. To add a book:

```bash
.venv/bin/python -m companion.worker ../test_epub/great_gatsby.epub   # or upload via POST /books
.venv/bin/python -m pytest            # offline tests; add -m live for real-model tests
```

## :gear: How it works

**Frontend** (`frontend/`): hash routes for the library (`#/`), reader
(`#/book/:id`), cast (`…/cast`), chat (`…/talk/:cid`) and compare
(`…/compare`). The bookmark is the last sentence finished on the open spread,
and it's saved in `localStorage`.

**Backend** (`backend/companion/`): FastAPI with a single SQLite database. The
import worker writes to it and the API reads from it.

| Module | Role |
|---|---|
| `epub.py`, `sentences.py` | EPUB to paragraphs and numbered sentences |
| `chunker.py`, `extract.py`, `prompts/` | model passes for dialogue, knowledge, and voice |
| `resolve.py` | checks model output against the text |
| `commit.py`, `worker.py` | checkpointed import pipeline (retries, adaptive chunk size) |
| `repair.py`, `names.py` | re-checks rejected records; full-name aliases |
| `chat.py` | retrieval, reply, and citation check |
| `speech.py` | ElevenLabs voices for assigned characters |
| `api.py` | HTTP |

A position is a sentence number counted from 1 across the book. Offsets are in
code points, not UTF-16 units, so render from `/passages` and not from the raw
EPUB.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| GET / POST | `/books` | list books, or upload an EPUB (multipart `file`) |
| GET | `/books/{id}` | poll this during an import (`indexed_through`) |
| GET | `/books/{id}/passages?start=1&limit=200` | paged paragraphs; follow `next_start` |
| GET | `/books/{id}/characters?position=N` | characters met by `N` |
| GET | `/books/{id}/passages/{pid}?position=N` | one paragraph cut at the bookmark |
| POST | `/chat` | `{book_id, character_id, position, question, history}` |
| POST | `/speech` | `{book_id, character_id, position, text}`, returns MP3 audio |

A `/chat` response contains `reply`, `citations` (each with a highlight range
`[lo, hi)`), `statements` mapped to their evidence, `unsupported_statements`,
and `voice_lines`. The error codes:
- `400`: bad request, or a character you haven't met yet
- `404`: unknown ID
- `409`: bookmark past `indexed_through`
- `502`: the model failed; retry

Replies take about 5 to 30 seconds.

## :movie_camera: Preview
[Demo video on YouTube](https://www.youtube.com/watch?v=zOaOIB_14Tk)

## :warning: Notes
- Anne Frank's voice is generated with ElevenLabs. It isn't a historical recording.
- `test_epub/` holds sample books for local testing. *The Great Gatsby* is in
  the US public domain, but the others are still under copyright, so don't
  redistribute them.

## :busts_in_silhouette: Team
- Emily Mei
- Emily Fung
- Owen Tam
- Kelun Li
