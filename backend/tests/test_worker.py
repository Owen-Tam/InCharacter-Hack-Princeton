import json

import pytest

from companion import db
from companion.worker import main, run_import
from helpers import (EVIDENCE_SQL, FIXTURE_HTML, UTTERANCE_SQL, FakeLLM, counts,
                     fixture_candidates, make_epub)

MORE = "<p>Later that day it rained.</p><p>Nobody spoke.</p><p>The end.</p>"


def remap(records, para):
    for record in records:
        for key in ("quotes", "attribution", "support"):
            for span in record.get(key) or []:
                span["para"] = para
        if "first_mention" in record:
            record["first_mention"]["para"] = para
    return records


def in_target(prompt_input, para):
    return f"[¶{para}]" in prompt_input.split("TARGET", 1)[1]


def fixture_llm(**kwargs):
    """Answers with the fixture only when its paragraph (¶2) is in TARGET."""
    def dialogue(prompt_input):
        if not in_target(prompt_input, 2):
            return {"characters": [], "aliases": [], "utterances": []}
        c = fixture_candidates()
        return {"characters": remap(c["characters"], 2), "aliases": [],
                "utterances": remap(c["utterances"], 2)}

    def knowledge(prompt_input):
        if not in_target(prompt_input, 2):
            return {"evidence": []}
        return {"evidence": remap(fixture_candidates()["evidence"], 2)}

    return FakeLLM({"report_dialogue": dialogue, "report_evidence": knowledge}, **kwargs)


@pytest.fixture
def epub_path(tmp_path):
    return make_epub(tmp_path / "letter.epub", [
        ("Text/ch01.xhtml", FIXTURE_HTML), ("Text/ch02.xhtml", MORE)])


def ids(conn):
    rows = {r["display_name"]: r["id"] for r in conn.execute("SELECT * FROM characters")}
    return rows["Theo"], rows["Mara"]


def claims(conn, who, pos):
    return [r["claim"] for r in conn.execute(EVIDENCE_SQL, {"book": 1, "who": who, "pos": pos})]


def test_full_import_satisfies_the_fixture_checks(conn, epub_path, tmp_path):
    log = tmp_path / "log.jsonl"
    messages = []
    summary_llm = fixture_llm()
    # small chunks so later chunks see the fixture's claims as "last known"
    summary = run_import(conn, epub_path, llm=summary_llm, log_path=log,
                         on_progress=messages.append, target_chars=60, prefix_chars=0)
    assert (summary.status, summary.indexed_through, summary.sentences) == ("ready", 8, 8)
    assert summary.added == {"characters": 2, "utterances": 1, "evidence": 3,
                             "snapshots": 0, "rejects": 0}

    theo, mara = ids(conn)
    # the heading is sentence 1, so the fixture's sentences 1-4 are 2-5 here
    assert len(conn.execute(UTTERANCE_SQL, {"book": 1, "who": theo, "pos": 4}).fetchall()) == 1
    assert claims(conn, theo, 4) == ["Theo said it was empty again."]
    assert "Theo was shown the letter inside the atlas." in claims(conn, theo, 5)
    assert not any("hid" in c for c in claims(conn, theo, 8))
    assert claims(conn, mara, 8) == ["Mara hid the letter in the atlas."]

    knowledge_inputs = [c["input"] for c in summary_llm.calls if c["name"] == "report_evidence"]
    assert "last known: (sentence 5) Theo was shown" in knowledge_inputs[-1]

    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(entries) == len(messages) >= 1
    assert entries[0]["paragraphs"] == [1]  # the heading chunk
    assert entries[1]["dialogue"]["characters"][0]["name"] == "Theo"
    assert "chunk 1/" in messages[0]


def test_rerun_after_ready_makes_no_model_calls(conn, epub_path):
    run_import(conn, epub_path, llm=fixture_llm())
    before = counts(conn)
    llm = fixture_llm()
    summary = run_import(conn, epub_path, llm=llm)
    assert summary.status == "ready" and llm.calls == []
    assert counts(conn) == before


def test_failure_marks_failed_and_rerun_resumes_without_duplicates(conn, epub_path):
    # chunks are ¶1 (heading), ¶2 (fixture), ¶3, ... ; fail on the third
    llm = fixture_llm(fail_on=("report_dialogue", 3))
    with pytest.raises(RuntimeError, match="scripted failure"):
        run_import(conn, epub_path, llm=llm, target_chars=60, prefix_chars=0)
    book = conn.execute("SELECT status, indexed_through FROM books").fetchone()
    assert book["status"] == "failed"
    checkpoint = book["indexed_through"]
    assert 0 < checkpoint < 8
    partial = counts(conn)

    resumed = fixture_llm()
    summary = run_import(conn, epub_path, llm=resumed, target_chars=60, prefix_chars=0)
    assert summary.status == "ready"
    first_target = resumed.calls[0]["input"].split("TARGET", 1)[1]
    assert "[¶1]" not in first_target and "[¶2]" not in first_target
    assert counts(conn) == partial  # the fixture rows were committed before the failure
    assert counts(conn)["evidence"] == 3


