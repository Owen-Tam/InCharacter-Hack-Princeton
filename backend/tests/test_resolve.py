import pytest

from companion import db
from companion.resolve import CandidateError, Rejected, locate, resolve_chunk
from helpers import FIXTURE_TEXT, PASSAGE_ID, fixture_candidates, resolve


def src(lo, hi, role):
    return {"passage_id": PASSAGE_ID, "lo": lo, "hi": hi, "role": role}


def theo_says(**changes):
    utterance = {
        "speaker": "theo",
        "quotes": [{"para": 1, "text": "Empty again."}],
        "attribution": [{"para": 1, "text": "Theo returned and said"}],
    }
    utterance.update(changes)
    return {
        "characters": [{"key": "theo", "name": "Theo",
                        "first_mention": {"para": 1, "text": "Theo stepped outside"}}],
        "utterances": [utterance],
    }


def reasons(chunk):
    return [(r.kind, r.index) for r in chunk.rejects]


# -- the guide's example records ------------------------------------------------

def test_fixture_resolves_to_the_guides_offsets(conn, book):
    chunk = resolve(conn, book, fixture_candidates(), 0, 4)
    assert chunk.rejects == []

    assert [(c.key, c.display_name, c.first_seen) for c in chunk.characters] == [
        ("theo", "Theo", 1), ("mara", "Mara", 2),
    ]

    (utterance,) = chunk.utterances
    assert utterance.speaker == "theo" and utterance.addressee is None
    assert utterance.available_from == 3
    assert utterance.sources == [src(81, 93, "quote"), src(56, 78, "attribution")]

    hid, said, shown = chunk.evidence
    assert (hid.character, hid.basis, hid.available_from) == ("mara", "performed_action", 2)
    assert hid.sources == [src(22, 55, "support")]
    assert (said.basis, said.available_from) == ("said", 3)
    assert shown.claim == "Theo was shown the letter inside the atlas."
    assert (shown.character, shown.basis, shown.reported_by) == ("theo", "observed", None)
    assert shown.available_from == 4
    assert shown.sources == [src(95, 139, "support"), src(56, 69, "support")]


# -- locating text ----------------------------------------------------------------

def test_locate_exact():
    assert locate(FIXTURE_TEXT, "Empty again.") == (81, 93)


def test_locate_matches_straight_quotes_against_curly_text():
    assert locate(FIXTURE_TEXT, 'said, "Empty again."') == (74, 94)


def test_locate_needs_occurrence_for_repeated_text():
    with pytest.raises(Rejected, match="2 times"):
        locate(FIXTURE_TEXT, "Theo")
    assert locate(FIXTURE_TEXT, "Theo", 1) == (0, 4)
    assert locate(FIXTURE_TEXT, "Theo", 2) == (56, 60)


@pytest.mark.parametrize("occurrence", [0, 3, True, "2"])
def test_locate_rejects_bad_occurrence(occurrence):
    with pytest.raises(Rejected):
        locate(FIXTURE_TEXT, "Theo", occurrence)


@pytest.mark.parametrize("needle", ["", "Theo hid the letter", "empty again."])
def test_locate_rejects_text_that_is_not_there(needle):
    with pytest.raises(Rejected):
        locate(FIXTURE_TEXT, needle)


def test_quotation_marks_are_left_out_of_the_quote_range(conn, book):
    for quoted in ("“Empty again.”", '"Empty again."', " “Empty again."):
        chunk = resolve(conn, book, theo_says(quotes=[{"para": 1, "text": quoted}]), 0, 4)
        assert chunk.utterances[0].sources[0] == src(81, 93, "quote")


def test_offsets_count_code_points_not_utf16_units(conn):
    book_id = db.create_book(conn, "Emoji", "emoji-hash", "v1")
    text = "\U0001F600 “Hi.” she said."
    db.insert_passage(conn, book_id, id=5, paragraph_pos=1, href="a", para_no=0,
                      first_sentence=1, sentence_ends=[len(text)], text=text)
    chunk = resolve(conn, book_id, {"utterances": [
        {"speaker": None, "quotes": [{"para": 1, "text": "Hi."}]}]}, 0, 1)
    source = chunk.utterances[0].sources[0]
    assert (source["lo"], source["hi"]) == (3, 6)
    assert text[3:6] == "Hi."


