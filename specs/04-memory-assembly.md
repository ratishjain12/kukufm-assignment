# 04 — Memory Assembly (`src/core/memory.py`)

This is the episode-150 answer — the module DECISIONS.md will point to directly.

## `assemble_context(run_id, episode_no) -> EpisodeContext`

Builds the prompt context for writing episode N from layered, bounded sources — never raw
concatenation of all prior episodes:

1. **Arc plan excerpt** — the `Beat` covering `episode_no`, plus the next 1-2 beats (so the
   writer can foreshadow toward the upcoming turning point).
2. **Character state** — only characters with `last_seen_episode` within a recent window OR
   appearing in the current beat's `character_arcs_touched`. Pulled as structured data
   (name, status, traits, relationships), not prose.
3. **Open threads** — all threads with status in (`planted`, `escalated`), sorted by
   `resolution_target_episode` ascending so near-due threads surface first. Capped at ~8 — if
   more are open, that itself is a critic/health signal (see 11-cost-and-scaling.md).
4. **Active directives** — all `Directive` rows with `status="active"` and not expired for this
   episode number. Injected verbatim as instructions, highest priority in the prompt.
5. **Rolling window** — full text of the last 2-3 episodes verbatim (voice/pacing continuity +
   immediate hook payoff), plus a rolling summary (updated incrementally, ~1 paragraph per
   10-episode block) covering everything older.
6. **Relevant facts** — `Fact` rows tagged with entities present in this beat, not the whole
   fact table.

## `EpisodeContext` (pydantic, lives in `models.py` or here — decide at implementation time)
Bundles the above into one object the writer/critic both consume, so both see identical state.

## Token budget discipline
Each layer has a cap (e.g. rolling summary ≤ 300 words, open threads ≤ 8, characters ≤ 10).
If the real state exceeds a cap, truncate deterministically (oldest-first for summary content,
nearest-deadline-first for threads) and log a DEBUG note — this is the explicit place where
"what breaks first as the story grows" happens, and it's visible rather than silent.

## Implementation notes
- Two-tier history: completed 10-episode blocks get an LLM recap built from per-episode one-liners; the current partial block
  contributes one-liners. If block summaries are non-contiguous, falls back to one-liners rather than leaving a gap.
- "Recent" and history only ever read finalized episodes strictly before N, so a retroactive regeneration never sees stale
  future episodes.
- Added `threads_to_introduce` (planned threads that are due or in the current beat) and `roster` (all characters, compact).
- Thread priority: this beat's threads, then neglected (>15 episodes), then nearest deadline; capped at 8 with a warning.
- Facts: `core`-tagged always, then tag-matched against relevant characters and the beat text; capped at 15.
- `formatting.py` renders the context once for writer, critic and extractor prompts.

## Status: [x] implemented (`src/core/memory.py`, `src/core/formatting.py`), `tests/test_memory.py`
