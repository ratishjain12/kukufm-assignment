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

## Live run / demo
- [ ] Full 200-episode arc plan generated for the sent premise
- [ ] 15+ episodes written and reviewed
- [ ] HITL intervention #1 (feedback) applied, shown to change a later episode
- [ ] HITL intervention #2 (retroactive edit on an earlier episode) applied, downstream staleness shown
- [ ] `cost-report` run, real numbers captured for DECISIONS.md
- [ ] Stop/resume demonstrated (stop after ep 12, resume, continue)

## Deliverables
- [x] README.md — setup in <5 min, usage walkthrough
- [~] DECISIONS.md — drafted and one page; cost/time numbers to be replaced with measured values after the live run
- [ ] Demo output saved (plan + episodes) in repo or `runs/`
- [ ] Screen recording (<5 min) of HITL flow
- [x] Final pass against `knowledge-base/review-checklist.md` conventions
