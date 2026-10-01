# 05 — Planner (`src/core/planner.py`)

## `generate_arc_plan(premise: str, total_episodes: int = 200) -> ArcPlan`
Single LLM call (strongest model) with a structured-output prompt asking for:
- Act/phase structure appropriate to 200 episodes (e.g. 5-8 major phases).
- A list of `Beat`s covering the full range, each with an `episode_range`, `title`, `summary`,
  `turning_point` flag, and the character/thread ids it touches.
- Initial `Character` roster (protagonist, key supporting cast) with starting state.
- Initial `Thread` list (central mystery/conflict + 2-4 subplots) with planted/target episodes.

Parse into `ArcPlan` + seed `Character`/`Thread` rows via `repo.py`. Don't hand-wave the planted
threads — they're what the critic checks against later, so the planner must emit them as
structured data, not just prose description of the story.

## `revise_arc_plan(run_id: str, feedback: str) -> ArcPlan`
Human edit/reject path. Takes free-text feedback ("make the building's history a bigger thread
earlier"), re-prompts with the existing plan + feedback, bumps `version`, re-saves. Does not
silently merge — replaces the plan and logs the diff (which beats/threads changed) so the human
can see what moved.

## Implementation notes
- LLM-facing `PlanDraft` schema uses string keys; the planner maps them to generated ids and seeds characters (with
  `planned_arc`), threads (status `planned`), core facts, and the plan (`overview` added to `ArcPlan`).
- Deterministic validation (contiguous beats covering 1..N, known keys, protagonist present, thread episodes in range) with one
  repair attempt that feeds the problems back; then `PlanInvalidError`.
- Revision re-plans from the current plan plus feedback and replaces everything wholesale; logs beats and characters added/removed.
  Plan edits are locked once any episode exists (`PlanLockedError`); later steering is by directive.
- Known limit: one 16k-token call for 200 episodes. See DECISIONS.md.

## Status: [x] implemented (`src/core/planner.py`, `src/prompts/planner.md`), `tests/test_planner.py`
- Planner model: Qwen3-235B (valid native structured output in testing); Kimi K2.5 degenerated into whitespace on this schema.
