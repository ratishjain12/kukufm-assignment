# Agentic Serial Story Writer

Plans a 200-episode serial from a one-line premise, then writes it one episode at a time (400-700 words, each ending on a hook) with a human in the loop. You can approve or edit the arc plan, approve, edit or reject every episode, give feedback that steers all later episodes, edit history retroactively, and stop and resume at any point.

CLI only. Plain Python with a custom state machine and SQLite. No agent framework; `DECISIONS.md` explains why, how the story is remembered at episode 150, and what breaks first.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env      # fill in ONE of the three setups inside (Anthropic, AWS Bedrock, OpenAI)
uv run python main.py demo
```

`demo` runs the whole scenario against the real models and exports the deliverables to `demo/` (about 15-20 minutes). Every step and every model call prints as it happens. See [What the demo does](#what-the-demo-does).

| Provider | You need | Models |
|---|---|---|
| Anthropic (default) | `ANTHROPIC_API_KEY` | built in |
| AWS Bedrock | AWS credentials (default profile works) and `AWS_REGION` | set `BEDROCK_<ROLE>_MODEL`; `.env.example` has a working layout |
| OpenAI | `OPENAI_API_KEY` | set `OPENAI_<ROLE>_MODEL` for all four roles |

Roles are `PLANNER`, `WRITER`, `CRITIC` and `EXTRACTOR`. `pricing.json` holds USD per million tokens per model and is pre-filled for the models in `.env.example`; a model without an entry is costed at $0, which also disables the cost caps, so add a line when you change models.

## What the demo does

`python main.py demo` is one reproducible command. The human side is a scripted reviewer whose decisions are data in `src/demo.py`:

1. **Plan:** plans 200 episodes, edits the plan once with reviewer feedback, then approves it.
2. **Session 1, episodes 1-12:** one episode is rejected with a reason, one is approved with a permanent directive (end every episode on spoken dialogue), and one with a time-limited directive (kill off a named supporting character).
3. **Session 2, episodes 13-18:** continues in a fresh session, resumed from the database.
4. **Export** into `demo/`:

| File | Contents |
|---|---|
| `plan/arc_plan.txt`, `plan/arc_plan_v1.txt` | the approved 200-episode plan and the version before the edit |
| `episodes/episode_001.md` ... | each episode with status, word count, rewrites and summary |
| `story.md` | all episodes in one file |
| `human_decisions.md` | directives, how the episodes around each one end, character status changes, timestamped gate decisions |
| `cost_report.txt` | spend and time by stage, and the projection to 200 episodes |
| `trace_summary.log`, `trace_full.jsonl` | readable trace, and the full trace with prompts, tokens, latency and retries |
| `console_output.log` | what was printed, when you run it as `... demo 2>&1 \| tee demo/console_output.log` |

Options: `--episodes`, `--plan-length`, `--premise`, `--out`, `--provider`. Working data stays in `runs/<run_id>/` (gitignored).

## Using it interactively

```bash
uv run python main.py new "<premise>"                  # plan, then review the plan
uv run python main.py run <run_id>                     # write episodes, review each one
uv run python main.py run <run_id> --count 5           # stop after 5
uv run python main.py resume <run_id>                  # same as run: continue where you stopped
uv run python main.py edit-episode <run_id> 12         # retroactive edit, opens $EDITOR
uv run python main.py directives <run_id>              # list; --add "text" [--next N]; --retire <id>
uv run python main.py show <run_id> [--episode N]      # plan and episode list, or one episode
uv run python main.py cost-report <run_id>             # spend so far and projection to 200 episodes
uv run python main.py export <run_id> --out demo       # write the deliverables folder
uv run python main.py rebuild-state <run_id>           # re-derive characters/threads/facts from the texts
uv run python main.py list
```

`--auto-approve` (on `new`, `review-plan`, `run`) approves clean drafts without prompting. A draft the system cannot fix stops the run instead of shipping; it stays saved for you to review and `resume`.

### The human controls

| Where | Choices |
|---|---|
| Plan gate (before episode 1) | approve, edit with feedback (re-plans), reject and regenerate, quit |
| Episode gate (every episode) | approve, approve plus steering feedback, edit the text, reject with a reason, quit |
| Feedback | becomes a standing directive (permanent, or the next N episodes) that is injected into every later prompt; the gate shows the directives currently in force |
| `edit-episode` | previews plot-level differences, then you choose whether to mark later episodes stale and regenerate them |

The gate also shows where the episode sits in its beat (for example "episode 3 of 8"). Quitting at a review keeps the draft, and resuming shows the same draft without paying to regenerate it.

## How it works

```
premise -> planner (1 structured call) -> plan gate
per episode:
  memory.assemble_context   bounded, layered prompt context (see DECISIONS.md)
  writer -> checks -> rewrite while objective rules fail (bounded) -> episode gate
  extractor -> state delta + one-line summary -> SQLite
  every 10 episodes: a block summary built from the one-liners
