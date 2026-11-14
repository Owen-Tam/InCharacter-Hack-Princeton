import pytest

from companion.db import check_passage
from companion.sentences import split_sentences
from helpers import FIXTURE_ENDS, FIXTURE_TEXT


def pieces(text):
    out, start = [], 0
    for end in split_sentences(text):
        out.append(text[start:end])
        start = end
    return out


def test_guide_fixture():
    assert split_sentences(FIXTURE_TEXT) == FIXTURE_ENDS


@pytest.mark.parametrize("text,expected", [
    ("One. Two.", ["One.", " Two."]),
    ("Really? Yes! Fine.", ["Really?", " Yes!", " Fine."]),
    ("What?! No.", ["What?!", " No."]),
    ("Wait… Then go.", ["Wait…", " Then go."]),
    ("Wait... Then go.", ["Wait...", " Then go."]),
    ("He left (quietly.) She stayed.", ["He left (quietly.)", " She stayed."]),
    ("‘Go.’ She went.", ["‘Go.’", " She went."]),
    ("It ended. “Begin,” he said.", ["It ended.", " “Begin,” he said."]),
    ("Room 4. 12 more remain.", ["Room 4.", " 12 more remain."]),
    ("No final stop", ["No final stop"]),
    ("Done. and lowercase", ["Done. and lowercase"]),
])
def test_splits(text, expected):
    assert pieces(text) == expected


@pytest.mark.parametrize("text", [
    "Mr. Darcy bowed.",
    "Mrs. Bennet and Dr. Jones met St. John.",
    "MR. COLLINS spoke.",
    "J. R. Smith wrote it.",
    "“Where?” he asked.",
    "“You came!” she cried.",
    "She paused—“No.”",
])
def test_does_not_split(text):
    assert split_sentences(text) == [len(text)]


def test_lone_I_ends_a_sentence():
    assert pieces("It was I. Then she spoke.") == ["It was I.", " Then she spoke."]


def test_known_split_before_an_uppercase_speaker_tag():
    # Accepted behaviour: the tag becomes its own sentence.
    assert pieces("“Yes!” Theo said.") == ["“Yes!”", " Theo said."]


def test_empty_text_is_refused():
    with pytest.raises(ValueError):
        split_sentences("")


@pytest.mark.parametrize("text", [
    FIXTURE_TEXT, "A. B. C.", "x", "Hi!   There.", "End.”", "?! ?! ?!", "\U0001F600. Ok.",
])
def test_output_is_always_a_valid_passage(text):
    check_passage(1, split_sentences(text), text)
