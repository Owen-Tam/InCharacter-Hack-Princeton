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


def fixture_llm(para=2, **kwargs):
    """Answers with the fixture only when its paragraph (¶para) is in TARGET."""
    def dialogue(prompt_input):
        if not in_target(prompt_input, para):
            return {"characters": [], "aliases": [], "utterances": []}
        c = fixture_candidates()
        return {"characters": remap(c["characters"], para), "aliases": [],
                "utterances": remap(c["utterances"], para)}

    def knowledge(prompt_input):
        if not in_target(prompt_input, para):
            return {"evidence": []}
        return {"evidence": remap(fixture_candidates()["evidence"], para)}

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
        lambda size, limit: run_import(conn, epub_path, llm=flaky, target_chars=size,
                                       max_chunks=limit, prefix_chars=0),
        resumes=2, wait=7, target_chars=60, say=notes.append, sleep=waits.append)
    assert summary.status == "ready"
    assert waits == [7] and "Resuming in 7s (1/2)" in notes[0]
    assert counts(conn)["evidence"] == 3

    def always_fails(size, limit):
        raise LLMError("down")

    with pytest.raises(LLMError):
        run_with_resume(always_fails, resumes=2, wait=1, say=notes.append, sleep=waits.append)
    assert waits == [7, 1, 1]


def test_output_limit_detours_with_smaller_chunks_then_returns_to_full_size():
    from types import SimpleNamespace

    from companion.llm import LLMError
    from companion.worker import run_with_resume

    calls, notes, waits = [], [], []
    trouble = iter([True, True, False])  # the first two attempts on the hard passage loop

    def run(size, limit):
        calls.append((size, limit))
        if size > 1000 and limit is None and next(trouble, False):
            raise LLMError("response incomplete: max_output_tokens")
        if limit is not None:  # the detour gets past the passage, more book remains
            return SimpleNamespace(status="indexing")
        return SimpleNamespace(status="ready")

    summary = run_with_resume(run, resumes=0, wait=9, target_chars=3500,
                              say=notes.append, sleep=waits.append)
    assert summary.status == "ready"
    # full size fails -> two half-size chunks -> back to full size, which loops again
    # -> detour again -> back to full size, which finishes
    assert calls == [(3500, None), (1750, 2), (3500, None), (1750, 2), (3500, None)]
    assert waits == []  # no waiting, and no resume attempts used up
    assert "smaller chunks (1750 characters)" in notes[0]
    assert "back to 3500-character chunks" in notes[1]


def test_output_limit_shrinks_to_the_floor_before_counting_failures():
    from companion.llm import LLMError
    from companion.worker import run_with_resume

    sizes = []

    def always_too_long(size, limit):
        sizes.append(size)
        raise LLMError("response incomplete: max_output_tokens")

    with pytest.raises(LLMError):
        run_with_resume(always_too_long, resumes=1, wait=0, target_chars=3500, min_chars=800,
                        say=lambda m: None, sleep=lambda s: None)
    assert sizes == [3500, 1750, 875, 800, 800]  # shrink to the floor, then normal retries


def test_detour_that_finishes_the_book_returns_ready():
    from types import SimpleNamespace

    from companion.llm import LLMError
    from companion.worker import run_with_resume

    calls = []

    def run(size, limit):
        calls.append((size, limit))
        if limit is None:
            raise LLMError("response incomplete: max_output_tokens")
        return SimpleNamespace(status="ready")

    assert run_with_resume(run, resumes=0, wait=0, say=lambda m: None).status == "ready"
    assert calls == [(3500, None), (1750, 2)]


def test_other_errors_are_not_resumed():
    from companion.worker import run_with_resume

    def broken(size, limit):
        raise ValueError("bug")

    with pytest.raises(ValueError):
        run_with_resume(broken, resumes=3, wait=0, sleep=lambda s: None)