```

- **Memory:** see [How context is built](#how-context-is-built). Never a replay of old prose.
- **Checks before you see a draft:** objective rules trigger rewrites (400-700 words, recycled phrasing, a word repeated as a tic, stray glyphs). An LLM judge also reviews continuity against facts, roster, directives and recent endings, but its findings are advice shown at the gate, because measured on real runs its automatic "contradiction" verdicts were unreliable.
- **Bounded:** at most `MAX_REVISIONS` rewrites per draft, a cost cap per draft cycle, an optional run budget, per-request timeouts, bounded retries, and one repair attempt for malformed structured output. When rewrites run out the best draft is shipped to you, not the last.
- **Resumable and recoverable:** every state change goes through one repository; derived state records an undo log per episode, so a retroactive edit rolls state back exactly and `rebuild-state` can re-derive it. A run refuses to continue if finalized episodes lack state and tells you to rebuild.
- **Traceable:** `runs/<run_id>/trace.log` (JSON lines, DEBUG) records prompts, responses, decisions, retries, tokens, cost and latency; the same per-call data is in the `llm_calls` table.
- **Bedrock structured output** uses the Converse API's native JSON-schema mode and falls back to a forced tool call for models that lack it (Amazon Nova). Details: `specs/02-llm-client.md`.

## How context is built

The prompt for episode N has a fixed size no matter how long the story is. The orchestrator (`src/core/orchestrator.py`) calls `memory.assemble_context` before every draft, and the writer sees only these layers:

| Layer | What it holds | Bound |
|---|---|---|
| Plan excerpt | the current beat and the next two, plus "episode k of m in this beat" | 3 beats |
| Characters | cards (role, status, traits, relationships, arc stage, last seen) for the characters in play, plus a one-line roster of everyone else | 10 cards |
| Threads | open threads, and planned threads due to be introduced | 8 open |
| Facts | concrete established facts, picked by relevance to the beat's characters and places | 15 |
| Author's notes | plan-seeded world facts the reader does not know yet, so the writer can foreshadow without revealing | 10 |
| Summaries | one recap per completed 10-episode block, then one-liners for the current block | 2 levels |
| Recent episodes | the last three verbatim, plus how each one ended (to vary the closing device) | 3 |
| Directives | your standing steering feedback that is still in force | all active |

**How characters stay consistent.** After each approved episode the extractor reads the text against the live roster and thread list and reports what the page establishes: who appeared, status changes, new traits and aliases, relationship changes, thread movement, new facts, and a one-line summary. `state_delta.apply_extraction` merges that into SQLite behind guardrails:

- a character's status changes (alive, dead, missing) only with an exact quote from the episode as evidence; the same applies to thread updates
- a new character needs a proper name, not a description ("the voice", "a clerk")
- a planned thread cannot move before it is scheduled, and thread status never regresses
- caps per episode on thread updates and new facts, so one noisy extraction cannot flood the state

Each merge is stored as a `StateDelta` that records the old value of every field it changed. That undo log is what makes a retroactive edit exact: `edit-episode` previews the plot-level differences, reverts the deltas of the edited episode and everything after it newest-first, applies the new reading, and marks later episodes stale. `rebuild-state` re-derives the whole state from the episode texts if it is ever lost or suspect.

**Summaries** are two-level: each episode gets a one-line summary from the extractor, and every 10 episodes a block recap is built from those one-liners (never from other recaps), so summarisation depth does not grow with the story.

**Steering** is a directive row, either permanent or for the next N episodes. It is injected into every later prompt and shown at the episode gate, so feedback given at episode 4 still applies at episode 150 until you retire it.

## Configuration

All optional except the provider setup. See `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `bedrock` or `openai` |
| `MAX_REVISIONS` | 2 | automatic rewrites before the draft goes to you |
| `MAX_EPISODE_COST_USD` | 0.50 | spend limit per draft attempt |
| `MAX_RUN_COST_USD` | 0 (off) | total spend limit for a run |
| `LLM_TIMEOUT_SECONDS` | 120 | per-request limit; a stuck call is cut off and retried |
| `BEDROCK_STRUCTURED_MODE` | `auto` | `auto`, `native` or `tool` |

## Layout

```
main.py                  entrypoint
src/cli.py, gates.py     commands; console and auto-approve human gates
src/demo.py, export.py   the reproducible demo run; the deliverables export
src/core/                planner, memory, writer, critic, extractor, state_delta,
                         summarizer, orchestrator, cost, formatting
src/llm/                 provider-agnostic client, retry/cost wrapper, structured output, adapters
src/storage/             SQLite schema and repository (the only SQL in the project)
src/prompts/*.md         prompts as data
specs/                   design specs; CHECKLIST.md tracks progress
tests/                   scripted fake LLMs, no network
```

## Development

```bash
uv run pytest                      # 160+ tests, no API calls
uv run ruff check src tests main.py
uv run mypy src main.py
```

## Status and limits

Verified live on AWS Bedrock (Qwen3-235B for planning, critic and extraction; Kimi K2.5 for writing): the full 200-episode plan, 18 episodes for $0.37, a rejection, two directives, a resumed second session and the export in `demo/`. The Anthropic and OpenAI adapters are unit-tested against fakes but were not exercised live. After that run I lowered the writer's length target (it overshot 700 words on most episodes) and raised the extractor's token cap; those two changes were not re-run, so the demo reflects the earlier behaviour. Measured weaknesses (state not always matching the story, duplicate character entries, repetitive imagery) are listed in `DECISIONS.md`.
