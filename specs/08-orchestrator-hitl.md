# 08 — Orchestrator & HITL (`src/core/orchestrator.py`)

The state machine. Owns transitions; every other `core/` module is a pure-ish function it calls.

## States
`plan_pending -> plan_approved -> writing(N) -> critiquing(N) -> human_review(N) -> [approved|edited|rejected] -> writing(N+1) -> ... -> done`

## Human gates
1. **Plan gate** (`cli.py review-plan`): approve / edit (free text -> `planner.revise_arc_plan`) / reject (regenerate from scratch).
2. **Episode gate** (`cli.py run`, per episode): approve / edit (replace text, source="human_edited") / reject (regenerate, optional reason feeds back as critic_notes) / feedback (free text, independent of approve/reject — can be attached to any of the above).

## Feedback propagation
`feedback` text -> `Directive` row via `repo.add_directive`. Default scope `permanent` unless
the text implies a window (simple heuristic: "for the next N episodes" phrasing -> `next_n_episodes`;
otherwise ask the CLI user to confirm scope explicitly — don't guess silently). Every future
`memory.assemble_context` call picks up active directives automatically — no per-episode wiring
needed, which is what makes feedback "actually propagate" instead of being a one-off patch.

## Retroactive edit (`cli.py edit-episode <n>`)
1. Load episode N, show current text, accept new text.
2. `repo.save_episode` with `source="human_edited"`, status reset to `edited`.
3. `extractor.extract_deltas` on the new text -> new `StateDelta`.
4. Diff new delta against the delta that was originally recorded for episode N (need to persist
   the original delta per episode, not just the final merged state, to make this diff possible).
5. If the diff changes any fact/character-status/thread-status that episodes N+1..current
   depended on: `repo.mark_episodes_stale(run_id, from_episode=N+1)`.
6. CLI reports which episodes are now stale and lets the human choose: regenerate from N+1, or
   acknowledge and continue (accepting the inconsistency, logged as a decision).

This is the mechanism the take-home's live-twist question ("human rewrites episode 40, what
happens to 41-60?") is testing — answer: they get flagged stale via delta diff, not silently
left wrong, and regeneration reuses the same per-episode loop starting at 41.

## Resume (`cli.py resume <run_id>`)
`repo.load_run(run_id)` gives `current_episode`; orchestrator re-enters the loop there. All
state (characters/threads/facts/directives) is already persisted — no recomputation needed.
This is why extraction-per-episode (07) matters: resume is just "continue," not "replay."

## Implementation notes
- All interaction goes through a `HumanGate` protocol (`review_plan`, `review_episode`); the orchestrator does no I/O.
- Retroactive edit is two-phase: `preview_edit` (one extraction + diff, read-only) then `commit_edit(invalidate_later)`.
  Invalidate: revert deltas newest-first to the end of N-1, apply the new reading, mark N+1.. stale, rewind the run to N+1.
  Keep: only text and stored reading change; hard differences are logged as an accepted inconsistency.
- Resume is crash-safe: a quit draft is stored as `critiqued` and reused; an approved episode with no delta is finalized
  without regenerating.
- Bounds: `MAX_REVISIONS`, per-draft-cycle `MAX_EPISODE_COST_USD`, optional `MAX_RUN_COST_USD`, end of plan.

## Status: [x] implemented (`src/core/orchestrator.py`), `tests/test_orchestrator.py`, `tests/test_retroactive_edit.py`
