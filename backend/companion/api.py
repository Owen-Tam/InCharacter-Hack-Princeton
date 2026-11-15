"""HTTP API for the reader: books, text, characters, chat and citations.

    cd backend && .venv/bin/uvicorn companion.api:app --reload --port 8000

Then open http://localhost:8000/docs for the interactive endpoint list.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Iterator, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import config, db
from .chat import ChatError, best_name, book_row, main_characters, character_row, chat, check_position, clamp, known_names, load_passage
from .epub import import_epub
from .speech import assigned_voice, synthesize
from .llm import LLM, LLMError

# -- request and response models ----------------------------------------------


class Turn(BaseModel):
    role: Literal["reader", "character"]
    text: str
    evidence_ids: list[int] = Field(default_factory=list, description=(
        "For a character turn: the evidence ids its statements cited, so later"
        " replies can bring up something new."))


class SpeechRequest(BaseModel):
    book_id: int
    character_id: int
    position: int
    text: str = Field(min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    book_id: int
    character_id: int
    position: int = Field(description="Last sentence the reader has finished (book-wide, from 1)")
    question: str
    history: list[Turn] = Field(default_factory=list, description=(
        "Earlier turns with this character at this bookmark. Send [] after switching"
        " character or moving the bookmark."))


ImportRunner = Callable[[Path, int, Path], None]

_running: set[int] = set()
_running_lock = threading.Lock()


def default_import_runner(epub_path: Path, book_id: int, db_path: Path) -> None:
    """Extract in a background thread with its own connection and model client,
    through the same ``extract_book`` path as the command line. One import per
    book at a time: a second upload of a book that is importing does nothing."""
    from .llm import AdaptionLLM
    from .worker import extract_book

    with _running_lock:
        if book_id in _running:
            return
        _running.add(book_id)

    def job() -> None:
        conn = db.connect(db_path)
        try:
            extract_book(conn, epub_path, llm=AdaptionLLM(),
                         log_path=Path(f"{db_path}.extract_log.jsonl"), resume_wait=60,
                         say=lambda message: print(f"[import book {book_id}] {message}"))
        except Exception as exc:  # the book stays 'failed' and resumes next time
            print(f"[import book {book_id}] stopped: {exc}")
        finally:
            conn.close()
            with _running_lock:
                _running.discard(book_id)

    threading.Thread(target=job, name=f"import-book-{book_id}", daemon=True).start()


def resume_unfinished_imports(db_path: Path, runner: ImportRunner) -> list[int]:
    """Restart extraction for uploaded books left unfinished (e.g. the server
    stopped). Uploads are saved under the hash that identifies the book."""
    conn = db.connect(db_path)
    try:
        resumed = []
        for row in conn.execute("SELECT id, epub_hash FROM books WHERE status != 'ready'"):
            path = config.UPLOAD_DIR / f"{row['epub_hash']}.epub"
            if path.exists():
                runner(path, row["id"], db_path)
                resumed.append(row["id"])
        return resumed
    finally:
        conn.close()


def _total_sentences(conn: sqlite3.Connection, book_id: int) -> int:
    row = conn.execute(
        "SELECT coalesce(max(first_sentence + json_array_length(sentence_ends) - 1), 0)"
        " FROM passages WHERE book_id=?", (book_id,)).fetchone()
    return row[0]


def _book_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {"id": row["id"], "title": row["title"], "author": row["author"], "status": row["status"],
            "indexed_through": row["indexed_through"],
            "sentences": _total_sentences(conn, row["id"]), "version": row["version"]}


# -- app ------------------------------------------------------------------------


def create_app(
    db_path: Path | str | None = None,
    chat_llm_factory: Callable[[], LLM] | None = None,
    import_runner: ImportRunner | None = None,
    resume_imports: bool = True,
) -> FastAPI:
    db_path = Path(db_path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    setup = db.connect(db_path)
    db.init_schema(setup)
    setup.close()
    runner = import_runner or default_import_runner
    if resume_imports:
        resume_unfinished_imports(db_path, runner)
    llm_holder: dict = {}

    def get_llm() -> LLM:
        if "llm" not in llm_holder:
            if chat_llm_factory:
                llm_holder["llm"] = chat_llm_factory()
            else:
                from .llm import AdaptionLLM
                llm_holder["llm"] = AdaptionLLM(model=config.CHAT_MODEL,
                                                temperature=config.CHAT_TEMPERATURE)
        return llm_holder["llm"]

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = db.connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    app = FastAPI(title="Fiction Companion API", version="1.0")
    origins = os.environ.get("COMPANION_CORS_ORIGINS", "*").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(ChatError)
    def chat_error(_request, exc: ChatError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(LLMError)
    def model_error(_request, exc: LLMError):
        # The model service failed after retries: a gateway problem, not the reader's.
        return JSONResponse(status_code=502, content={
            "detail": "The character could not answer right now (model service error). Try again.",
            "error": str(exc)[:300]})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/books")
    def list_books(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        """Every imported book. ``indexed_through``: chat works for bookmarks up to here."""
        return [_book_json(conn, r) for r in conn.execute("SELECT * FROM books ORDER BY id")]

    @app.post("/books", status_code=201)
    def upload_book(file: UploadFile = File(...),
                    conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """Upload an EPUB. Text is available at once; extraction runs in the background."""
        data = file.file.read()
        if not data.startswith(b"PK"):
            raise HTTPException(400, "that does not look like an EPUB file")
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = config.UPLOAD_DIR / f"{hashlib.sha256(data).hexdigest()}.epub"
        path.write_bytes(data)
        try:
            imported = import_epub(conn, path, config.VERSION)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:  # unreadable or DRM-protected EPUB
            raise HTTPException(400, "could not read that EPUB") from None
        row = book_row(conn, imported.book_id)
        if row["status"] != "ready":
            runner(path, imported.book_id, db_path)
        return _book_json(conn, row)

    @app.get("/books/{book_id}")
    def get_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return _book_json(conn, book_row(conn, book_id))

    @app.get("/books/{book_id}/passages")
    def get_passages(book_id: int, start: int = Query(1, ge=1),
                     limit: int = Query(200, ge=1, le=1000),
                     conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """The canonical text, paragraph by paragraph. Render this text so citation
        offsets line up. Sentence ``first_sentence + i`` ends at ``sentence_ends[i]``."""
        book_row(conn, book_id)
        rows = conn.execute(
            "SELECT * FROM passages WHERE book_id=? AND paragraph_pos>=? ORDER BY paragraph_pos"
            " LIMIT ?", (book_id, start, limit)).fetchall()
        last = conn.execute("SELECT max(paragraph_pos) FROM passages WHERE book_id=?",
                            (book_id,)).fetchone()[0] or 0
        passages = [{"id": r["id"], "paragraph_pos": r["paragraph_pos"], "href": r["href"],
                     "para_no": r["para_no"], "first_sentence": r["first_sentence"],
                     "sentence_ends": json.loads(r["sentence_ends"]), "text": r["text"]}
                    for r in rows]
        next_start = passages[-1]["paragraph_pos"] + 1 if passages else None
        return {"passages": passages,
                "next_start": next_start if next_start and next_start <= last else None}

    @app.get("/books/{book_id}/characters")
    def get_characters(book_id: int, position: int | None = None, everyone: bool = False,
                       conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        """The main characters the reader has met by ``position`` (default: everything
        indexed), with only the names revealed by then. Who counts as main is decided
        by the whole book; ``everyone=true`` lists minor characters too."""
        book = book_row(conn, book_id)
        if position is None:
            position = book["indexed_through"]
        main = None if everyone else main_characters(conn, book_id)
        out = []
        for r in conn.execute("SELECT * FROM characters WHERE book_id=? AND first_seen<=?"
                              " ORDER BY first_seen, id", (book_id, position)):
            if main is not None and r["id"] not in main:
                continue
            lines, last_heard = conn.execute(
                "SELECT count(*), max(available_from) FROM utterances WHERE book_id=? AND speaker_id=?"
                " AND available_from<=?", (book_id, r["id"], position)).fetchone()
            facts = conn.execute("SELECT count(*) FROM evidence WHERE book_id=? AND character_id=?"
                                 " AND available_from<=?", (book_id, r["id"], position)).fetchone()[0]
            snapshots = [s for s in json.loads(r["voice_snapshots"])
                         if s["through_position"] <= position]
            out.append({"id": r["id"], "name": best_name(r, position),
                        "names": known_names(r, position), "first_seen": r["first_seen"],
                        "has_audio_voice": assigned_voice(book, r) is not None,
                        "lines": lines, "last_heard": last_heard, "facts": facts, "has_voice_profile": bool(snapshots)})
        return out

    @app.get("/books/{book_id}/passages/{passage_id}")
    def get_citation_passage(book_id: int, passage_id: int, position: int,
                             conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """One paragraph, cut off at the reader's bookmark ("show passage")."""
        check_position(book_row(conn, book_id), position)
        return clamp(load_passage(conn, book_id, passage_id), position)

    @app.post("/speech")
    def speech(request: SpeechRequest, conn: sqlite3.Connection = Depends(get_conn)):
        book = book_row(conn, request.book_id)
        check_position(book, request.position)
        character = character_row(conn, request.book_id, request.character_id, request.position)
        voice = assigned_voice(book, character)
        if not voice:
            raise HTTPException(404, "No audio voice is assigned to this character in this book.")
        if not request.text.strip():
            raise HTTPException(400, "Speech text is empty.")
        try:
            audio = synthesize(voice, request.text)
            if not audio:
                raise ValueError("Empty audio")
        except Exception:
            raise HTTPException(502, "Audio could not be generated. Please try again.") from None
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.post("/chat")
    def post_chat(request: ChatRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """An imagined, in-character reply grounded in what the character knows at
        ``position``. Each citation's sources are clamped to the bookmark."""
        result = chat(conn, get_llm(), request.book_id, request.character_id,
                      request.position, request.question,
                      [turn.model_dump() for turn in request.history])
        character = character_row(conn, request.book_id, request.character_id, request.position)
        return {
            "kind": result.kind,
            "character": {"id": character["id"], "name": best_name(character, request.position)},
            "position": request.position,
            "reply": result.reply,
            "citations": result.citations,
            "statements": result.statements,
            "unsupported_statements": result.unsupported,
            "voice_lines": result.voice_lines,
            "retrieved_evidence_ids": result.retrieved_evidence_ids,
        }

    return app


def __getattr__(name: str):
    """``companion.api:app`` for uvicorn, created on first use so that merely
    importing this module (as the tests do) never opens the real database."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
