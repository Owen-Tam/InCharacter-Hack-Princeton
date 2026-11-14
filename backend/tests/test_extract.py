from companion import db
from companion.chunker import plan_chunks
from companion.extract import extract_chunk, render_registry, render_text
from companion.resolve import resolve_chunk
from helpers import FakeLLM, fixture_candidates


def fixture_replies():
    candidates = fixture_candidates()
    return {
        "report_dialogue": {"characters": candidates["characters"], "aliases": [],
                            "utterances": candidates["utterances"]},
        "report_evidence": {"evidence": candidates["evidence"]},
    }


def test_render_text_marks_context_and_target(conn, book):
    db.insert_passage(conn, book, paragraph_pos=2, href="Text/ch01.xhtml", para_no=1,
                      first_sentence=5, sentence_ends=[8], text="The end.")
    passages = db.load_passages(conn, book)
    chunk = plan_chunks(passages.values(), after=4)[0]
    rendered = render_text(chunk)
    assert rendered.splitlines()[0] == "CONTEXT (already processed)"
    assert rendered.splitlines()[1].startswith("[¶1] Theo stepped outside.")
    assert rendered.splitlines()[2:] == ["TARGET", "[¶2] The end."]


def test_render_registry_lists_ids_aliases_and_new_keys(conn, book):
    conn.execute("INSERT INTO characters (book_id, display_name, first_seen, aliases)"
                 " VALUES (?, 'Mrs. Hutchinson', 1, '[{\"name\": \"Tessie\"}]')", (book,))
    characters = db.load_characters(conn, book)
    text = render_registry(characters, [{"key": "joe", "name": "Mr. Summers"}, "junk"])
    assert text.splitlines() == [
        f"- id {characters[0].id}: Mrs. Hutchinson (also called: Tessie)",
        '- key "joe": Mr. Summers (new in this section)',
    ]
    assert render_registry([]) == "(none yet)"


def test_knowledge_registry_shows_each_characters_latest_claim(conn, book):
    from companion.commit import commit_chunk
    from companion.extract import last_known_claims
    from helpers import resolve

    result = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4))
    theo, mara = result.character_ids["theo"], result.character_ids["mara"]
    conn.execute("INSERT INTO characters (book_id, display_name, first_seen) VALUES (?, 'Ada', 1)",
                 (book,))

    assert last_known_claims(conn, book, 3) == {
        mara: "(sentence 2) Mara hid the letter in the atlas.",
        theo: "(sentence 3) Theo said it was empty again.",
    }
    latest = last_known_claims(conn, book, 4)
    assert latest[theo] == "(sentence 4) Theo was shown the letter inside the atlas."

    text = render_registry(db.load_characters(conn, book), last_known=latest)
    assert f"- id {theo}: Theo | last known: (sentence 4) Theo was shown" in text
    assert "Ada | last known: nothing recorded yet" in text
    assert "last known" not in render_registry(db.load_characters(conn, book))


def test_two_passes_and_merged_candidates_resolve_cleanly(conn, book):
    llm = FakeLLM(fixture_replies())
    passages = db.load_passages(conn, book)
    (chunk,) = plan_chunks(passages.values())
    extraction = extract_chunk(llm, chunk, [])

    assert llm.names() == ["report_dialogue", "report_evidence"]
    dialogue_call, knowledge_call = llm.calls
    assert "KNOWN CHARACTERS\n(none yet)" in dialogue_call["input"]
    assert "[¶1] Theo stepped outside." in dialogue_call["input"]
    assert '- key "theo": Theo (new in this section)' in knowledge_call["input"]
    assert "spoken dialogue" in dialogue_call["instructions"]
    assert dialogue_call["reasoning_effort"] == "low"
    assert knowledge_call["reasoning_effort"] == "low"
    assert "what each character in a story knows" in knowledge_call["instructions"]

    resolved = resolve_chunk(extraction.candidates, chunk.passages, [], chunk.start, chunk.through)
    assert resolved.rejects == []
    assert len(resolved.characters) == 2 and len(resolved.utterances) == 1
    assert len(resolved.evidence) == 3


def test_merge_candidates_declares_carried_characters_once():
    from companion.extract import merge_candidates
    pending = [{"key": "theo", "name": "Theo", "first_mention": {"para": 1, "text": "Theo"}},
               "junk"]
    dialogue = {"characters": [{"key": "theo", "name": "Theo again"},
                               {"key": "ada", "name": "Ada"}],
                "utterances": [{"speaker": "theo"}]}
    merged = merge_candidates(dialogue, {"evidence": [{"characters": ["ada"]}]}, pending)
    assert [c["key"] for c in merged["characters"]] == ["theo", "ada"]
    assert merged["characters"][0]["name"] == "Theo"
    assert merged["utterances"] == [{"speaker": "theo"}]
    assert merged["evidence"] == [{"characters": ["ada"]}]


def test_malformed_model_lists_become_empty(conn, book):
    llm = FakeLLM({"report_dialogue": {"characters": "Theo", "utterances": None},
                   "report_evidence": {"evidence": {"oops": 1}}})
    (chunk,) = plan_chunks(db.load_passages(conn, book).values())
    candidates = extract_chunk(llm, chunk, []).candidates
    assert candidates == {"characters": [], "aliases": [], "utterances": [], "evidence": []}
