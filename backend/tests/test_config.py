import os

from companion import config


def test_parse_env_handles_spacing_quotes_and_comments():
    text = """
    # a comment
    ADAPTION_LAB_KEY = "pt_live_abc "
    export OTHER=plain
    SINGLE='quoted value'
    TRAILING=value # note
    EMPTY=
    not a pair
    """
    assert config.parse_env(text) == {
        "ADAPTION_LAB_KEY": "pt_live_abc ",
        "OTHER": "plain",
        "SINGLE": "quoted value",
        "TRAILING": "value",
        "EMPTY": "",
    }


def test_load_env_does_not_override_existing_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FC_TEST_A=from_file\nFC_TEST_B=from_file\n")
    monkeypatch.setenv("FC_TEST_A", "from_shell")
    monkeypatch.delenv("FC_TEST_B", raising=False)
    config.load_env(env)
    assert os.environ["FC_TEST_A"] == "from_shell"
    assert os.environ["FC_TEST_B"] == "from_file"
    monkeypatch.delenv("FC_TEST_B")


def test_find_env_file_walks_up(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert config.find_env_file(nested / "file.py") == tmp_path / ".env"


def test_version_names_prompt_and_model():
    assert config.VERSION == f"{config.PROMPT_VERSION}/{config.EXTRACT_MODEL}"


def test_prompts_exist():
    for name in ("dialogue", "knowledge", "voice"):
        assert len(config.prompt(name)) > 200
