# 00 — Overview & Scope

## Problem
Agentic system that plans a 200-episode serial story from a one-line premise, writes episodes
sequentially (400-700 words, ends on a hook), stays consistent across episodes, and supports
human-in-the-loop control (approve/edit/reject plan and episodes, feedback that propagates
forward, resume after stopping, retroactive edits to past episodes).

## What's actually graded (drives priority order)
1. Memory & consistency design — must hold at episode 150, not just 15.
2. HITL design — intervention points + feedback that actually propagates forward.
3. Design reasoning — no complexity for its own sake.
4. Output quality — hooks, momentum, not generic prose.
5. Engineering — resumability, observability, cost awareness, clean code.
6. Honesty — DECISIONS.md must say what breaks first.

## Decisions locked
- Format: CLI (not web-hosted) — no deploy/uptime overhead, more time for memory/critic engine.
- Language: Python (uv-managed venv, already initialized).
- Orchestration: custom state machine, no LangGraph/CrewAI — the hard part (retroactive
  mutation of historical state) needs a relational store regardless, so a graph checkpointer
  would duplicate state rather than replace it.
- LLM access: provider-agnostic client interface — Anthropic, OpenAI, Bedrock swappable via config.
- Storage: SQLite, one DB file per run, accessed only through `storage/repo.py`.
- Logging: Python `logging` module — console (INFO+, human-readable) + JSON file handler
  (DEBUG+) per run, for the "traceable" requirement. Cost/token numbers also persisted to
  SQLite (queryable), not just logged.

## Non-goals for the 6-8hr slice
- No fine-tuning, no vector DB / embedding infra beyond a cheap similarity check for repetition.
- No multi-agent framework. No web UI.
- Not generating all 200 episodes — a meaningful slice (full plan + 15+ episodes + 2 HITL
  interventions with visible downstream effect), per the brief.

## Core loop (one episode)
```
memory.assemble(run_id, episode_no)
  -> writer.draft(context)
  -> critic.check(draft, context)
  -> [revise, max 2 attempts] -> if still failing, surface to human anyway with critic notes
  -> human gate: approve / edit / reject+feedback
  -> extractor.extract_deltas(final_text) -> update characters/threads/facts
  -> repo.save_episode(...), repo.log_llm_calls(...)
  -> advance to episode N+1
```
