# DECISIONS

## 1. How does the system remember the story at episode 150?

Never by replaying prose. After each approved episode an extractor reads the text once into SQLite: characters (status, aliases, relationships), threads, facts, a one-line summary. Each draft gets a bounded context: the current beat plus the next two; up to 10 character cards plus a one-line roster of everyone; up to 8 open threads plus threads due to be introduced; up to 15 facts; plan-seeded facts as author's notes the reader doesn't know yet; a recap per finished 10-episode block (built from the one-liners, never from other recaps, so summarisation stays two levels deep) plus the current block's one-liners; the last 3 episodes verbatim; every active directive. Prompt size stays roughly flat as the story grows. Older prose survives only as state and summaries.

State changes pass guardrails because extractors over-claim: a death or thread update needs an exact quote from the episode, new characters need a proper name, planned threads can't move before they are due. Every change stores its old value, so a retroactive edit reverts exactly and `rebuild-state` can re-derive everything.

## 2. Where does the human step in, and why there?

- **Plan gate**, before episode 1: the cheapest place to steer 200 episodes. The plan locks once writing starts; later steering is by directive.
- **Episode gate**, after the checks: approve, edit, reject with a reason, or approve with feedback. Feedback becomes a persistent directive (permanent, or the next N episodes) injected into every later prompt, so it steers everything after it rather than patching one episode.
- **Retroactive edit**: the system previews plot-level differences (character status, thread outcomes) and the human decides whether later episodes are marked stale.

Not per scene (too much attention at 200 episodes) and not on the database (people steer stories, not tables).

## 3. How are inconsistency and repetition caught before a human sees them?

Objective rules trigger rewrites (400-700 words, recycled 5-word phrasing, a word repeated as a tic, stray CJK glyphs). An LLM judge also reads roster, facts, directives and recent endings, but **its findings are advice shown at the gate, not blockers**: on real runs its "contradiction" verdicts were often hallucinated, and acting on them caused rewrite loops. Rewrites are bounded (2 per draft, a cost cap), and the best draft is shipped, not the last. The cost of this choice: semantic problems reach the human, who is the real continuity check.

## 4. What the demo run shows, and what it doesn't

Run `run_d0fff703` (Bedrock: Qwen3-235B plans, judges and extracts; Kimi K2.5 writes): full 200-episode plan (one edit with feedback), 18 episodes, one rejection, two directives, a second session resumed from the database. Measured: **$0.37, about 13 minutes of LLM time, 93 calls**.

Honest results:
- **Directive 1** (end on spoken dialogue, given at episode 4): held in 12 of 14 later episodes; episodes 10 and 13 end on narration. The judge only advises on this, so nothing forced a rewrite.
- **Directive 2** (kill Mira Chen): the story does it on the page in episodes 11-12, but the database still says she is alive. The extractor's death claim had no matching quote (the death is ghost-story ambiguous) and the guardrail dropped it. State and story disagree.
- **Duplicate characters** (`Lira`/`Lira Rao`, `Mira`/`Mira Chen`, `Mira (vision)`): the extractor missed some alias matches.
- **Length:** the writer overshoots 700 words on most episodes, so nearly every episode paid for a rewrite (about 37% of spend). After the run I lowered the writer's length target and raised the extractor's token cap (4 of 18 extractions were truncated and recovered via retry). **These fixes were not re-run**; the demo shows the earlier behaviour and the numbers above are from it.
- **Prose** stays repetitive in imagery ("voice" 56 times, "helmet" 48 across 18 episodes) though no episode has a runaway word.
- The retroactive edit is not in the scripted demo; it is shown interactively in the screen recording.

## 5. What breaks first at 200 episodes, and the fix

1. **State vs. story drift.** Quote-gated extraction is safe against hallucination but misses ambiguous events, and alias merges are imperfect. Fix: a periodic roster audit and a reconcile step that proposes merges and status corrections for the human.
2. **Directives vs. the static plan.** "Kill X" is honoured, but later beats still feature X. Fix: re-plan the tail from current state and directives.
3. **Facts nobody can see.** Facts are chosen by tag relevance and capped at 15 (the logs show 20-50 relevant facts truncated). Fix: embedding retrieval.
4. **Semantic repetition** beyond the recent window relies on the writer seeing one-liners. Fix: embed summaries and check similarity.
5. **One-shot 200-beat plan.** It worked here (about a minute), but some models loop on the large schema; the adapter detects whitespace loops and retries as a forced tool call. Fix for scale: outline, then expand per phase.
6. **Retroactive edits** detect plot-level claims only, and regenerating later episodes is sequential with a review each.

## Cost and time for 200 episodes

Extrapolated from the 18-episode run, rewrites and rejections included: about $0.020 and 39 s of LLM time per episode, **about $4 and 2.2 hours for 200 episodes**, excluding human review. It is a linear projection; context size is bounded so cost per episode should stay near flat, but later episodes with more state may run slightly higher. Levers: shorter writer target (fewer rewrites), prompt caching on the stable prefix, a cheaper judge, `--auto-approve` for unattended stretches. A run budget (`MAX_RUN_COST_USD`) caps spend.
