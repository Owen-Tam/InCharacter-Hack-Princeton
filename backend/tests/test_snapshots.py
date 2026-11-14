import json

import pytest

from companion import db
from companion.commit import commit_chunk
from companion.snapshots import SnapshotError, publish_snapshot
from helpers import SNAPSHOT_SQL, fixture_candidates, resolve

SECOND = "“Empty again,” Theo said."


@pytest.fixture
def story(conn, book):
    """Fixture committed through 4, plus a second Theo line at sentence 5."""
    one = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4))
    theo, mara = one.character_ids["theo"], one.character_ids["mara"]
    db.insert_passage(conn, book, paragraph_pos=2, href="Text/ch01.xhtml", para_no=1,
                      first_sentence=5, sentence_ends=[len(SECOND)], text=SECOND)
    two = commit_chunk(conn, book, resolve(conn, book, {"utterances": [{
        "speaker": theo,
        "quotes": [{"para": 2, "text": "Empty again,"}],
        "attribution": [{"para": 2, "text": "Theo said"}]}]}, 4, 5))
    return {"book": book, "theo": theo, "mara": mara,
            "first": one.utterance_ids[0], "second": two.utterance_ids[0]}


def snapshot(story, through=3, traits=None, phrases=None):
    if traits is None:
        traits = [{"pattern": "Replies in a short fragment.",
                   "supporting_utterance_ids": [story["first"]]}]
    return {"through_position": through, "traits": traits, "recurring_phrases": phrases or []}


def latest(conn, story, pos, who="theo"):
    row = conn.execute(
        SNAPSHOT_SQL, {"book": story["book"], "who": story[who], "pos": pos}).fetchone()
    return json.loads(row[0]) if row else None


def test_published_snapshot_is_retrievable_from_its_position(conn, story):
    stored = publish_snapshot(conn, story["book"], story["theo"], snapshot(story))
    assert stored == {
        "through_position": 3,
        "traits": [{"pattern": "Replies in a short fragment.",
                    "supporting_utterance_ids": [story["first"]]}],
        "recurring_phrases": [],
    }
    assert latest(conn, story, 2) is None
    assert latest(conn, story, 3) == stored
    assert latest(conn, story, 5) == stored


def test_bookmark_selects_the_latest_eligible_snapshot(conn, story):
    phrase = [{"phrase": "empty again",
               "supporting_utterance_ids": [story["first"], story["second"]]}]
    publish_snapshot(conn, story["book"], story["theo"], snapshot(story, 5, phrases=phrase))
    publish_snapshot(conn, story["book"], story["theo"], snapshot(story, 3))
    stored = db.load_characters(conn, story["book"])[0].voice_snapshots
    assert [s["through_position"] for s in stored] == [3, 5]
    assert latest(conn, story, 4)["through_position"] == 3
    assert latest(conn, story, 5)["recurring_phrases"][0]["phrase"] == "empty again"


def test_republishing_a_position_replaces_it(conn, story):
    publish_snapshot(conn, story["book"], story["theo"], snapshot(story))
    publish_snapshot(conn, story["book"], story["theo"], snapshot(
        story, traits=[{"pattern": "Dry.", "supporting_utterance_ids": [story["first"]]}]))
    stored = db.load_characters(conn, story["book"])[0].voice_snapshots
    assert [t["pattern"] for s in stored for t in s["traits"]] == ["Dry."]


def test_cannot_cite_another_characters_turn(conn, story):
    with pytest.raises(SnapshotError, match="not spoken by"):
        publish_snapshot(conn, story["book"], story["mara"], snapshot(story))


def test_cannot_cite_a_turn_the_reader_has_not_reached(conn, story):
    with pytest.raises(SnapshotError, match="not readable"):
        publish_snapshot(conn, story["book"], story["theo"], snapshot(story, through=2))
    late = [{"pattern": "Repeats himself.", "supporting_utterance_ids": [story["second"]]}]
    with pytest.raises(SnapshotError, match="not readable"):
        publish_snapshot(conn, story["book"], story["theo"], snapshot(story, 4, traits=late))


def test_cannot_run_ahead_of_the_committed_prefix(conn, story):
    with pytest.raises(SnapshotError, match="committed prefix"):
        publish_snapshot(conn, story["book"], story["theo"], snapshot(story, through=6))


def test_character_must_be_introduced(conn, story):
    with pytest.raises(SnapshotError, match="not introduced"):
        publish_snapshot(conn, story["book"], story["mara"], snapshot(story, 1, traits=[]))


@pytest.mark.parametrize("ids", [[], None, ["1"], [True], [9999]])
def test_trait_needs_real_supporting_turns(conn, story, ids):
    traits = [{"pattern": "Terse.", "supporting_utterance_ids": ids}]
    with pytest.raises(SnapshotError):
        publish_snapshot(conn, story["book"], story["theo"], snapshot(story, traits=traits))


def test_catchphrase_needs_two_distinct_turns_that_contain_it(conn, story):
    def publish(phrase, ids):
        return publish_snapshot(conn, story["book"], story["theo"], snapshot(
            story, 5, phrases=[{"phrase": phrase, "supporting_utterance_ids": ids}]))

    both = [story["first"], story["second"]]
    with pytest.raises(SnapshotError, match="2 distinct"):
        publish("Empty again", [story["first"]])
    with pytest.raises(SnapshotError, match="2 distinct"):
        publish("Empty again", [story["first"], story["first"]])
    with pytest.raises(SnapshotError, match="not found"):
        publish("Splendid", both)
    assert publish("Empty again", both)["recurring_phrases"][0]["supporting_utterance_ids"] == both


@pytest.mark.parametrize("bad", [
    None, [], {"through_position": "3"}, {"through_position": 0},
    {"through_position": 3, "traits": {}},
    {"through_position": 3, "traits": [{"pattern": " ", "supporting_utterance_ids": [1]}]},
    {"through_position": 3, "traits": ["Terse."]},
])
def test_malformed_snapshot_is_refused_and_nothing_is_stored(conn, story, bad):
    with pytest.raises(SnapshotError):
        publish_snapshot(conn, story["book"], story["theo"], bad)
    assert db.load_characters(conn, story["book"])[0].voice_snapshots == []


def test_unknown_character_or_book_is_refused(conn, story):
    with pytest.raises(SnapshotError):
        publish_snapshot(conn, story["book"], 9999, snapshot(story))
    with pytest.raises(SnapshotError):
        publish_snapshot(conn, 9999, story["theo"], snapshot(story))
