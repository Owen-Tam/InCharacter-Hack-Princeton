"""HTTP endpoints, with the model and the import worker replaced by fakes."""

import pytest
from fastapi.testclient import TestClient

from companion import api, config, db
from companion.commit import commit_chunk
from helpers import (FIXTURE_ENDS, FIXTURE_TEXT, PASSAGE_ID, FakeLLM, fixture_candidates,
                     make_epub, resolve)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "companion.db"
    conn = db.connect(path)
    db.init_schema(conn)
    book = db.create_book(conn, "The Missing Letter", "demo-epub-hash", "extract-v1")
    db.insert_passage(conn, book, id=PASSAGE_ID, paragraph_pos=1, href="Text/ch01.xhtml",
                      para_no=0, first_sentence=1, sentence_ends=FIXTURE_ENDS, text=FIXTURE_TEXT)
    result = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4), final=True)
    location = conn.execute("SELECT id FROM evidence WHERE claim LIKE 'Theo was shown%'").fetchone()[0]
    conn.close()

    llm = FakeLLM({"reply_in_character": {
        "reply": "In the atlas, of all places.",
        "statements": [{"text": "It was in the atlas.", "evidence_ids": [location]}]}})
    imports = []
    app = api.create_app(path, chat_llm_factory=lambda: llm,
                         import_runner=lambda epub, book_id, db_path: imports.append(book_id))
    return {"client": TestClient(app), "book": book, "llm": llm, "imports": imports,
            "theo": result.character_ids["theo"], "mara": result.character_ids["mara"],
            "tmp": tmp_path}


def test_health_and_book_list(setup):
    client = setup["client"]
    assert client.get("/health").json() == {"ok": True}
    (book,) = client.get("/books").json()
    assert book == {"id": setup["book"], "title": "The Missing Letter", "status": "ready",
                    "indexed_through": 4, "sentences": 4, "version": "extract-v1"}
    assert client.get(f"/books/{setup['book']}").json()["status"] == "ready"
    assert client.get("/books/999").status_code == 404


def test_passages_are_paged(setup, tmp_path):
    client = setup["client"]
    page = client.get(f"/books/{setup['book']}/passages").json()
    assert page["next_start"] is None
    (passage,) = page["passages"]
    assert passage["text"] == FIXTURE_TEXT and passage["sentence_ends"] == FIXTURE_ENDS


def test_characters_depend_on_the_bookmark(setup):
    client = setup["client"]
    at_1 = client.get(f"/books/{setup['book']}/characters", params={"position": 1}).json()
    assert [c["name"] for c in at_1] == ["Theo"]
    assert at_1[0]["lines"] == 0 and at_1[0]["facts"] == 0
    everyone = client.get(f"/books/{setup['book']}/characters").json()
    assert [c["name"] for c in everyone] == ["Theo", "Mara"]
    theo = everyone[0]
    assert theo["lines"] == 1 and theo["facts"] == 2 and theo["has_voice_profile"] is False


def test_chat_returns_a_cited_imagined_reply(setup):
    response = setup["client"].post("/chat", json={
        "book_id": setup["book"], "character_id": setup["theo"], "position": 4,
        "question": "Where is the letter?", "history": [{"role": "reader", "text": "Hi"}]})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "imagined_reply"
    assert body["character"] == {"id": setup["theo"], "name": "Theo"}
    assert body["reply"] == "In the atlas, of all places."
    assert body["citations"][0]["sources"][0]["quote"] == "Mara showed him the letter inside the atlas."
    assert body["unsupported_statements"] == []
    assert "Reader: Hi" in setup["llm"].calls[0]["input"]


@pytest.mark.parametrize("payload,status", [
    ({"position": 9}, 409),
    ({"position": 0}, 400),
    ({"character": "mara", "position": 1}, 400),
    ({"character_id": 999}, 404),
    ({"question": " "}, 400),
    ({"history": [{"role": "narrator", "text": "x"}]}, 422),
])
def test_chat_errors(setup, payload, status):
    body = {"book_id": setup["book"], "character_id": setup["theo"], "position": 4,
            "question": "Where?", "history": []}
    if "character" in payload:
        body["character_id"] = setup[payload.pop("character")]
    body.update(payload)
    assert setup["client"].post("/chat", json=body).status_code == status


def test_model_failure_is_a_502_with_a_readable_message(setup):
    from companion.llm import LLMError

    def broken(*args, **kwargs):
        raise LLMError("response incomplete: max_output_tokens")

    setup["llm"].complete_json = broken
    response = setup["client"].post("/chat", json={
        "book_id": setup["book"], "character_id": setup["theo"], "position": 4,
        "question": "Where?", "history": []})
    assert response.status_code == 502
    assert "could not answer right now" in response.json()["detail"]


def test_citation_passage_is_clamped(setup):
    client = setup["client"]
    url = f"/books/{setup['book']}/passages/{PASSAGE_ID}"
    at_3 = client.get(url, params={"position": 3}).json()
    assert at_3["truncated"] is True and "showed him" not in at_3["text"]
    assert client.get(url, params={"position": 4}).json()["truncated"] is False
    assert client.get(url, params={"position": 5}).status_code == 409
    assert client.get(f"/books/{setup['book']}/passages/999", params={"position": 4}).status_code == 404


def test_upload_creates_the_book_and_starts_extraction(setup):
    epub = make_epub(setup["tmp"] / "new.epub", [("c.xhtml", "<p>One. Two.</p>")], title="New")
    with open(epub, "rb") as handle:
        response = setup["client"].post("/books", files={"file": ("new.epub", handle)})
    assert response.status_code == 201
    book = response.json()
    assert book["title"] == "New" and book["status"] == "pending" and book["sentences"] == 2
    assert setup["imports"] == [book["id"]]
    assert (setup["tmp"] / "uploads").exists()

    with open(epub, "rb") as handle:  # same file again: same book, extraction resumed
        again = setup["client"].post("/books", files={"file": ("new.epub", handle)})
    assert again.json()["id"] == book["id"]


def test_upload_rejects_non_epub(setup):
    response = setup["client"].post("/books", files={"file": ("x.epub", b"not a zip")})
    assert response.status_code == 400


def test_importing_the_module_does_not_open_the_real_database(tmp_path):
    import subprocess
    import sys
    missing = tmp_path / "never-created.db"
    code = ("import companion.api as a, pathlib, sys;"
            f" sys.exit(0 if 'app' not in vars(a) and not pathlib.Path({str(missing)!r}).exists() else 1)")
    env = {"COMPANION_DB": str(missing), "PATH": ""}
    assert subprocess.run([sys.executable, "-c", code], env=env, cwd=str(config.DATA_DIR.parent)
                          ).returncode == 0