# -- utterances ---------------------------------------------------------------------

def test_quote_fragments_keep_reading_order_then_attribution(conn, book):
    chunk = resolve(conn, book, theo_says(
        quotes=[{"para": 1, "text": "Empty"}, {"para": 1, "text": "again."}]), 0, 4)
    assert chunk.utterances[0].sources == [
        src(81, 86, "quote"), src(87, 93, "quote"), src(56, 78, "attribution"),
    ]


def test_a_later_speaker_tag_pushes_eligibility_past_the_quote(conn, book):
    chunk = resolve(conn, book, theo_says(
        attribution=[{"para": 1, "text": "Mara showed him"}]), 0, 4)
    assert chunk.utterances[0].available_from == 4


REPEATED = ("“Allen,” Mr. Summers said. “Anderson,” Mr. Summers said."
            " “Bentham,” Mr. Summers said.")


def test_repeated_speech_tag_resolves_to_the_copy_next_to_its_quote(conn):
    book_id = db.create_book(conn, "Tags", "tags-hash", "v1")
    from companion.sentences import split_sentences
    db.insert_passage(conn, book_id, id=7, paragraph_pos=1, href="a", para_no=0,
                      first_sentence=1, sentence_ends=split_sentences(REPEATED), text=REPEATED)
    candidates = {"utterances": [
        {"speaker": None, "quotes": [{"para": 1, "text": name}],
         "attribution": [{"para": 1, "text": "Mr. Summers said"}]}
        for name in ("Allen,", "Anderson,", "Bentham,")]}
    chunk = resolve(conn, book_id, candidates, 0, 3)
    assert chunk.rejects == []
    tags = [u.sources[1] for u in chunk.utterances]
    assert [REPEATED[t["lo"]:t["lo"] + 3] for t in tags] == ["Mr.", "Mr.", "Mr."]
    assert [t["lo"] for t in tags] == sorted({t["lo"] for t in tags})  # three different copies
    assert [u.available_from for u in chunk.utterances] == [1, 2, 3]


def test_explicit_occurrence_still_wins_over_proximity(conn):
    book_id = db.create_book(conn, "Tags", "tags-hash", "v1")
    db.insert_passage(conn, book_id, id=7, paragraph_pos=1, href="a", para_no=0,
                      first_sentence=1, sentence_ends=[len(REPEATED)], text=REPEATED)
    chunk = resolve(conn, book_id, {"utterances": [
        {"speaker": None, "quotes": [{"para": 1, "text": "Allen,"}],
         "attribution": [{"para": 1, "text": "Mr. Summers said", "occurrence": 3}]}]}, 0, 1)
    assert chunk.utterances[0].sources[1]["lo"] == REPEATED.rindex("Mr. Summers said")


def test_support_span_with_added_quote_marks_is_still_found(conn, book):
    chunk = resolve(conn, book, evidence(support=[{"para": 1, "text": '"Theo returned and"'}]), 0, 4)
    assert chunk.rejects == []
    assert chunk.evidence[0].sources == [src(56, 73, "support")]


def test_unassigned_line_is_kept_with_no_speaker(conn, book):
    chunk = resolve(conn, book, theo_says(speaker=None, attribution=None), 0, 4)
    assert chunk.rejects == []
    assert chunk.utterances[0].speaker is None
    assert chunk.utterances[0].sources == [src(81, 93, "quote")]


def test_hallucinated_quote_is_rejected_and_the_rest_survives(conn, book):
    candidates = fixture_candidates()
    candidates["utterances"].append(
        {"speaker": "mara", "quotes": [{"para": 1, "text": "I hid it, Theo."}]})
    chunk = resolve(conn, book, candidates, 0, 4)
    assert reasons(chunk) == [("utterances", 1)]
    assert "not found" in chunk.rejects[0].reason
    assert len(chunk.utterances) == 1 and len(chunk.evidence) == 3


@pytest.mark.parametrize("changes", [
    {"quotes": []},
    {"quotes": None},
    {"quotes": "Empty again."},
    {"quotes": [{"para": 2, "text": "Empty again."}]},
    {"quotes": [{"para": "1", "text": "Empty again."}]},
    {"quotes": [{"para": 1, "text": "“”"}]},
    {"quotes": [{"para": 1}]},
    {"quotes": ["Empty again."]},
    {"speaker": "nobody"},
    {"speaker": 999},
    {"speaker": True},
    {"addressee": "nobody"},
    {"attribution": [{"para": 1, "text": "Theo whispered"}]},
])
def test_bad_utterances_are_rejected(conn, book, changes):
    chunk = resolve(conn, book, theo_says(**changes), 0, 4)
    assert chunk.utterances == []
    assert reasons(chunk) == [("utterances", 0)]


