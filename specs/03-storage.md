# 03 — Storage (`src/storage/`)

## `db.py`
SQLite, one file per run at `runs/<run_id>/state.db`. Schema (tables mirror `models.py`):
`runs`, `arc_plans`, `beats`, `episodes`, `characters`, `threads`, `facts`, `directives`,
`llm_calls`. Plain `CREATE TABLE IF NOT EXISTS` on connect — no migration framework needed at
this scope.

## `repo.py`
Only module allowed to touch SQL. Typed functions, pydantic in/out, e.g.:

```python
def create_run(premise: str) -> RunState: ...
def load_run(run_id: str) -> RunState: ...
def save_arc_plan(plan: ArcPlan) -> None: ...
def get_arc_plan(run_id: str) -> ArcPlan: ...

def save_episode(episode: Episode) -> None: ...
def get_episode(run_id: str, episode_no: int) -> Episode | None: ...
def get_recent_episodes(run_id: str, n: int) -> list[Episode]: ...

def upsert_character(run_id: str, character: Character) -> None: ...
def get_characters(run_id: str) -> list[Character]: ...

def upsert_thread(run_id: str, thread: Thread) -> None: ...
def get_open_threads(run_id: str) -> list[Thread]: ...          # status != resolved/abandoned
def get_stale_threads(run_id: str, current_ep: int, staleness: int = 15) -> list[Thread]: ...

def add_fact(run_id: str, fact: Fact) -> None: ...
def get_facts(run_id: str, tags: list[str] | None = None) -> list[Fact]: ...

def add_directive(run_id: str, directive: Directive) -> None: ...
def get_active_directives(run_id: str, current_ep: int) -> list[Directive]: ...
def retire_directive(directive_id: str) -> None: ...

def mark_episodes_stale(run_id: str, from_episode: int) -> list[int]: ...  # retroactive edit

def log_llm_call(call: LLMCallLog) -> None: ...
def sum_run_cost(run_id: str) -> dict: ...                      # for cost-report CLI command
```

## Why repository pattern here specifically
- `orchestrator.py` and `cli.py` never write SQL — keeps the state-mutation surface auditable,
  which matters because resume and retroactive-edit correctness depend on *every* write going
  through the same path.
- Swapping SQLite for Postgres later (if this had to actually scale to real users) touches only
  `db.py`/`repo.py`.

## Implementation notes (deviations from original spec, found while implementing)
- **Dropped `run_id` as a column** on every table except `runs`. Since storage is already one
  SQLite file per run (`runs/<run_id>/state.db`), the file *is* the run boundary — a `run_id`
  column everywhere would be redundant data that could only ever drift from the filename.
  `repo.py` re-attaches `run_id` to returned models from the parameter already in hand.
  All function signatures still take `run_id` as the first arg (per this spec) — it now
  selects *which file to open*, not a WHERE-clause filter.
- Connections are opened and closed per call (`_query`/`_transact` helpers), not held open
  across a run. Acceptable here because the CLI is human-paced (seconds between calls waiting
  on LLM latency/human review), not a hot loop — would need to change if this became a service.
- Added `find_character_by_name_or_alias(run_id, name)` — not in the original function list.
  Centralizes identity resolution (case-insensitive name/alias match) in `repo.py` rather than
  letting `extractor.py` reimplement it, since this is the exact mechanism preventing duplicate
  character rows ("the old man" vs "Mr. Verma").
- `get_open_threads` and `get_stale_threads` share one definition of "open"
  (`OPEN_THREAD_STATUSES = ("planted", "escalated")`) — one constant, not repeated string literals.

## Later additions
- Tables/columns for `block_summaries`, `episodes.summary`, `characters.planned_arc`, `arc_plans.overview`, `state_deltas.extraction`.
- `get_recent_episodes` was replaced by `get_episodes_before` and `get_episodes_range` (finalized-only by default, so stale future
  episodes never reach a prompt). `get_facts` lost its unused tag filter.
- Added delete functions (characters, threads, facts, deltas from N, block summaries from N) used by the undo log and plan regeneration,
  `clear_world_state`, `sum_episode_cost`, `cost_by_stage`, `stage_tokens`, and `require_*` accessors that raise `LookupError`
  where presence is expected (mypy-clean, no scattered asserts).

## Status: [x] implemented (`src/storage/db.py`, `src/storage/repo.py`), tested in `tests/test_repo.py`; originally integration-tested
against a throwaway run exercising every function (arc plan round-trip, alias resolution,
thread staleness, directive expiry window, episode save/recent/stale-marking, delta round-trip,
cost aggregation).
