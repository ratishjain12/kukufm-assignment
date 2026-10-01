"""Single source of truth for all domain types. Nothing outside this module defines
a shadow dataclass for these concepts — storage, core, and cli all import from here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["character", "thread", "fact"]
CharacterStatus = Literal["alive", "dead", "missing", "unknown"]
ThreadStatus = Literal["planned", "planted", "escalated", "resolved", "abandoned"]
DirectiveScope = Literal["permanent", "next_n_episodes"]


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    """Base for schemas sent to an LLM: extra="forbid" emits additionalProperties: false."""

    model_config = ConfigDict(extra="forbid")


class Beat(BaseModel):
    """One planned story unit. Can span multiple episodes."""

    beat_id: str = Field(default_factory=lambda: gen_id("beat"))
    episode_start: int
    episode_end: int
    title: str
    summary: str
    turning_point: bool = False
    character_ids: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)


class ArcPlan(BaseModel):
    run_id: str
    premise: str
    overview: str = ""
    total_episodes: int = 200
    beats: list[Beat] = Field(default_factory=list)
    status: Literal["draft", "approved"] = "draft"
    version: int = 1

    def beat_for_episode(self, episode_no: int) -> Beat | None:
        for beat in self.beats:
            if beat.episode_start <= episode_no <= beat.episode_end:
                return beat
        return None


class Character(BaseModel):
    character_id: str = Field(default_factory=lambda: gen_id("char"))
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str = "supporting"
    status: CharacterStatus = "alive"
    traits: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)  # other character_id -> description
    planned_arc: str = ""
    arc_stage: str = ""
    last_seen_episode: int = 0


class Thread(BaseModel):
    thread_id: str = Field(default_factory=lambda: gen_id("thread"))
    description: str
    status: ThreadStatus = "planted"
    planted_episode: int
    last_touched_episode: int
    resolution_target_episode: int | None = None


class Fact(BaseModel):
    fact_id: str = Field(default_factory=lambda: gen_id("fact"))
    statement: str
    established_episode: int
    tags: list[str] = Field(default_factory=list)


class Directive(BaseModel):
    """Persistent HITL steering feedback, not tied to a single episode."""

    directive_id: str = Field(default_factory=lambda: gen_id("dir"))
    text: str
    created_at_episode: int
    scope: DirectiveScope = "permanent"
    expires_after_episode: int | None = None  # absolute episode number, resolved at creation time
    status: Literal["active", "retired"] = "active"

    def active_for(self, episode_no: int) -> bool:
        if self.status != "active":
            return False
        if self.scope == "next_n_episodes" and self.expires_after_episode is not None:
            return episode_no <= self.expires_after_episode
        return True


class Episode(BaseModel):
    episode_no: int
    run_id: str
    beat_id: str
    text: str
    summary: str = ""
    status: Literal["drafted", "critiqued", "approved", "edited", "rejected", "stale"] = "drafted"
    critic_notes: list[str] = Field(default_factory=list)
    revision_count: int = 0
    human_feedback: str | None = None
    source: Literal["generated", "human_edited"] = "generated"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


FINALIZED_STATUSES = ("approved", "edited")


class FieldChange(BaseModel):
    """One atomic state mutation attributed to a specific episode's text.

    Generic across entity types so diffing a retroactive edit is a set comparison
    on (entity_id, field) pairs, not bespoke per-entity-type logic.
    """

    entity_type: EntityType
    entity_id: str
    field: str
    old_value: str | None
    new_value: str


class StateDelta(BaseModel):
    """What extracting episode N's text changed in the world state. Persisted per
    episode (not just merged into the live tables) so a later retroactive edit to
    episode N can diff its new delta against this original one."""

    run_id: str
    episode_no: int
    changes: list[FieldChange] = Field(default_factory=list)
    new_entity_ids: list[str] = Field(default_factory=list)
    extraction: dict[str, Any] = Field(default_factory=dict)  # raw extractor output, diffed on retroactive edits


class LLMCallLog(BaseModel):
    call_id: str = Field(default_factory=lambda: gen_id("call"))
    run_id: str
    episode_no: int | None = None
    stage: Literal["plan", "draft", "critic", "extract", "revise", "summarize"]
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    retry_count: int = 0
    timestamp: datetime = Field(default_factory=utc_now)


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: gen_id("run"))
    premise: str
    current_episode: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BlockSummary(BaseModel):
    """LLM summary of one completed 10-episode block, built from per-episode summaries
    (never from other block summaries) so summarization depth stays at two levels."""

    block_no: int
    episode_start: int
    episode_end: int
    summary: str


class EpisodeSummary(BaseModel):
    episode_no: int
    summary: str


class CriticResult(BaseModel):
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    minor_notes: list[str] = Field(default_factory=list)

    @property
    def notes(self) -> list[str]:
        return [*self.blockers, *self.minor_notes]


class EpisodeContext(BaseModel):
    """Everything writer, critic and extractor see for episode N. Built once by memory.py
    so all three work from identical state."""

    run_id: str
    episode_no: int
    premise: str
    overview: str
    beat: Beat
    upcoming_beats: list[Beat] = Field(default_factory=list)
    relevant_characters: list[Character] = Field(default_factory=list)
    roster: list[Character] = Field(default_factory=list)
    open_threads: list[Thread] = Field(default_factory=list)
    neglected_threads: list[Thread] = Field(default_factory=list)
    threads_to_introduce: list[Thread] = Field(default_factory=list)
    directives: list[Directive] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    author_notes: list[Fact] = Field(default_factory=list)
    block_summaries: list[BlockSummary] = Field(default_factory=list)
    episode_summaries: list[EpisodeSummary] = Field(default_factory=list)
    recent_episodes: list[Episode] = Field(default_factory=list)
