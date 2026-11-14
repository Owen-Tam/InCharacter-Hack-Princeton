import pytest

from companion.chunker import Chunk, looks_like_heading, plan_chunks
from companion.db import Passage
from companion.sentences import split_sentences


def make_passages(texts):
    passages, sentence = [], 1
    for pos, text in enumerate(texts, start=1):
        ends = tuple(split_sentences(text))
        passages.append(Passage(pos, 1, pos, "a.xhtml", pos - 1, sentence, ends, text))
        sentence += len(ends)
    return passages


def para(n_sentences, width=40):
    sentence = ("x" * (width - 2)) + "."
    return " ".join(sentence.capitalize() for _ in range(n_sentences))


def check_coverage(chunks, passages, after=0):
    """Windows are contiguous and cover every remaining sentence exactly once."""
    last = passages[-1].last_sentence
    assert chunks[0].start == after
    for previous, current in zip(chunks, chunks[1:]):
        assert current.start == previous.through
    assert chunks[-1].through == last
    for chunk in chunks:
        assert chunk.start < chunk.through
        assert chunk.target[-1].last_sentence == chunk.through
        positions = [p.paragraph_pos for p in chunk.target]
        assert positions == list(range(positions[0], positions[-1] + 1))


def test_greedy_paragraph_aligned_chunks():
    passages = make_passages([para(3)] * 10)  # 10 paragraphs of ~120 chars
    chunks = plan_chunks(passages, target_chars=400, prefix_chars=0)
    check_coverage(chunks, passages)
    assert [len(c.target) for c in chunks] == [3, 3, 3, 1]
    assert all(c.context == () for c in chunks)


def test_everything_fits_in_one_chunk():
    passages = make_passages([para(2), para(2)])
    (chunk,) = plan_chunks(passages)
    assert (chunk.start, chunk.through) == (0, 4)
    assert set(chunk.passages) == {1, 2}


def test_oversized_paragraph_is_its_own_chunk():
    passages = make_passages([para(1), para(50), para(1)])
    chunks = plan_chunks(passages, target_chars=500, prefix_chars=0)
    check_coverage(chunks, passages)
    assert [[p.paragraph_pos for p in c.target] for c in chunks] == [[1], [2], [3]]


def test_prefers_to_break_before_a_heading():
    passages = make_passages([para(3), para(3), "Chapter II.", para(3), para(3)])
    # ~244 chars before the heading: over half of 400, so the chunk closes early
    chunks = plan_chunks(passages, target_chars=400, prefix_chars=0)
    check_coverage(chunks, passages)
    assert [[p.paragraph_pos for p in c.target] for c in chunks] == [[1, 2], [3, 4, 5]]


def test_heading_does_not_break_a_nearly_empty_chunk():
    passages = make_passages(["Chapter I.", para(1), "Chapter II.", para(1)])
    (chunk,) = plan_chunks(passages, target_chars=1000)
    assert len(chunk.target) == 4


def test_context_is_the_preceding_paragraphs_within_budget():
    passages = make_passages([para(1)] * 6)  # ~40 chars each
    chunks = plan_chunks(passages, target_chars=80, prefix_chars=90)
    assert chunks[0].context == ()
    assert [p.paragraph_pos for p in chunks[1].context] == [1, 2]
    assert [p.paragraph_pos for p in chunks[2].context] == [3, 4]
    assert set(chunks[2].passages) == {3, 4, 5, 6}


def test_huge_preceding_paragraph_is_left_out_of_context():
    passages = make_passages([para(100), para(1)])
    chunks = plan_chunks(passages, target_chars=100, prefix_chars=500)
    assert chunks[1].context == ()


def test_resumes_after_the_checkpoint():
    passages = make_passages([para(2)] * 5)  # sentences 1-10
    chunks = plan_chunks(passages, after=6, target_chars=100, prefix_chars=250)
    check_coverage(chunks, passages, after=6)
    assert [p.paragraph_pos for p in chunks[0].target] == [4]
    assert [p.paragraph_pos for p in chunks[0].context] == [1, 2, 3]
    assert plan_chunks(passages, after=10) == []


def test_checkpoint_inside_a_paragraph_keeps_the_paragraph_whole():
    passages = make_passages([para(4)])
    (chunk,) = plan_chunks(passages, after=2)
    assert (chunk.start, chunk.through) == (2, 4)
    assert chunk.target == (passages[0],)


@pytest.mark.parametrize("text,expected", [
    ("Chapter I.", True), ("CHAPTER XII", True), ("II", True), ("Part Two", True),
    ("PRIDE AND PREJUDICE", True), ("The End", True),
    ("He left.", False), ("“Yes,” she said.", False), (para(3), False),
])
def test_looks_like_heading(text, expected):
    (passage,) = make_passages([text])
    assert looks_like_heading(passage) is expected


def test_chunk_is_hashable_value():
    passages = make_passages([para(1)])
    assert isinstance(plan_chunks(passages)[0], Chunk)
