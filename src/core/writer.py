import logging
import re

from pydantic import BaseModel

from src.core.formatting import format_context
from src.llm.base import LLMClient, call_with_tracking
from src.models import EpisodeContext
from src.prompts import render

logger = logging.getLogger(__name__)

WRITER_MAX_TOKENS = 1800
HEADING_LINE = re.compile(r"^(#+\s.*|\*{0,2}episode\s+\d+.*)$", re.IGNORECASE)


class Revision(BaseModel):
    """Fix-this instructions for a rewrite: critic blockers or a human rejection reason."""

    previous_draft: str
    notes: list[str]


def _revision_section(revision: Revision | None) -> str:
    if revision is None:
        return ""
    notes = "\n".join(f"- {n}" for n in revision.notes)
    return (
        "REVISION REQUIRED. Rewrite the whole episode, fixing every issue below while keeping what works.\n"
        f"Issues to fix:\n{notes}\n\nPrevious draft:\n{revision.previous_draft}"
    )


def _clean(text: str) -> str:
    lines = text.strip().splitlines()
    while lines and HEADING_LINE.match(lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


def draft_episode(client: LLMClient, context: EpisodeContext, revision: Revision | None = None) -> str:
    prompt = render("writer", **format_context(context), revision_section=_revision_section(revision))
    response = call_with_tracking(
        client,
        run_id=context.run_id,
        stage="revise" if revision else "draft",
        system=prompt.system,
        prompt=prompt.user,
        max_tokens=WRITER_MAX_TOKENS,
        episode_no=context.episode_no,
    )
    return _clean(response.text)