# -- characters ---------------------------------------------------------------------

def committed_theo(conn, book):
    return conn.execute(
        "INSERT INTO characters (book_id, display_name, first_seen) VALUES (?,?,1)",
        (book, "Theo"),
    ).lastrowid


def test_repeated_first_mention_uses_the_earliest_occurrence(conn, book):
    candidates = theo_says()
    candidates["characters"][0]["first_mention"] = {"para": 1, "text": "Theo"}  # appears twice
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == []
    assert chunk.characters[0].first_seen == 1
    candidates["characters"][0]["first_mention"]["occurrence"] = 2
    assert resolve(conn, book, candidates, 0, 4).characters[0].first_seen == 3


@pytest.mark.parametrize("name", ["I", "me", " You ", "HER"])
def test_pronoun_is_never_a_character_name(conn, book, name):
    candidates = theo_says()
    candidates["characters"][0]["name"] = name
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.characters == []
    assert "cannot be named" in chunk.rejects[0].reason


def test_narrator_character_is_allowed(conn, book):
    candidates = theo_says()
    candidates["characters"][0].update(key="narrator", name="the narrator")
    candidates["utterances"][0]["speaker"] = "narrator"
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == [] and chunk.characters[0].display_name == "the narrator"


def test_existing_character_is_referenced_by_id(conn, book):
    theo = committed_theo(conn, book)
    candidates = {"utterances": [theo_says(speaker=theo)["utterances"][0]]}
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == [] and chunk.characters == []
    assert chunk.utterances[0].speaker == theo


def test_quoted_numeric_id_is_accepted_unless_it_is_a_declared_key(conn, book):
    theo = committed_theo(conn, book)
    candidates = {"utterances": [theo_says(speaker=str(theo))["utterances"][0]]}
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == [] and chunk.utterances[0].speaker == theo

    declared = theo_says(speaker=str(theo))
    declared["characters"][0]["key"] = str(theo)
    declared["characters"][0]["name"] = "Mara"
    declared["characters"][0]["first_mention"] = {"para": 1, "text": "Mara hid"}
    chunk = resolve(conn, book, declared, 0, 4)
    assert chunk.utterances[0].speaker == str(theo)  # the key wins


def test_invented_key_matching_a_known_name_maps_to_that_character(conn, book):
    conn.execute("INSERT INTO characters (book_id, display_name, first_seen) VALUES (?,?,1)",
                 (book, "Mr. Theo Summers"))
    theo = conn.execute("SELECT id FROM characters").fetchone()[0]
    for key in ("mr_theo_summers", "MR. THEO SUMMERS", "mr theo summers", "summers",
                "theo_summers"):
        chunk = resolve(conn, book, evidence(character=key), 0, 4)
        assert [e.character for e in chunk.evidence] == [theo], key
    for key in ("mr_summers_jr", "mr_ada"):  # different names stay unknown
        chunk = resolve(conn, book, evidence(character=key), 0, 4)
        assert chunk.evidence == [], key


def test_short_invented_key_maps_to_the_one_character_it_names(conn, book):
    for name, aliases in (("Mr. Gatsby", "[]"), ("Tom Buchanan", "[]"),
                          ("Miss Baker", '[{"name": "Jordan Baker"}]'),
                          ("Mrs. Buchanan", "[]")):
        conn.execute("INSERT INTO characters (book_id, display_name, first_seen, aliases)"
                     " VALUES (?,?,1,?)", (book, name, aliases))
    ids = {r["display_name"]: r["id"] for r in conn.execute("SELECT * FROM characters")}
    for key, expected in (("gatsby", "Mr. Gatsby"), ("tom", "Tom Buchanan"),
                          ("jordan", "Miss Baker"), ("GATSBY", "Mr. Gatsby")):
        chunk = resolve(conn, book, evidence(character=key), 0, 4)
        assert [e.character for e in chunk.evidence] == [ids[expected]], key
    # "buchanan" fits both Buchanans and "baker" fits one name of one character
    assert resolve(conn, book, evidence(character="buchanan"), 0, 4).evidence == []
    chunk = resolve(conn, book, evidence(character="baker"), 0, 4)
    assert [e.character for e in chunk.evidence] == [ids["Miss Baker"]]
    for key in ("cody", "mr", "jo"):  # unknown, a bare title, too short
        assert resolve(conn, book, evidence(character=key), 0, 4).evidence == [], key


