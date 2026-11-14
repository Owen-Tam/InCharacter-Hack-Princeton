"""Import worker: EPUB in, fully extracted book out. Safe to stop and rerun.

    python -m companion.worker ../test_epub/jackson_lottery.epub --db companion.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, db
from .awake import keep_awake
from .chunker import plan_chunks
from .commit import CheckpointMismatch, commit_chunk
from .epub import import_epub
from .extract import (Extraction, dialogue_pass, knowledge_pass, last_known_claims,
                      merge_candidates)
from .llm import LLM
from .resolve import resolve_chunk
from .voice import VoiceJob, ask_voice, plan_voice_jobs, publish_voice, update_voices


@dataclass
class ImportSummary:
    book_id: int
    title: str
    status: str
    indexed_through: int
    sentences: int
    chunks_run: int = 0
    chunks_left: int = 0
    added: dict = field(default_factory=lambda: {
        "characters": 0, "utterances": 0, "evidence": 0, "snapshots": 0, "rejects": 0})


def _set_status(conn: sqlite3.Connection, book_id: int, status: str) -> None:
    with db.write_tx(conn):
        conn.execute("UPDATE books SET status=? WHERE id=?", (status, book_id))


def _book(conn: sqlite3.Connection, book_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()


def _usage(llm) -> tuple[int, int, float]:
    usage = getattr(llm, "usage", None)
    if usage is None:
        return 0, 0, 0.0
    return usage.input_tokens, usage.output_tokens, usage.credits(getattr(llm, "model", ""))


def run_import(
    conn: sqlite3.Connection,
    epub_path: str | Path,
    *,
    llm: LLM,
    version: str = config.VERSION,
    target_chars: int = 3500,
    prefix_chars: int = 1500,
    max_chunks: int | None = None,
    voice: bool = True,
    log_path: str | Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ImportSummary:
    imported = import_epub(conn, epub_path, version)
    book_id = imported.book_id
    book = _book(conn, book_id)
    summary = ImportSummary(book_id, book["title"], book["status"],
                            book["indexed_through"], imported.sentences)
    if book["status"] == "ready":
        return summary

    passages = db.load_passages(conn, book_id)
    chunks = plan_chunks(passages.values(), book["indexed_through"],
                         target_chars=target_chars, prefix_chars=prefix_chars)
    _set_status(conn, book_id, "indexing")
    say = on_progress or (lambda message: None)
    log = open(log_path, "a", encoding="utf-8") if log_path else None
    if max_chunks is not None:
        chunks_to_run = chunks[:max_chunks]
    else:
        chunks_to_run = chunks
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="llm")
    voice_jobs: list[tuple[VoiceJob, Future]] = []

    def publish_voices() -> list:
        """Publish the snapshots requested after the previous commit."""
        published = []
        for job, future in voice_jobs:
            update = publish_voice(conn, book_id, job, future.result())
            if update:
                published.append(update)
        voice_jobs.clear()
        return published

    def request_voices(through: int) -> None:
        if voice:
            for job in plan_voice_jobs(conn, book_id, through):
                voice_jobs.append((job, pool.submit(ask_voice, llm, job)))

    try:
        if not chunks:  # everything committed; a crash may have skipped the last snapshots
            if voice:
                summary.added["snapshots"] += len(
                    update_voices(conn, llm, book_id, book["indexed_through"]))
            _set_status(conn, book_id, "ready")

        # Pipeline: chunk N's knowledge pass runs alongside chunk N+1's dialogue
        # pass, and voice snapshots run in the background. Commits stay in order.
        pending: list = []  # characters the previous chunk introduced, by key
        next_dialogue = None
        if chunks_to_run:
            next_dialogue = pool.submit(dialogue_pass, llm, chunks_to_run[0],
                                        db.load_characters(conn, book_id))
        for number, chunk in enumerate(chunks_to_run):
            started = time.monotonic()
            before = _usage(llm)
            dialogue = next_dialogue.result()
            new = [c for c in (dialogue.get("characters") or []) if isinstance(c, dict)]
            characters = db.load_characters(conn, book_id)
            knowledge_future = pool.submit(
                knowledge_pass, llm, chunk, characters, new,
                last_known_claims(conn, book_id, chunk.start))
            next_dialogue = None
            if number + 1 < len(chunks_to_run):
                next_dialogue = pool.submit(dialogue_pass, llm, chunks_to_run[number + 1],
                                            characters, new)
            knowledge = knowledge_future.result()

            extraction = Extraction(merge_candidates(dialogue, knowledge, pending),
                                    dialogue, knowledge)
            resolved = resolve_chunk(extraction.candidates, chunk.passages, characters,
                                     chunk.start, chunk.through)
            result = commit_chunk(conn, book_id, resolved, final=number == len(chunks) - 1)
            voices = publish_voices()
            request_voices(chunk.through)
            pending = new
            after = _usage(llm)

            summary.chunks_run += 1
            added = summary.added
            added["characters"] += len(result.character_ids)
            added["utterances"] += len(result.utterance_ids)
            added["evidence"] += len(result.evidence_ids)
            added["snapshots"] += len(voices)
            added["rejects"] += len(resolved.rejects)

            if log:
                log.write(json.dumps({
                    "book_id": book_id, "start": chunk.start, "through": chunk.through,
                    "paragraphs": [p.paragraph_pos for p in chunk.target],
                    "dialogue": extraction.dialogue, "knowledge": extraction.knowledge,
                    "rejects": [r.__dict__ for r in resolved.rejects],
                    "character_ids": result.character_ids,
                    "utterance_ids": result.utterance_ids, "evidence_ids": result.evidence_ids,
                    "snapshots": [v.character_id for v in voices],
                    "tokens": [after[0] - before[0], after[1] - before[1]],
                }, ensure_ascii=False) + "\n")
                log.flush()
            say(f"chunk {number + 1}/{len(chunks)} sentences {chunk.start + 1}-{chunk.through}:"
                f" +{len(result.character_ids)} characters, +{len(result.utterance_ids)} lines,"
                f" +{len(result.evidence_ids)} claims, {len(voices)} snapshots,"
                f" {len(resolved.rejects)} rejected"
                f" | {time.monotonic() - started:.0f}s, {after[2]:.4f} credits so far")
        if voice_jobs:
            final_voices = publish_voices()
            summary.added["snapshots"] += len(final_voices)
            if final_voices:
                say(f"published {len(final_voices)} final voice snapshots")
    except CheckpointMismatch as exc:
        say(f"stopped: {exc} (another worker is probably running)")
    except BaseException:
        _set_status(conn, book_id, "failed")
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if log:
            log.close()

    book = _book(conn, book_id)
    summary.status = book["status"]
    summary.indexed_through = book["indexed_through"]
    summary.chunks_left = len(chunks) - summary.chunks_run if chunks else 0
    return summary


def run_with_resume(run: Callable[[int, int | None], ImportSummary], *, resumes: int,
                    wait: float, target_chars: int = 3500, min_chars: int = 800,
                    detour_chunks: int = 2,
                    say: Callable[[str], None] = print,
                    sleep: Callable[[float], None] = time.sleep) -> ImportSummary:
    """Call ``run(chunk_chars, max_chunks)`` again after an API failure; each call
    resumes at the checkpoint.

    A call that runs out of output tokens (the model sometimes reasons in circles
    on a dense scene) is retried at once with chunks half the size, for
    ``detour_chunks`` chunks only, then the import returns to ``target_chars``.
    This does not count against ``resumes``.
    """
    from .llm import LLMError

    failures = 0
    size = target_chars
    while True:
        detour = size < target_chars
        try:
            summary = run(size, detour_chunks if detour else None)
        except LLMError as exc:
            if "max_output_tokens" in str(exc) and size > min_chars:
                size = max(min_chars, size // 2)
                say(f"Output limit hit; getting past this passage with smaller chunks"
                    f" ({size} characters). Committed chunks are kept.")
                continue
            failures += 1
            if failures > resumes:
                raise
            say(f"API failure: {exc}. Resuming in {wait:.0f}s"
                f" ({failures}/{resumes}); committed chunks are kept.")
            sleep(wait)
            continue
        if detour and summary.status != "ready":
            size = target_chars
            say(f"Past the difficult passage; back to {size}-character chunks.")
            continue
        return summary


def extract_book(
    conn: sqlite3.Connection,
    epub_path: str | Path,
    *,
    llm: LLM,
    version: str = config.VERSION,
    target_chars: int = 3500,
    max_chunks: int | None = None,
    voice: bool = True,
    log_path: Path | None = None,
    resumes: int = 5,
    resume_wait: float = 120.0,
    stay_awake: bool = True,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> ImportSummary:
    """The one way to extract a book; the CLI and the upload endpoint both use it.

    1. Keeps the computer awake (macOS ``caffeinate``) for the whole run.
    2. Resumes from the checkpoint after API failures (``resumes`` times).
    3. Gets past passages that make the model run out of output tokens by using
       smaller chunks briefly, then returns to ``target_chars``.
    4. Recovers previously rejected records from the extraction log (no model
       calls), so resolver improvements reach books imported earlier.
    Snapshots a crash skipped are caught up by ``run_import`` itself.
    """
    from .repair import recover_rejects

    with (keep_awake() if stay_awake else nullcontext(False)) as awake:
        if awake:
            say("Keeping the computer awake until the import finishes.")
        summary = run_with_resume(
            lambda size, limit: run_import(
                conn, epub_path, llm=llm, version=version, target_chars=size,
                max_chunks=limit if limit is not None else max_chunks, voice=voice,
                log_path=log_path, on_progress=say),
            resumes=resumes, wait=resume_wait, target_chars=target_chars,
            say=say, sleep=sleep)

    if log_path is not None and Path(log_path).exists():
        repaired = recover_rejects(conn, summary.book_id, Path(log_path))
        summary.added["evidence"] += repaired.evidence_added
        summary.added["utterances"] += repaired.utterances_added
        if repaired.evidence_added or repaired.utterances_added:
            say(f"Recovered {repaired.evidence_added} claims and {repaired.utterances_added}"
                " lines that had been rejected earlier.")
    return summary


def main(argv: list[str] | None = None) -> int:
    from .llm import AdaptionLLM

    parser = argparse.ArgumentParser(
        description="Extract characters, dialogue and evidence from an EPUB. Keeps the computer"
                    " awake, resumes after API failures, and recovers rejected records at the end.")
    parser.add_argument("epub", type=Path)
    parser.add_argument("--db", type=Path, default=config.DB_PATH,
                        help="shared database for all books (default: backend/data/companion.db)")
    parser.add_argument("--model", default=config.EXTRACT_MODEL)
    parser.add_argument("--version", help=f"defaults to {config.PROMPT_VERSION}/<model>")
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--target-chars", type=int, default=3500)
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--resumes", type=int, default=5,
                        help="times to wait and resume after the API keeps failing")
    parser.add_argument("--resume-wait", type=float, default=120.0)
    args = parser.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.db)
    db.init_schema(conn)
    llm = AdaptionLLM(model=args.model, on_call=print)
    summary = extract_book(
        conn, args.epub, llm=llm,
        version=args.version or f"{config.PROMPT_VERSION}/{args.model}",
        target_chars=args.target_chars, max_chunks=args.max_chunks, voice=not args.no_voice,
        log_path=Path(f"{args.db}.extract_log.jsonl"),
        resumes=args.resumes, resume_wait=args.resume_wait,
    )
    tokens_in, tokens_out, credits = _usage(llm)
    print(f"book {summary.book_id} {summary.title!r}: {summary.status},"
          f" indexed through {summary.indexed_through}/{summary.sentences}")
    print(f"added {summary.added}; {llm.usage.calls} calls,"
          f" {tokens_in} in / {tokens_out} out tokens, {credits:.4f} credits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
