# 07 — Extractor (`src/core/extractor.py`)

## `extract_deltas(episode: Episode, context: EpisodeContext) -> StateDelta`
Cheap-model, structured-output LLM call. Given the final (approved or human-edited) episode
text, returns:
- Character updates: status changes, new traits, relationship changes, `last_seen_episode`.
- Thread updates: status transitions (planted → escalated → resolved), `last_touched_episode`.
- New facts introduced (names, dates, places worth remembering verbatim).

`StateDelta` is applied via `repo.upsert_character/upsert_thread/add_fact` — this is what makes
the fact/thread/character store *derived from text* rather than hand-maintained, and it's the
exact mechanism that makes retroactive edits tractable (re-run extraction on new text, diff
against old delta, see 08).

## Why extraction instead of re-reading prose each time
Re-reading all prior prose to figure out "is this character still alive" doesn't scale past a
few dozen episodes. Running this once per episode and persisting structured state means episode
150's context never needs to touch episode 3's prose directly.

## Implementation notes
- Split: `extractor.py` (LLM call, structured `Extraction`, `hard_differences`) and `state_delta.py` (deterministic
  `apply_extraction`, `revert_delta`). `summarizer.py` builds block recaps.
- Deltas are an undo log: every change records its old value; revert is exact (test: apply then revert restores a snapshot).
  The raw extraction is stored on the delta and edits are diffed extraction-to-extraction.
- `hard_differences` compares only plot-level claims; a one-sided claim counts only for terminal events or introductions.
- Identity resolution: roster ids in the prompt, then exact name/alias lookup, then create. Thread status can only move forward.

## Status: [x] implemented, `tests/test_state_delta.py`, `tests/test_summarizer.py`

Changed after live runs: status changes and thread updates need an exact quote from the episode (`evidence`, required for threads); new characters need a proper name; planned threads cannot move early; a truncated extraction is retried once with double max_tokens. Known gap: ambiguous events (a ghost-story death) are dropped for lack of a quote, so state can disagree with the story (DECISIONS.md).
