# 10 — Logging & Observability (`src/logging_config.py`)

## `configure_logging(run_id: str) -> None`
Called once, at CLI startup, before any other module logs anything.
- Root logger level `DEBUG`.
- Console handler: level `INFO`, simple human-readable formatter.
- File handler: level `DEBUG`, JSON-lines formatter, writes to `runs/<run_id>/trace.log`.
- Every module uses `logger = logging.getLogger(__name__)` — no custom logger classes, no
  per-module handler setup.

## Level conventions (enforced by convention, not code)
- `DEBUG` — full prompts/responses, raw critic checklist output.
- `INFO` — state transitions and decisions: plan approved, episode N drafted, episode N
  approved/edited/rejected, directive added, episode marked stale.
- `WARNING` — LLM call retried, critic failed and revision triggered.
- `ERROR` — retries exhausted, revision cap hit and escalated to human.

## Relationship to the DB (`llm_calls` table)
Logging and persistence are both written from the same place (`llm/base.py`'s
`call_with_tracking`), but serve different purposes:
- Log file = human-readable/greppable trace of *what happened and why*, per run.
- DB table = structured, queryable record of cost/tokens/latency for `cost-report`.
No duplication of logic — one wrapper function emits both.

## Implementation notes
- `configure_logging` is idempotent (dictConfig replaces root handlers); noisy SDK loggers are pinned to WARNING.
- The CLI calls it once per command after the run id is known.

## Status: [x] implemented (`src/logging_config.py`), `tests/test_logging_config.py`
