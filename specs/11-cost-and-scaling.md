# 11 — Cost & Scaling Notes (feeds DECISIONS.md)

## Per-episode call count (steady state, no revisions)
1 writer call + 1 deterministic critic pass (free) + 1 LLM-critic call + 1 extractor call
= ~3 LLM calls/episode. Revisions add up to 2 more writer+critic pairs in the worst case.

## Cost estimate for 200 episodes
To be computed concretely once a provider/model is picked and a few real episodes are run
(use `cost-report` on the first 10-15 episodes and extrapolate linearly — plan + per-episode
average × 200). Record the actual measured number in DECISIONS.md, not a guess made before
building.

## What breaks first as the story grows (be honest about this)
- **Open-thread count** creeping upward if resolution isn't paced against introduction —
  mitigated by the resolution_target_episode staleness check, but if the planner over-plants
  subplots early, episode 100+ context gets crowded. Cap + warn, don't silently drop.
- **Rolling summary drift** — a summary-of-a-summary compounds small errors over many episodes.
  Mitigation: regenerate the rolling summary from structured state (characters/threads/facts)
  periodically instead of always summarizing-the-summary, since structured state doesn't drift
  the same way free text does.
- **Critic false-negatives at scale** — an LLM-as-judge checklist is probabilistic; given 200
  episodes, some contradictions will slip through. This is the honest limit: the system reduces
  inconsistency, it doesn't guarantee zero. Human spot-checks remain necessary periodically, not
  just at the two mandated gates.
- **Cost compounding** if revisions trend upward late in the story as context gets more
  constrained/truncated — watch `revision_count` per episode as a leading indicator.

## Reduction strategies (for the "how would you reduce cost/time" answer)
- Batch non-interactive runs (`--approve-all`) for throughput once trust in the pipeline is
  established, reserving human review for flagged/low-confidence episodes only.
- Use the cheapest capable model for critic/extractor (structured, short output) — already the
  default in 02-llm-client.md.
- Cache/reuse the rolling summary instead of recomputing from scratch each episode.
- Prompt caching (provider-level) on the stable parts of the context (arc plan excerpt, system
  prompt) since only the rolling window changes episode to episode.

## Implementation notes
- `src/core/cost.py` projects from measured spend (plan once + observed per-episode average incl. rewrites); surfaced by `cost-report`.
- DECISIONS.md carries an arithmetic estimate clearly labelled as such; replace it with `cost-report` numbers after a live run.

## Status: [~] tooling done; real numbers pending a live run