def test_max_chunks_stops_early_and_leaves_the_book_indexing(conn, epub_path):
    summary = run_import(conn, epub_path, llm=fixture_llm(), target_chars=60,
                         prefix_chars=0, max_chunks=1)
    assert summary.status == "indexing"
    assert summary.chunks_run == 1 and summary.chunks_left > 0
    finished = run_import(conn, epub_path, llm=fixture_llm(), target_chars=60, prefix_chars=0)
    assert finished.status == "ready"


def test_voice_snapshots_run_after_commits(conn, tmp_path):
    lines = "".join(f"<p>“Line {n}.” Theo said.</p>" for n in range(1, 4))
    path = make_epub(tmp_path / "talk.epub", [("c.xhtml", lines)])

    def dialogue(prompt_input):
        return {"characters": [{"key": "t", "name": "Theo",
                                "first_mention": {"para": 1, "text": "Theo said"}}],
                "aliases": [],
                "utterances": [{"speaker": "t", "addressee": None,
                                "quotes": [{"para": n, "text": f"Line {n}."}],
                                "attribution": [{"para": n, "text": "Theo said"}]}
                               for n in (1, 2, 3)]}

    def voice(prompt_input):
        return {"traits": [{"pattern": "Counts aloud.", "supporting_utterance_ids": [1, 2, 3]}],
                "recurring_phrases": []}

    llm = FakeLLM({"report_dialogue": dialogue, "report_evidence": {"evidence": []},
                   "report_voice": voice})
    summary = run_import(conn, path, llm=llm)
    assert summary.added["snapshots"] == 1
    assert llm.names() == ["report_dialogue", "report_evidence", "report_voice"]
    (theo,) = db.load_characters(conn, summary.book_id)
    assert theo.voice_snapshots[0]["through_position"] == summary.indexed_through


def test_next_chunk_can_refer_to_characters_the_previous_chunk_introduced(conn, epub_path):
    """Chunk N+1's dialogue pass runs before chunk N commits, so it sees N's new
    characters by key; those keys must land on the committed rows."""
    base = fixture_llm()

    def dialogue(prompt_input):
        if in_target(prompt_input, 3):
            assert '- key "theo": Theo (from the previous section' in prompt_input
            return {"characters": [], "utterances": [], "aliases": [
                {"character": "theo", "name": "the visitor",
                 "support": [{"para": 3, "text": "Later that day"}]}]}
        return base.replies["report_dialogue"](prompt_input)

    llm = FakeLLM({"report_dialogue": dialogue,
                   "report_evidence": base.replies["report_evidence"]})
    summary = run_import(conn, epub_path, llm=llm, target_chars=60, prefix_chars=0)
    assert summary.status == "ready" and summary.added["rejects"] == 0
    theo = next(c for c in db.load_characters(conn, summary.book_id) if c.display_name == "Theo")
    assert [a["name"] for a in theo.aliases] == ["the visitor"]
    assert counts(conn)["characters"] == 2  # no duplicate Theo


def test_knowledge_and_next_dialogue_calls_overlap(conn, epub_path):
    import threading
    import time as clock

    spans, lock = [], threading.Lock()
    base = fixture_llm()

    class Slow(FakeLLM):
        def complete_json(self, **kwargs):
            began = clock.monotonic()
            clock.sleep(0.15)
            reply = super().complete_json(**kwargs)
            with lock:
                spans.append((kwargs["name"], began, clock.monotonic()))
            return reply

    started = clock.monotonic()
    run_import(conn, epub_path, llm=Slow(base.replies), target_chars=60, prefix_chars=0)
    elapsed = clock.monotonic() - started
    calls = len(spans)
    assert calls == 6  # 3 chunks (heading, fixture, the rest) x 2 passes
    overlapping = sum(
        1 for i, (_, a0, a1) in enumerate(spans) for (_, b0, b1) in spans[i + 1:]
        if a0 < b1 and b0 < a1)
    assert overlapping >= 2
    assert elapsed < calls * 0.15 * 0.85  # measurably faster than one call at a time


