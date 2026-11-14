"""Settings shared by the extraction pipeline."""

from __future__ import annotations

import os
from pathlib import Path

BASE_URL = "https://api.prod.adaptionlabs.ai/api/v1"
API_KEY_VAR = "ADAPTION_LAB_KEY"
EXTRACT_MODEL = "deepseek-v4-flash"

# Bump PROMPT_VERSION whenever a prompt in companion/prompts/ changes meaningfully.
# A new version creates a new books row instead of mixing outputs, as the guide asks.
PROMPT_VERSION = "extract-v2"  # v2: shared claims, last-known presence in the registry
VERSION = f"{PROMPT_VERSION}/{EXTRACT_MODEL}"

# Credits per million tokens (input, output) from Adaption's model list.
PRICES = {
    "deepseek-v4-flash": (0.045, 0.14),
    "qwen3.5-397b": (0.55, 3.50),
    "qwen3.8-27b": (0.039, 1.25),
    "glm-5.2": (1.40, 4.40),
    "glm-53-flash": (0.15, 0.50),
    "gemma-4-31b-it": (0.09, 0.34),
    "gemma-4-26b-a4b-it": (0.077, 0.26),
}

# Reasoning effort per pass (None = the model's default). On a dense Lottery
# chunk, "low" for the knowledge pass was ~30% faster with equally good claims.
# Revisit with None if secrets start leaking to characters who weren't present.
DIALOGUE_EFFORT = "low"
KNOWLEDGE_EFFORT: str | None = "low"
VOICE_EFFORT = "low"

PROMPTS_DIR = Path(__file__).with_name("prompts")


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, allowing spaces around ``=``, quotes and comments."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def find_env_file(start: Path | None = None) -> Path | None:
    here = (start or Path(__file__)).resolve()
    for folder in [here, *here.parents]:
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env(path: Path | None = None) -> None:
    """Load the nearest ``.env`` into ``os.environ`` without overriding existing values."""
    path = path or find_env_file()
    if path is None:
        return
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)


def prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
