from pathlib import Path
from string import Template
from typing import NamedTuple

PROMPT_DIR = Path(__file__).parent
USER_MARKER = "=== USER ==="


class Prompt(NamedTuple):
    system: str
    user: str


def render(name: str, **variables: str) -> Prompt:
    """Loads prompts/<name>.md and fills `$var` placeholders (write a literal `$` as `$$`).
    The file holds the system prompt, a line `=== USER ===`, then the user prompt."""
    raw = (PROMPT_DIR / f"{name}.md").read_text()
    system, _, user = raw.partition(USER_MARKER)
    return Prompt(
        system=Template(system.strip()).substitute(variables),
        user=Template(user.strip()).substitute(variables),
    )