def test_two_books_share_one_database_without_mixing(conn, epub_path, tmp_path):
    lines = "".join(f"<p>“Line {n}.” Theo said.</p>" for n in range(1, 4))
    other_path = make_epub(tmp_path / "talk.epub", [("c.xhtml", lines)], title="Other Book")

    def other_dialogue(prompt_input):
        if "Line 1." not in prompt_input:
            return fixture_llm().replies["report_dialogue"](prompt_input)
        return {"characters": [{"key": "t", "name": "Theo",
                                "first_mention": {"para": 1, "text": "Theo said"}}],
                "aliases": [],
                "utterances": [{"speaker": "t", "addressee": None,
                                "quotes": [{"para": 1, "text": "Line 1."}],
                                "attribution": [{"para": 1, "text": "Theo said"}]}]}

    first = run_import(conn, epub_path, llm=fixture_llm())
    second = run_import(conn, other_path, llm=FakeLLM({
        "report_dialogue": other_dialogue, "report_evidence": {"evidence": []}}))
    assert first.book_id != second.book_id
    assert counts(conn)["books"] == 2

    # each book has its own "Theo", and nothing crosses over
    theos = conn.execute(
        "SELECT book_id, id FROM characters WHERE display_name='Theo' ORDER BY book_id").fetchall()
    assert [r["book_id"] for r in theos] == [first.book_id, second.book_id]
    for book_id, theo_id in theos:
        for table, column in (("utterances", "speaker_id"), ("evidence", "character_id")):
            rows = conn.execute(f"SELECT book_id FROM {table} WHERE {column}=?", (theo_id,))
            assert {r["book_id"] for r in rows} <= {book_id}
        for row in conn.execute("SELECT sources FROM utterances WHERE book_id=?", (book_id,)):
            for source in json.loads(row["sources"]):
                owner = conn.execute("SELECT book_id FROM passages WHERE id=?",
                                     (source["passage_id"],)).fetchone()["book_id"]
                assert owner == book_id
    assert len(db.load_characters(conn, first.book_id)) == 2  # Theo, Mara
    assert len(db.load_characters(conn, second.book_id)) == 1


def test_cli_default_database_is_the_shared_file():
    from companion import config
    assert config.DB_PATH.name == "companion.db"
    assert config.DB_PATH.parent.name == "data"
    assert config.DB_PATH.parent.parent.name == "backend"


def test_run_with_resume_retries_api_failures_and_resumes(conn, epub_path):
    from companion.llm import LLMError
    from companion.worker import run_with_resume

    class Flaky(FakeLLM):
        def complete_json(self, **kwargs):
            if len(self.calls) == 4:  # chunk 3's dialogue call, first time only
                self.calls.append({"name": "boom", "input": ""})
                raise LLMError("gave up after 4 tries: ReadError")
            return super().complete_json(**kwargs)

    flaky = Flaky(fixture_llm().replies)
    notes, waits = [], []
    summary = run_with_resume(
        lambda size: run_import(conn, epub_path, llm=flaky, target_chars=size, prefix_chars=0),
        resumes=2, wait=7, target_chars=60, say=notes.append, sleep=waits.append)
    assert summary.status == "ready"
    assert waits == [7] and "Resuming in 7s (1/2)" in notes[0]
    assert counts(conn)["evidence"] == 3

    def always_fails(size):
        raise LLMError("down")

    with pytest.raises(LLMError):
        run_with_resume(always_fails, resumes=2, wait=1, say=notes.append, sleep=waits.append)
    assert waits == [7, 1, 1]


def test_output_limit_halves_the_chunk_size_without_waiting():
    from companion.llm import LLMError
    from companion.worker import run_with_resume

    sizes, notes, waits = [], [], []

    def run(size):
        sizes.append(size)
        if size > 1000:
            raise LLMError("response incomplete: max_output_tokens")
        return "done"

    assert run_with_resume(run, resumes=0, wait=9, target_chars=3500,
                           say=notes.append, sleep=waits.append) == "done"
    assert sizes == [3500, 1750, 875]
    assert waits == []  # no waiting, and no resume attempts used up
    assert "smaller chunks (1750 characters)" in notes[0]

    def always_too_long(size):
        sizes.append(size)
        raise LLMError("response incomplete: max_output_tokens")

    sizes.clear()
    with pytest.raises(LLMError):
        run_with_resume(always_too_long, resumes=1, wait=0, target_chars=3500, min_chars=800,
                        say=notes.append, sleep=waits.append)
    assert sizes == [3500, 1750, 875, 800, 800]  # shrink to the floor, then normal retries


def test_other_errors_are_not_resumed():
    from companion.worker import run_with_resume

    def broken(size):
        raise ValueError("bug")

    with pytest.raises(ValueError):
        run_with_resume(broken, resumes=3, wait=0, sleep=lambda s: None)


def test_cli_requires_a_key(monkeypatch, tmp_path, epub_path):
    from companion import config, llm as llm_module
    monkeypatch.setattr(llm_module.config, "load_env", lambda: None)
    monkeypatch.delenv(config.API_KEY_VAR, raising=False)
    with pytest.raises(llm_module.LLMError, match=config.API_KEY_VAR):
        main([str(epub_path), "--db", str(tmp_path / "x.db")])
