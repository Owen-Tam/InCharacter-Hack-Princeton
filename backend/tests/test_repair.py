import json

from companion import db
from companion.commit import commit_chunk
from companion.repair import main, recover_rejects
from helpers import fixture_candidates, resolve


def committed(conn, book):
    """The fixture committed, then a log entry whose records were rejected at the time."""
    result = commit_chunk(conn, book, resolve(conn, book, fixture_candidates(), 0, 4), final=True)
    return result.character_ids


def write_log(path, book, evidence, utterances=(), rejects=None):
    entry = {
        "book_id": book, "start": 0, "through": 4,
        "dialogue": {"characters": [], "aliases": [], "utterances": list(utterances)},
        "knowledge": {"evidence": list(evidence)},
        "rejects": rejects if rejects is not None else
                   [{"kind": "evidence", "index": i, "reason": "unknown key"}
                    for i in range(len(evidence))]
                   + [{"kind": "utterances", "index": i, "reason": "unknown key"}
                      for i in range(len(utterances))],
    }
    path.write_text(json.dumps(entry) + "\n" + json.dumps({**entry, "book_id": 999}) + "\n")


INVENTED = {"character": "theo_x", "claim": "Theo came back in.", "basis": "performed_action",
            "reported_by": None, "support": [{"para": 1, "text": "Theo returned"}]}


def test_recovers_records_the_old_resolver_rejected(conn, book, tmp_path):
    ids = committed(conn, book)
    conn.execute("UPDATE characters SET display_name='Theo X' WHERE id=?", (ids["theo"],))
    log = tmp_path / "log.jsonl"
    line = {"speaker": "theo_x", "addressee": None,
            "quotes": [{"para": 1, "text": "Empty"}], "attribution": []}
    write_log(log, book, [INVENTED], [line])
    before = conn.execute("SELECT count(*) FROM evidence").fetchone()[0]

    dry = recover_rejects(conn, book, log, dry_run=True)
    assert (dry.evidence_added, dry.utterances_added) == (1, 1)
    assert conn.execute("SELECT count(*) FROM evidence").fetchone()[0] == before

    summary = recover_rejects(conn, book, log)
    assert (summary.chunks, summary.rejected) == (1, 2)  # the other book's entry is ignored
    assert (summary.evidence_added, summary.utterances_added) == (1, 1)
    row = conn.execute("SELECT * FROM evidence WHERE claim='Theo came back in.'").fetchone()
    assert row["character_id"] == ids["theo"] and row["available_from"] == 3

    again = recover_rejects(conn, book, log)  # idempotent
    assert (again.evidence_added, again.utterances_added, again.skipped_existing) == (0, 0, 2)


def test_records_that_still_fail_are_reported_not_inserted(conn, book, tmp_path):
    committed(conn, book)
    log = tmp_path / "log.jsonl"
    ghost = {**INVENTED, "character": "nobody_at_all"}
    write_log(log, book, [ghost])
    summary = recover_rejects(conn, book, log)
    assert summary.evidence_added == 0
    assert "nobody_at_all" in summary.still_rejected[0][1]


def test_keys_of_characters_that_were_never_committed_are_skipped(conn, book, tmp_path):
    committed(conn, book)
    log = tmp_path / "log.jsonl"
    entry_evidence = {**INVENTED, "character": "ada"}
    entry = {"book_id": book, "start": 0, "through": 4,
             "dialogue": {"characters": [{"key": "ada", "name": "Ada",
                                          "first_mention": {"para": 1, "text": "Theo"}}],
                          "aliases": [], "utterances": []},
             "knowledge": {"evidence": [entry_evidence]},
             "rejects": [{"kind": "evidence", "index": 0, "reason": "x"}]}
    log.write_text(json.dumps(entry) + "\n")
    summary = recover_rejects(conn, book, log)
    assert summary.evidence_added == 0
    assert db.load_characters(conn, book)[-1].display_name != "Ada"  # no character created


def test_cli_dry_run(conn, book, tmp_path, capsys):
    ids = committed(conn, book)
    conn.execute("UPDATE characters SET display_name='Theo X' WHERE id=?", (ids["theo"],))
    log = tmp_path / "log.jsonl"
    write_log(log, book, [INVENTED])
    path = tmp_path / "c.db"
    file_conn = db.connect(path)
    conn.backup(file_conn)
    file_conn.close()
    assert main(["--book", str(book), "--db", str(path), "--log", str(log), "--dry-run"]) == 0
    assert "would add 1 evidence rows" in capsys.readouterr().out
