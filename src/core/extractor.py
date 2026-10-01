import logging
from typing import Literal

from pydantic import Field

from src.core.formatting import format_roster_with_ids, format_threads_with_ids
from src.llm.base import LLMClient
from src.llm.structured import generate_model
from src.models import StrictModel
from src.prompts import render
from src.storage import repo

logger = logging.getLogger(__name__)

EXTRACTOR_MAX_TOKENS = 3000
TERMINAL_VALUES = frozenset({"dead", "missing", "resolved", "abandoned"})


class ExtractedRelationship(StrictModel):
    other: str
    description: str


class ExtractedCharacter(StrictModel):
    """Optional fields default so a model that omits an empty value still validates; status is the one required claim."""

    character_id: str | None = None
    name: str
    role: str | None = None
    status: Literal["alive", "dead", "missing", "unknown"]
    new_aliases: list[str] = Field(default_factory=list)
    new_traits: list[str] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    arc_stage: str | None = None
    appears_on_page: bool = True
    evidence: str = ""  # exact quote from the episode; required for any change of status


class ExtractedThread(StrictModel):
    thread_id: str | None = None
    description: str
    evidence: str  # required (no default) so schema-constrained decoding always fills it; updates without a real quote are ignored
    status: Literal["planted", "escalated", "resolved", "abandoned"]


class ExtractedFact(StrictModel):
    statement: str
    tags: list[str] = Field(default_factory=list)


class Extraction(StrictModel):
    summary: str
    characters: list[ExtractedCharacter] = Field(default_factory=list)
    threads: list[ExtractedThread] = Field(default_factory=list)
    new_facts: list[ExtractedFact] = Field(default_factory=list)


def extract(client: LLMClient, run_id: str, episode_no: int, text: str) -> Extraction:
    """What this episode's text establishes, resolved against the live roster and thread list."""
    prompt = render(
        "extractor",
        episode_no=str(episode_no),
        text=text,
        roster=format_roster_with_ids(repo.get_characters(run_id)),
        threads=format_threads_with_ids(repo.get_threads(run_id)),
    )
    result = generate_model(
        client, Extraction, run_id=run_id, stage="extract",
        system=prompt.system, prompt=prompt.user, max_tokens=EXTRACTOR_MAX_TOKENS, episode_no=episode_no,
    )
    logger.info("extracted episode=%d characters=%d threads=%d facts=%d", episode_no, len(result.characters), len(result.threads), len(result.new_facts))
    return result


def _hard_facts(extraction: Extraction) -> tuple[dict[tuple[str, str, str], str], dict[str, str]]:
    """Plot-level claims only (character status/introduction, thread status/introduction). Free-text
    fields (aliases, traits, relationships, arc stage, facts) paraphrase between runs, so comparing them
    would flag cosmetic edits as story changes."""
    claims: dict[tuple[str, str, str], str] = {}
    names: dict[str, str] = {}
    for c in extraction.characters:
        ref = c.character_id or f"new:{c.name.lower()}"
        names[ref] = c.name
        claims[("character", ref, "status")] = c.status
        if c.character_id is None:
            claims[("character", ref, "introduced")] = "yes"
    for t in extraction.threads:
        ref = t.thread_id or f"new:{t.description.lower()}"
        names[ref] = t.description
        claims[("thread", ref, "status")] = t.status
        if t.thread_id is None:
            claims[("thread", ref, "introduced")] = "yes"
    return claims, names


def hard_differences(old: Extraction, new: Extraction) -> list[str]:
    """Plot-relevant differences between two readings of an episode. A value change is always hard; a
    claim present on only one side is hard only for terminal events (a death or resolution appearing
    or vanishing) or an introduction."""
    old_claims, old_names = _hard_facts(old)
    new_claims, new_names = _hard_facts(new)
    names = {**old_names, **new_names}
    diffs = []
    for key in sorted(old_claims.keys() | new_claims.keys()):
        before, after = old_claims.get(key), new_claims.get(key)
        if before == after:
            continue
        kind, ref, field = key
        one_sided = before is None or after is None
        if one_sided and field != "introduced" and (before or after) not in TERMINAL_VALUES:
            continue
        diffs.append(f"{kind} '{names[ref]}': {field} {before or 'absent'} -> {after or 'absent'}")
    return diffs
