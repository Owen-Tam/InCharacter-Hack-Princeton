"""Real-model check of the guide's four-sentence fixture. Run with: pytest -m live

The model is not deterministic, so these assert the properties that matter
(page 13 of the guide), not exact rows.
"""

import json
import os

import pytest

from companion import config, db
from companion.worker import run_import
from helpers import EVIDENCE_SQL, FIXTURE_HTML, UTTERANCE_SQL, make_epub

config.load_env()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(config.API_KEY_VAR), reason="no API key"),
]


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    from companion.llm import AdaptionLLM

    folder = tmp_path_factory.mktemp("live")
    path = make_epub(folder / "letter.epub", [("Text/ch01.xhtml", FIXTURE_HTML)])
    conn = db.connect(folder / "live.db")
    db.init_schema(conn)
    llm = AdaptionLLM()
    summary = run_import(conn, path, llm=llm, log_path=folder / "log.jsonl")
    print(f"\nlive run: {llm.usage.calls} calls, {llm.usage.credits(llm.model):.5f} credits,"
          f" log at {folder / 'log.jsonl'}")
    names = {r["display_name"]: r["id"] for r in conn.execute("SELECT * FROM characters")}
    yield conn, summary, names
    conn.close()


def theo_claims(conn, names, pos):
    return conn.execute(EVIDENCE_SQL, {"book": 1, "who": names["Theo"], "pos": pos}).fetchall()


def test_import_finishes_with_both_characters(extracted):
    conn, summary, names = extracted
    assert summary.status == "ready"
    assert {"Theo", "Mara"} <= set(names)


def test_theos_line_is_attributed_and_eligible_at_its_sentence(extracted):
    conn, _, names = extracted
    # the heading is sentence 1, so "Empty again." ends sentence 4
    assert conn.execute(UTTERANCE_SQL, {"book": 1, "who": names["Theo"], "pos": 3}).fetchall() == []
    (row,) = conn.execute(UTTERANCE_SQL, {"book": 1, "who": names["Theo"], "pos": 4}).fetchall()
    assert db.quote_text(conn, 1, json.loads(row["sources"])) == "Empty again."


def test_theo_learns_where_the_letter_is_only_at_the_reveal(extracted):
    conn, _, names = extracted
    assert not any("atlas" in r["claim"] for r in theo_claims(conn, names, 4))
    assert any("atlas" in r["claim"] for r in theo_claims(conn, names, 5))


def test_theo_never_learns_who_hid_it(extracted):
    conn, _, names = extracted
    passage = db.load_passages(conn, 1)[2]
    hiding_lo, hiding_hi = passage.sentence_ends[0] + 1, passage.sentence_ends[1]
    for row in theo_claims(conn, names, 5):
        assert "hid" not in row["claim"].lower()
        for source in json.loads(row["sources"]):
            overlaps = source["lo"] < hiding_hi and source["hi"] > hiding_lo
            assert not overlaps, f"Theo's claim cites the hiding sentence: {row['claim']}"


def test_mara_knows_she_hid_it(extracted):
    conn, _, names = extracted
    rows = conn.execute(EVIDENCE_SQL, {"book": 1, "who": names["Mara"], "pos": 3}).fetchall()
    assert any("hid" in r["claim"].lower() for r in rows)