def test_redeclared_character_maps_to_the_existing_row(conn, book):
    theo = committed_theo(conn, book)
    candidates = theo_says()
    candidates["characters"][0]["name"] = "  theo "
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == [] and chunk.characters == []
    assert chunk.utterances[0].speaker == theo


def test_same_name_declared_twice_in_a_chunk_makes_one_character(conn, book):
    candidates = theo_says(speaker="theo2")
    candidates["characters"].append(
        {"key": "theo2", "name": "THEO", "first_mention": {"para": 1, "text": "Theo returned"}})
    chunk = resolve(conn, book, candidates, 0, 4)
    assert [c.key for c in chunk.characters] == ["theo"]
    assert chunk.utterances[0].speaker == "theo"


@pytest.mark.parametrize("character", [
    {"key": "", "name": "Mara", "first_mention": {"para": 1, "text": "Mara hid"}},
    {"key": "mara", "name": " ", "first_mention": {"para": 1, "text": "Mara hid"}},
    {"key": "mara", "name": "Mara"},
    {"key": "mara", "name": "Mara", "first_mention": {"para": 1, "text": "Mara smiled"}},
    {"key": "theo", "name": "Mara", "first_mention": {"para": 1, "text": "Mara hid"}},
    "Mara",
])
def test_bad_characters_are_rejected(conn, book, character):
    candidates = theo_says()
    candidates["characters"].append(character)
    chunk = resolve(conn, book, candidates, 0, 4)
    assert [c.key for c in chunk.characters] == ["theo"]
    assert reasons(chunk) == [("characters", 1)]


def test_records_naming_a_rejected_character_are_rejected_too(conn, book):
    candidates = theo_says()
    candidates["characters"][0]["first_mention"]["text"] = "Theodore"
    chunk = resolve(conn, book, candidates, 0, 4)
    assert reasons(chunk) == [("characters", 0), ("utterances", 0)]


def test_alias_is_dated_and_sourced(conn, book):
    candidates = theo_says()
    candidates["aliases"] = [
        {"character": "theo", "name": "him", "support": [{"para": 1, "text": "showed him"}]}]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == []
    (alias,) = chunk.aliases
    assert alias.character == "theo"
    assert alias.alias == {
        "name": "him", "available_from": 4, "sources": [src(100, 110, "support")]}


def test_alias_already_known_is_dropped_and_contested_alias_is_rejected(conn, book):
    candidates = fixture_candidates()
    candidates["aliases"] = [
        {"character": "theo", "name": "theo", "support": [{"para": 1, "text": "Theo returned"}]},
        {"character": "theo", "name": "Mara", "support": [{"para": 1, "text": "Theo returned"}]},
        {"character": "theo", "name": "T", "support": []},
    ]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.aliases == []
    assert reasons(chunk) == [("aliases", 1), ("aliases", 2)]


# -- evidence -----------------------------------------------------------------------

def evidence(**changes):
    record = {"character": "theo", "claim": "Theo returned.", "basis": "performed_action",
              "support": [{"para": 1, "text": "Theo returned"}]}
    record.update(changes)
    candidates = theo_says()
    candidates["utterances"] = []
    candidates["evidence"] = [record]
    return candidates


def test_reported_claim_keeps_its_source_speaker(conn, book):
    candidates = fixture_candidates()
    candidates["evidence"] = [{
        "character": "theo", "claim": "Mara told Theo the letter was in the atlas.",
        "basis": "was_told", "reported_by": "mara",
        "support": [{"para": 1, "text": "Mara showed him the letter inside the atlas."}]}]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == []
    assert chunk.evidence[0].reported_by == "mara"