def test_extract_book_keeps_awake_imports_and_repairs(conn, epub_path, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from companion import repair, worker
    from companion.repair import RepairSummary

    states = []

    @contextmanager
    def fake_awake():
        states.append("awake")
        yield True
        states.append("asleep-allowed")

    repaired_with = []

    def fake_repair(conn_, book_id, log_path, dry_run=False):
        repaired_with.append((book_id, log_path))
        return RepairSummary(evidence_added=2, utterances_added=1)

    monkeypatch.setattr(worker, "keep_awake", fake_awake)
    monkeypatch.setattr(repair, "recover_rejects", fake_repair)
    log = tmp_path / "log.jsonl"
    notes = []
    summary = worker.extract_book(conn, epub_path, llm=fixture_llm(), log_path=log,
                                  say=notes.append)
    assert summary.status == "ready"
    assert states == ["awake", "asleep-allowed"]
    assert repaired_with == [(summary.book_id, log)]
    assert summary.added["evidence"] == 3 + 2 and summary.added["utterances"] == 1 + 1
    assert notes[0].startswith("Keeping the computer awake")
    assert "Recovered 2 claims and 1 lines" in notes[-1]


def test_extract_book_can_skip_staying_awake_and_needs_no_log(conn, epub_path, monkeypatch):
    from companion import worker

    def must_not_run():
        raise AssertionError("keep_awake should not be used")

    monkeypatch.setattr(worker, "keep_awake", must_not_run)
    summary = worker.extract_book(conn, epub_path, llm=fixture_llm(), stay_awake=False,
                                  say=lambda m: None)
    assert summary.status == "ready"


FRONT = "<p>Contents</p><p>Foreword: everyone in the house was later arrested.</p>"
BACK = "<p>AFTERWORD</p><p>Theo died years later, far away.</p><p>Index</p>"


@pytest.fixture
def framed_epub(tmp_path):
    return make_epub(tmp_path / "framed.epub", [
        ("front.xhtml", FRONT), ("Text/ch01.xhtml", FIXTURE_HTML), ("back.xhtml", BACK)])


def test_story_range_keeps_front_and_back_matter_away_from_the_model(conn, framed_epub, tmp_path):
    log = tmp_path / "log.jsonl"
    llm = fixture_llm(para=4)  # after "Contents" and the foreword
    notes = []
    summary = run_import(conn, framed_epub, llm=llm, log_path=log, on_progress=notes.append,
                         story_start="Chapter One", story_end="afterword")
    assert summary.status == "ready" and summary.indexed_through == summary.sentences
    sent = " ".join(call["input"] for call in llm.calls)
    assert "arrested" not in sent and "died years later" not in sent and "Contents" not in sent
    assert "Theo stepped outside" in sent
    assert any("Skipped sentences 1-" in n and "before the story" in n for n in notes)
    assert any("after the story" in n for n in notes)
    # the fixture's records still land at the right sentences
    theo = conn.execute("SELECT id FROM characters WHERE display_name='Theo'").fetchone()[0]
    assert "Theo was shown the letter inside the atlas." in claims(conn, theo, summary.sentences)


def test_story_range_is_remembered_when_rerun_without_the_options(conn, framed_epub, tmp_path):
    log = tmp_path / "log.jsonl"
    first = run_import(conn, framed_epub, llm=fixture_llm(), log_path=log, target_chars=20,
                       prefix_chars=0, max_chunks=1,
                       story_start="Chapter One", story_end="AFTERWORD")
    assert first.status == "indexing"
    llm = fixture_llm()
    second = run_import(conn, framed_epub, llm=llm, log_path=log, target_chars=20, prefix_chars=0)
    assert second.status == "ready"
    sent = " ".join(call["input"] for call in llm.calls)
    assert "died years later" not in sent and "Index" not in sent
    assert sum('"story_range"' in line for line in log.read_text().splitlines()) == 1


@pytest.mark.parametrize("start,end,message", [
    ("Nothing like this", None, "no paragraph matches"),
    ("AFTERWORD", "Chapter One", "comes before"),
])
def test_bad_story_markers_are_refused(conn, framed_epub, start, end, message):
    with pytest.raises(ValueError, match=message):
        run_import(conn, framed_epub, llm=fixture_llm(), story_start=start, story_end=end)


def test_find_paragraph_prefers_an_exact_match_and_ignores_quote_style():
    from companion.worker import find_paragraph
    from helpers import make_epub as _  # noqa: F401  (keeps helper import style consistent)
    from companion.db import Passage

    def p(pos, text):
        return Passage(pos, 1, pos, "a", pos, pos, (len(text),), text)

    passages = {1: p(1, "Born June 12, 1942, she wrote."), 2: p(2, "June 12, 1942"),
                3: p(3, "ANNE’S DIARY ENDS HERE.")}
    assert find_paragraph(passages, "june 12, 1942").paragraph_pos == 2
    assert find_paragraph(passages, "Born June").paragraph_pos == 1
    assert find_paragraph(passages, "ANNE'S DIARY ENDS HERE").paragraph_pos == 3


def test_cli_requires_a_key(monkeypatch, tmp_path, epub_path):
    from companion import config, llm as llm_module
    monkeypatch.setattr(llm_module.config, "load_env", lambda: None)
    monkeypatch.delenv(config.API_KEY_VAR, raising=False)
    with pytest.raises(llm_module.LLMError, match=config.API_KEY_VAR):
        main([str(epub_path), "--db", str(tmp_path / "x.db")])
