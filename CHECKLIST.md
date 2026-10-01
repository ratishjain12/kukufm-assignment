# Progress Checklist

Specs live in `specs/00-*.md` through `specs/11-*.md`. Check items off as implemented; update
each spec file's own `## Status` line to match.

## Setup
- [x] uv project initialized, venv active
- [x] dependencies added (pydantic, anthropic, openai, boto3, python-dotenv; dev: pytest, ruff, mypy). `rich` dropped.
- [x] `.env.example` created (+ empty `pricing.json`)

## Foundations
- [x] `src/models.py` — all pydantic models (spec 01)
- [x] `src/logging_config.py` (spec 10)
- [x] `src/llm/base.py` — protocol + `call_with_tracking` wrapper (spec 02)
- [x] `src/llm/anthropic_client.py`
- [x] `src/llm/openai_client.py`
- [x] `src/llm/bedrock_client.py`
- [x] `src/llm/factory.py`
- [x] `src/storage/db.py` — schema (spec 03)
- [x] `src/storage/repo.py` — CRUD functions (spec 03)

## Core pipeline
- [x] `src/core/memory.py` — context assembly (spec 04)
- [x] `src/core/planner.py` — arc plan generate/revise (spec 05)
- [x] `src/core/writer.py` — episode drafting (spec 06)
- [x] `src/core/critic.py` — deterministic + LLM checks, revision loop (spec 06)
- [x] `src/core/extractor.py` — state delta extraction (spec 07)
- [x] `src/core/orchestrator.py` — state machine, HITL gates, retroactive edit, resume (spec 08)
- [x] `src/prompts/*.md` — planner/writer/critic/extractor templates

## CLI
- [x] `src/cli.py` — new / review-plan / run / resume / edit-episode / cost-report / show (spec 09)
- [x] `main.py` wired to `src/cli.py`

## Extra modules added during implementation
- [x] `src/core/formatting.py`, `state_delta.py`, `summarizer.py`, `cost.py`; `src/llm/structured.py`; `src/gates.py`; `src/config.py`

## Quality gates
- [x] `uv run pytest` (100+ tests, scripted fakes), `ruff check`, `mypy src main.py` all clean

## Tests
- [x] `tests/test_critic.py` (mock LLM client)
- [x] `tests/test_extractor.py`
- [x] `tests/test_memory.py`
- [x] `tests/test_orchestrator.py` — resume + retroactive-edit staleness logic

## Live run / demo (run_d0fff703, Bedrock: Qwen3-235B + Kimi K2.5)
- [x] Full 200-episode arc plan generated for the premise (one edit with feedback, then approved)
- [x] 18 episodes written and reviewed
- [x] HITL intervention #1: directive "end on spoken dialogue" at episode 4; endings change from episode 5 (12 of 14 comply)
- [x] HITL intervention #2: directive "kill Mira Chen" at episode 8; the death appears on the page in episodes 11-12 (database state still says alive, see DECISIONS.md)
- [x] One episode rejected with a reason (episode 3)
- [x] Stop/resume: session 1 (episodes 1-12), session 2 resumes from the database (13-18)
- [x] `cost-report` run, measured numbers in DECISIONS.md ($0.37 for 18 episodes, about $4 projected for 200)
- [ ] Retroactive edit on an earlier episode: not in the scripted demo, shown in the screen recording

## Deliverables
- [x] README.md: setup, usage, how context is built
- [x] DECISIONS.md: one page, measured numbers, honest limits
- [x] Demo output saved in `demo/` (plan, episodes, human decisions, cost report, traces)
- [ ] Screen recording (<5 min) of the HITL flow
- [x] Final pass against `knowledge-base/review-checklist.md` conventions

## Known limits (post-demo fixes not re-run)
- Writer length target lowered and extractor max tokens raised after the demo; the demo predates both.
- State/story drift on ambiguous events, duplicate character entries, repetitive imagery (DECISIONS.md section 4).
