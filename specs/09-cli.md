# 09 — CLI (`src/cli.py`)

Single entrypoint (`main.py` at repo root calls into `src/cli.py`), subcommands:

- `new "<premise>" [--episodes 200] [--provider anthropic]` — creates run, generates arc plan,
  drops into plan review.
- `review-plan <run_id>` — reprint current plan, approve/edit/reject loop.
- `run <run_id> [--count N]` — generate episodes one at a time starting at `current_episode`,
  interactive approve/edit/reject/feedback per episode, stop after N or on quit.
- `resume <run_id>` — same as `run` but explicitly re-entering an existing run (mostly sugar
  over `run`, kept separate for clarity in the demo/README).
- `edit-episode <run_id> <episode_no>` — retroactive edit flow (08).
- `cost-report <run_id>` — sums `llm_calls`, prints actual spend so far + extrapolation to 200
  episodes (feeds the "cost-aware" requirement and DECISIONS.md numbers).
- `show <run_id> [--episode N]` — print plan or a specific episode, for demo/recording use.

No TUI framework needed — `input()`/`print()` plus `rich` (optional, for readable tables/panels)
is enough. Keep it scriptable: non-interactive flags (`--approve-all`, `--feedback "..."`) are
useful for the demo recording and for automated tests of the orchestrator loop.

## Implementation notes
- Plain `argparse` + `input()`; `rich` dropped to keep setup minimal. Gates live in `src/gates.py`.
- Commands: new, review-plan, run, resume, edit-episode (`--file`, `--invalidate`, `--keep`), cost-report, show, directives, list.
- Unknown run ids are rejected before touching the filesystem; LLM/validation failures exit 1 with a resume hint.

## Status: [x] implemented (`src/cli.py`, `src/gates.py`), `tests/test_cli.py`, `tests/test_gates.py`
