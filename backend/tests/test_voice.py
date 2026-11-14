import pytest

from companion import db
from companion.commit import commit_chunk
from companion.voice import clean_snapshot, update_voices
from helpers import FakeLLM, fixture_candidates, resolve

SECOND = "“Empty again,” Theo said."


@pytest.fixture
def story(conn, book):
    """Theo has two lines: sentence 3 and sentence 5."""
    one = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4))
    theo = one.character_ids["theo"]
    db.insert_passage(conn, book, paragraph_pos=2, href="Text/ch01.xhtml", para_no=1,
                      first_sentence=5, sentence_ends=[len(SECOND)], text=SECOND)
    two = commit_chunk(conn, book, resolve(conn, book, {"utterances": [{
        "speaker": theo, "quotes": [{"para": 2, "text": "Empty again,"}],
        "attribution": [{"para": 2, "text": "Theo said"}]}]}, 4, 5))
    return {"book": book, "theo": theo, "mara": one.character_ids["mara"],
            "first": one.utterance_ids[0], "second": two.utterance_ids[0]}


def test_clean_snapshot_keeps_only_provable_entries():
    lines = {1: "Empty again.", 2: "Empty again, I said.", 3: "Splendid."}
    raw = {
        "traits": [
            {"pattern": "Terse.", "supporting_utterance_ids": [1, 99, 1]},
            {"pattern": "Cites nothing real.", "supporting_utterance_ids": [99]},
            {"pattern": "  ", "supporting_utterance_ids": [1]},
            "junk",
        ],
        "recurring_phrases": [
            {"phrase": "“empty again”", "supporting_utterance_ids": [1, 2, 3]},
            {"phrase": "Splendid", "supporting_utterance_ids": [3]},
            {"phrase": "Empty", "supporting_utterance_ids": [1, 1]},
        ],
    }
    assert clean_snapshot(raw, lines) == {
        "traits": [{"pattern": "Terse.", "supporting_utterance_ids": [1]}],
        "recurring_phrases": [{"phrase": "empty again", "supporting_utterance_ids": [1, 2]}],
    }


def test_snapshot_is_written_at_the_chunk_end(conn, story):
    def reply(prompt_input):
        assert f"[{story['first']}] Empty again." in prompt_input
        assert f"[{story['second']}] Empty again," in prompt_input
        assert "CHARACTER: Theo" in prompt_input
        return {"traits": [{"pattern": "Short fragments.",
                            "supporting_utterance_ids": [story["first"], 999]}],
                "recurring_phrases": [{"phrase": "Empty again",
                                       "supporting_utterance_ids": [story["first"], story["second"]]}]}

    llm = FakeLLM({"report_voice": reply})
    updates = update_voices(conn, llm, story["book"], 5, min_new=2)
    assert [u.character_id for u in updates] == [story["theo"]]
    theo = next(c for c in db.load_characters(conn, story["book"]) if c.id == story["theo"])
    assert theo.voice_snapshots == [{
        "through_position": 5,
        "traits": [{"pattern": "Short fragments.", "supporting_utterance_ids": [story["first"]]}],
        "recurring_phrases": [{"phrase": "Empty again",
                               "supporting_utterance_ids": [story["first"], story["second"]]}],
    }]


def test_below_threshold_makes_no_model_call(conn, story):
    llm = FakeLLM()
    assert update_voices(conn, llm, story["book"], 5) == []  # default min_new is 3
    assert update_voices(conn, llm, story["book"], 4, min_new=2) == []  # only 1 line by 4
    assert llm.calls == []


def test_only_lines_since_the_last_snapshot_count(conn, story):
    reply = {"traits": [{"pattern": "Terse.", "supporting_utterance_ids": [story["first"]]}],
             "recurring_phrases": []}
    llm = FakeLLM({"report_voice": reply})
    update_voices(conn, llm, story["book"], 3, min_new=1)
    assert len(llm.calls) == 1
    update_voices(conn, llm, story["book"], 5, min_new=2)  # one new line since 3
    assert len(llm.calls) == 1
    update_voices(conn, llm, story["book"], 5, min_new=1)
    assert len(llm.calls) == 2
    assert "PREVIOUS NOTES\n- Terse." in llm.calls[1]["input"]


def test_empty_answer_publishes_nothing(conn, story):
    llm = FakeLLM({"report_voice": {"traits": [], "recurring_phrases": []}})
    assert update_voices(conn, llm, story["book"], 5, min_new=1) == []
    assert all(c.voice_snapshots == [] for c in db.load_characters(conn, story["book"]))