def test_shared_claim_becomes_one_row_per_character(conn, book):
    candidates = fixture_candidates()
    candidates["evidence"] = [{
        "characters": ["theo", "mara", "theo"], "claim": "Theo returned.",
        "basis": "observed", "reported_by": None,
        "support": [{"para": 1, "text": "Theo returned"}]}]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.rejects == []
    assert [e.character for e in chunk.evidence] == ["theo", "mara"]
    assert chunk.evidence[0].sources == chunk.evidence[1].sources
    assert chunk.evidence[0].sources is not chunk.evidence[1].sources


def test_bad_name_in_a_shared_claim_drops_only_that_character(conn, book):
    candidates = fixture_candidates()
    candidates["evidence"] = [{
        "characters": ["theo", "ghost"], "claim": "Theo returned.", "basis": "observed",
        "support": [{"para": 1, "text": "Theo returned"}]}]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert [e.character for e in chunk.evidence] == ["theo"]
    assert reasons(chunk) == [("evidence", 0)]
    assert "ghost" in chunk.rejects[0].reason


@pytest.mark.parametrize("characters", [[], "theo", ["ghost"], [None]])
def test_shared_claim_with_no_valid_character_is_rejected(conn, book, characters):
    candidates = fixture_candidates()
    candidates["evidence"] = [{
        "characters": characters, "claim": "Theo returned.", "basis": "observed",
        "support": [{"para": 1, "text": "Theo returned"}]}]
    chunk = resolve(conn, book, candidates, 0, 4)
    assert chunk.evidence == []
    assert reasons(chunk) == [("evidence", 0)]


@pytest.mark.parametrize("changes", [
    {"character": None},
    {"character": "mara"},
    {"claim": "  "},
    {"claim": None},
    {"basis": "guessed"},
    {"basis": None},
    {"reported_by": "nobody"},
    {"support": []},
    {"support": [{"para": 1, "text": "Theo saw Mara hide it"}]},
    {"support": [{"para": 1, "text": "the letter"}]},
])
def test_bad_evidence_is_rejected(conn, book, changes):
    chunk = resolve(conn, book, evidence(**changes), 0, 4)
    assert chunk.evidence == []
    assert reasons(chunk) == [("evidence", 0)]


# -- chunk windows ------------------------------------------------------------------

def test_record_citing_text_past_the_chunk_end_is_rejected(conn, book):
    chunk = resolve(conn, book, theo_says(), 0, 2)
    assert chunk.utterances == []
    assert "past the chunk end" in chunk.rejects[0].reason


def test_record_wholly_inside_the_prefix_is_rejected(conn, book):
    theo = committed_theo(conn, book)
    candidates = {"evidence": [{
        "character": theo, "claim": "Theo stepped outside.", "basis": "performed_action",
        "support": [{"para": 1, "text": "Theo stepped outside."}]}]}
    chunk = resolve(conn, book, candidates, 2, 4)
    assert chunk.evidence == []
    assert "earlier chunk" in chunk.rejects[0].reason


def test_prefix_text_may_be_cited_as_context(conn, book):
    theo = committed_theo(conn, book)
    candidates = {"evidence": [{
        "character": theo, "claim": "Theo was shown the letter inside the atlas.",
        "basis": "observed",
        "support": [
            {"para": 1, "text": "Mara showed him the letter inside the atlas."},
            {"para": 1, "text": "Theo stepped outside."},
        ]}]}
    chunk = resolve(conn, book, candidates, 2, 4)
    assert chunk.rejects == []
    assert chunk.evidence[0].available_from == 4
    assert chunk.evidence[0].sources[1] == src(0, 21, "support")


# -- input shape --------------------------------------------------------------------

@pytest.mark.parametrize("candidates", [None, [], "{}", {"utterances": {}}, {"evidence": "x"}])
def test_malformed_candidates_raise(conn, book, candidates):
    with pytest.raises(CandidateError):
        resolve(conn, book, candidates, 0, 4)


def test_empty_candidates_resolve_to_an_empty_chunk(conn, book):
    chunk = resolve(conn, book, {"characters": None}, 0, 4)
    assert (chunk.start, chunk.through) == (0, 4)
    assert not (chunk.characters or chunk.utterances or chunk.evidence or chunk.rejects)


@pytest.mark.parametrize("window", [(4, 4), (5, 4), (-1, 4), (0, True)])
def test_invalid_window_raises(window):
    with pytest.raises(ValueError):
        resolve_chunk({}, {}, [], *window)
