# 01 — Data Models (`src/models.py`)

Single source of truth. Every other module imports from here — no shadow dataclasses in
`storage/`, `core/`, or `cli.py`. Pydantic v2 `BaseModel` for all of these.

## ArcPlan
- `run_id: str`
- `premise: str`
- `total_episodes: int` (default 200)
- `beats: list[Beat]`
- `status: Literal["draft", "approved"]`
- `version: int` (bumped on human edit/regenerate)

## Beat
One planned story unit, not 1:1 with an episode necessarily — a beat can span a few episodes.
- `beat_id: str`
- `episode_range: tuple[int, int]`
- `title: str`
- `summary: str`
- `turning_point: bool`
- `character_arcs_touched: list[str]` (character ids)
- `threads_touched: list[str]` (thread ids introduced/escalated/resolved here)

## Character
- `character_id: str`
- `name: str`
- `role: str` (protagonist, antagonist, supporting, etc.)
- `status: Literal["alive", "dead", "missing", "unknown"]`
- `traits: list[str]`
- `relationships: dict[str, str]` (other character_id -> relationship description)
- `last_seen_episode: int`
- `arc_stage: str` (free text, e.g. "denial" / "confronting past")

## Thread
Open-thread tracker — the explicit mechanism against "dropped threads."
- `thread_id: str`
- `description: str`
- `status: Literal["planted", "escalated", "resolved", "abandoned"]`
- `planted_episode: int`
- `last_touched_episode: int`
- `resolution_target_episode: int | None` (from arc plan, for staleness alerts)

## Fact
Canonical, non-character world facts (place names, dates, physical details) kept separate
from character state because they're queried differently (critic checks drafts against raw
fact strings, not against relationship graphs).
- `fact_id: str`
- `statement: str`
- `established_episode: int`
- `tags: list[str]`

## Directive
Persistent HITL steering feedback — not tied to a single episode.
- `directive_id: str`
- `text: str` (e.g. "slow down the romance", "kill off this character by ep 60")
- `created_at_episode: int`
- `scope: Literal["permanent", "next_n_episodes"]`
- `expires_after_episode: int | None`
- `status: Literal["active", "retired"]`

## Episode
- `episode_no: int`
- `run_id: str`
- `beat_id: str`
- `text: str`
- `word_count: int`
- `status: Literal["drafted", "critiqued", "approved", "edited", "rejected", "stale"]`
- `critic_notes: list[str]`
- `revision_count: int`
- `human_feedback: str | None`
- `source: Literal["generated", "human_edited"]`

## LLMCallLog
Traceability requirement — one row per LLM call.
- `call_id: str`
- `run_id: str`
- `episode_no: int | None`
- `stage: Literal["plan", "draft", "critic", "extract", "revise"]`
- `provider: str`
- `model: str`
- `prompt_tokens: int`
- `completion_tokens: int`
- `cost_usd: float`
- `latency_ms: int`
- `retry_count: int`
- `timestamp: datetime`

## RunState
Top-level pointer used for resume.
- `run_id: str`
- `premise: str`
- `current_episode: int` (next episode to generate)
- `plan_status: Literal["pending", "approved"]`
- `created_at: datetime`
- `updated_at: datetime`

## Implementation notes (deviations from original spec, found while implementing)
- Added `StateDelta` / `FieldChange` — referenced by specs 07/08 but never actually defined
  here originally. Generic `(entity_type, entity_id, field, old_value, new_value)` shape so
  retroactive-edit diffing is a set comparison, not per-entity-type logic.
- Added `Character.aliases: list[str]` — without it, the extractor has no way to match "the old
  man" in episode 3 to "Mr. Verma" in episode 40, and silently creates a duplicate character.
  This is the single biggest risk to "no forgotten characters" at scale.
- Dropped `RunState.plan_status` — redundant with `ArcPlan.status`, two sources of truth that
  could drift. Plan approval is always read from `ArcPlan` directly.
- `Episode.word_count` is a computed property derived from `text`, not a settable field —
  can't drift from the actual text.
- `Beat.episode_range: tuple[int,int]` became two plain ints (`episode_start`, `episode_end`) —
  simpler SQLite column mapping, same information.
- `Directive.expires_after_episode` stores the resolved absolute episode number at creation
  time (`created_at_episode + N`), not the raw N — avoids re-deriving the window on every read.

## Later additions (while implementing specs 04-08)
- `Episode.summary`, `ArcPlan.overview`, `Character.planned_arc`, `StateDelta.extraction` (raw extractor output, diffed on edits).
- `Thread.status` gained `planned` (scheduled but not yet introduced). Literal aliases: `CharacterStatus`, `ThreadStatus`, `DirectiveScope`, `EntityType`.
- New shared types: `BlockSummary`, `EpisodeSummary`, `CriticResult`, `EpisodeContext`, `StrictModel` (base for LLM-facing schemas), `FINALIZED_STATUSES`.
- `LLMCallLog.stage` gained `summarize`.

## Status: [x] implemented (`src/models.py`), covered indirectly by every test module
