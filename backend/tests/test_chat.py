"""Retrieval, composition and citation checks on the guide's four-sentence fixture."""

import pytest

from companion import db
from companion.chat import (ChatError, chat, clamp, compose, load_passage, rank_evidence,
                            render, retrieve)
from companion.commit import commit_chunk
from companion.snapshots import publish_snapshot
from helpers import PASSAGE_ID, FakeLLM, fixture_candidates, resolve

LOCATION = "Theo was shown the letter inside the atlas."


@pytest.fixture
def story(conn, book):
    result = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4), final=True)
    theo, mara = result.character_ids["theo"], result.character_ids["mara"]
    publish_snapshot(conn, book, theo, {
        "through_position": 3,
        "traits": [{"pattern": "Replies in a short fragment.",
                    "supporting_utterance_ids": result.utterance_ids}],
        "recurring_phrases": []})
    claims = {r["claim"]: r["id"] for r in conn.execute("SELECT id, claim FROM evidence")}
    return {"book": book, "theo": theo, "mara": mara, "claims": claims}


def reply(text, *statements):
    return {"reply": text,
            "statements": [{"text": t, "evidence_ids": ids} for t, ids in statements]}


# -- retrieval ------------------------------------------------------------------

def test_at_3_theo_has_his_line_and_voice_but_not_the_location(conn, story):
    got = retrieve(conn, story["book"], story["theo"], 3, "Where is the letter?")
    assert [e.claim for e in got.evidence] == ["Theo said it was empty again."]
    assert [line.text for line in got.lines] == ["Empty again."]
    assert got.snapshot["traits"][0]["pattern"] == "Replies in a short fragment."
    assert got.character["names"] == ["Theo"]


def test_at_4_the_location_is_retrieved(conn, story):
    got = retrieve(conn, story["book"], story["theo"], 4, "Where is the letter?")
    assert LOCATION in [e.claim for e in got.evidence]


def test_mara_never_shares_her_secret_with_theo(conn, story):
    got = retrieve(conn, story["book"], story["theo"], 4, "Who hid the letter in the atlas?")
    assert not any("hid" in e.claim for e in got.evidence)


def test_ranking_prefers_claims_sharing_rare_words():
    from companion.chat import EvidenceItem
    items = [EvidenceItem(1, "Theo walked outside.", "performed_action", 1, []),
             EvidenceItem(2, "Theo saw the letter inside the atlas.", "observed", 2, []),
             EvidenceItem(3, "Theo saw the desk.", "observed", 3, [])]
    scores = dict((item.id, score) for score, item in rank_evidence(items, "Where's the atlas?"))
    assert scores[2] > 0 and scores[1] == 0 and scores[3] == 0


@pytest.mark.parametrize("who,pos,status", [
    ("mara", 1, 400),   # not met yet
    ("theo", 0, 400),   # no such bookmark
    ("theo", 5, 409),   # past the indexed prefix
    (999, 3, 404),      # unknown character
])
def test_retrieval_refuses_bad_requests(conn, story, who, pos, status):
    character = story.get(who, who)
    with pytest.raises(ChatError) as info:
        retrieve(conn, story["book"], character, pos, "hi")
    assert info.value.status == status


def test_character_from_another_book_is_not_found(conn, story):
    other = db.create_book(conn, "Other", "other-hash", "v1")
    conn.execute("UPDATE books SET indexed_through=4 WHERE id=?", (other,))
    with pytest.raises(ChatError) as info:
        retrieve(conn, other, story["theo"], 1, "hi")
    assert info.value.status == 404


# -- citations ------------------------------------------------------------------

def test_clamp_hides_text_past_the_bookmark(conn, story):
    passage = load_passage(conn, story["book"], PASSAGE_ID)
    at_3 = clamp(passage, 3)
    assert at_3["text"].endswith("“Empty again.”") and at_3["truncated"]
    assert "showed him" not in at_3["text"]
    assert clamp(passage, 4)["truncated"] is False


# -- composition ----------------------------------------------------------------

def test_render_includes_voice_lines_knowledge_history_and_question(conn, story):
    got = retrieve(conn, story["book"], story["theo"], 3, "Where is it?")
    text = render(got, "Where is it?", [{"role": "reader", "text": "Hello"},
                                        {"role": "character", "text": "Hm."}])
    assert "- Replies in a short fragment." in text
    assert '- "Empty again."' in text
    claim_id = story["claims"]["Theo said it was empty again."]
    assert f"[{claim_id}] Theo said it was empty again." in text
    assert "Reader: Hello\nTheo: Hm." in text
    assert text.endswith("THE READER ASKS\nWhere is it?")


def test_ids_outside_the_retrieved_set_are_dropped_and_trigger_a_rewrite(conn, story):
    said = story["claims"]["Theo said it was empty again."]
    location = story["claims"][LOCATION]  # exists, but only from sentence 4
    llm = FakeLLM({"reply_in_character": [
        reply("It's in the atlas.", ("The letter is in the atlas.", [location])),
        reply("Gone. The desk was bare.", ("The desk was empty.", [said])),
    ]})
    got = retrieve(conn, story["book"], story["theo"], 3, "Where is the letter?")
    text, statements, unsupported = compose(llm, got, "Where is the letter?")
    assert text == "Gone. The desk was bare."
    assert statements == [{"text": "The desk was empty.", "evidence_ids": [said]}]
    assert unsupported == []
    assert len(llm.calls) == 2
    assert "REWRITE NEEDED" in llm.calls[1]["input"]
    assert "The letter is in the atlas." in llm.calls[1]["input"]


def test_still_unsupported_after_rewrite_is_reported(conn, story):
    llm = FakeLLM({"reply_in_character": reply("Mara hid it.", ("Mara hid it.", []))})
    result = chat(conn, llm, story["book"], story["theo"], 4, "Who hid it?")
    assert result.unsupported == ["Mara hid it."]
    assert result.citations == []
    assert len(llm.calls) == 2


def test_chat_returns_clamped_citations_and_voice_lines(conn, story):
    location = story["claims"][LOCATION]
    llm = FakeLLM({"reply_in_character": reply(
        "In the atlas, of all places.", ("The letter was in the atlas.", [location, location]))})
    result = chat(conn, llm, story["book"], story["theo"], 4, "  Where is the letter?  ",
                  [{"role": "reader", "text": "Hi"}])
    assert result.kind == "imagined_reply"
    assert result.reply == "In the atlas, of all places."
    (citation,) = result.citations
    assert citation["evidence_id"] == location and citation["basis"] == "observed"
    first = citation["sources"][0]
    assert first["quote"] == "Mara showed him the letter inside the atlas."
    assert first["text"].endswith("inside the atlas.") and not first["truncated"]
    assert [v["text"] for v in result.voice_lines] == ["Empty again."]
    assert llm.calls[0]["input"].endswith("Where is the letter?")


def test_empty_question_is_refused(conn, story):
    with pytest.raises(ChatError) as info:
        chat(conn, FakeLLM(), story["book"], story["theo"], 3, "   ")
    assert info.value.status == 400


def test_character_with_no_lines_still_gets_a_prompt(conn, story):
    got = retrieve(conn, story["book"], story["mara"], 4, "What did you do?")
    assert got.lines == []
    assert "(they have not spoken yet" in render(got, "What did you do?", [])
