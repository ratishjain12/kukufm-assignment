# DECISIONS

## 1. How does the system remember the story at episode 150?

Never by replaying prose. An extractor reads each episode once into a structured SQLite store (characters with status/aliases/relationships, threads, facts, a one-line summary). Each draft call gets a bounded, layered context:

- **Plan**: premise, overview, current beat and the next two.
- **State**: up to 10 characters in detail (protagonist, in this beat, seen in the last 10 episodes) plus a compact roster of *all* characters with canonical status; up to 8 open threads (this beat's, then neglected, then nearest deadline) plus threads due to be introduced; up to 15 facts (`core` plus tag-matched).
- **History**: a recap per completed 10-episode block, built from episode one-liners (never from other recaps, so summarisation depth stays at two), plus one-liners for the current block.
- **Steering**: every active human directive.
- **Voice**: the last 3 episodes verbatim.

About 10k tokens at episode 150, growing ~150 tokens per 10 episodes. Older prose survives only as state and summaries.

## 2. Where does the human step in, and why there?

- **Plan gate**, before episode 1: the cheapest place to steer 200 episodes. The plan locks once writing starts; later steering is by directive.
- **Episode gate**, after the critic: the human sees passing drafts, or ones the critic could not fix (with its notes), so attention goes to taste, not mechanics. Feedback becomes a persistent directive that steers every later episode rather than patching one.
- **Retroactive edit**: the system re-reads the edit, diffs plot-level claims (character status, thread outcomes) and *recommends*; the human decides whether later episodes are invalidated.

Not per scene (too much attention at 200 episodes), and not on the database (people steer stories, not tables).

## 3. How are inconsistency and repetition caught before a human sees them?

A deterministic pass (word count; 5-word-sequence reuse against recent episodes), then an LLM judge that sees roster, state, directives, summaries and recent hooks, plus facts re-retrieved from entities named in the *draft* (the writer's filtered facts could miss one). Pass/fail comes from issue severities, not a self-reported flag. Blockers trigger up to 2 rewrites under a cost cap, then escalate to the human with notes. Threads untouched for 15 episodes are flagged to the writer.

## 4. What breaks first, and how would I fix it?

1. **Facts nobody can see.** Facts surface by tag match; an untagged one is invisible to writer and critic. Fix: embedding retrieval.
2. **Directives vs. the static plan.** "Kill X" is honoured, but later beats still feature X. Fix: `replan_tail` from current state and directives.
3. **Retroactive edits.** Detection covers plot-level claims, not names or details; regenerating 41-60 is sequential with a review each. Fix: human can invalidate manually; batch with `--auto-approve`.
4. **Semantic repetition** beyond the recent window relies on the judge reading one-liners. Fix: embed summaries.
5. **Identity drift.** A wrong extractor merge is silent. Fix: periodic roster audit.
6. **One-shot 200-beat plan** will degrade or truncate. Fix: outline, then expand per phase.

Not yet validated live: the full 200-beat plan (native JSON decoding can loop on whitespace; the adapter detects it and retries as a forced tool call), critic accuracy on real drafts, and measured cost per episode.

## Cost and time for 200 episodes

Measured figures replace this after the first live run (`cost-report` extrapolates real spend, rewrites included). Estimate until then, with Opus 4.6 planning, Sonnet 4.6 writing and Nova Pro judging/extracting on Bedrock: ~3 calls per episode plus ~0.5 rewrites, roughly 30k input / 3k output tokens per episode (~6M / 0.6M for the run), about $0.09 per episode and ~$18 for 200 episodes including the ~$0.30 plan, and 2-3 hours of LLM time excluding review. Token counts are my estimates; the rates are real. Levers: prompt caching on the stable prefix, a cheaper critic, `--auto-approve` for unattended stretches.
