"""Typed CRUD only. This is the single module allowed to touch SQL — orchestrator.py
and cli.py always go through here, which is what makes resume and retroactive-edit
correctness auditable (every state mutation has exactly one code path).
"""

import json
import sqlite3
from collections.abc import Callable

from src.models import (
    FINALIZED_STATUSES,
    ArcPlan,
    Beat,
    BlockSummary,
    Character,
    Directive,
    Episode,
    Fact,
    FieldChange,
    LLMCallLog,
    RunState,
    StateDelta,
    Thread,
    utc_now,
)
from src.storage import db

OPEN_THREAD_STATUSES = ("planted", "escalated")
MAX_EPISODE_NO = 10**9


def _query[T](run_id: str, fn: Callable[[sqlite3.Connection], T]) -> T:
    conn = db.connect(run_id)
    try:
        return fn(conn)
    finally:
        conn.close()


def _transact[T](run_id: str, fn: Callable[[sqlite3.Connection], T]) -> T:
    conn = db.connect(run_id)
    try:
        result = fn(conn)
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------- runs ----

def create_run(premise: str) -> RunState:
    run = RunState(premise=premise)
    _transact(
        run.run_id,
        lambda conn: conn.execute(
            "INSERT INTO runs (run_id, premise, current_episode, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run.run_id, run.premise, run.current_episode, run.created_at.isoformat(), run.updated_at.isoformat()),
        ),
    )
    return run


def get_run(run_id: str) -> RunState | None:
    def _op(conn: sqlite3.Connection) -> RunState | None:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunState(**dict(row))

    return _query(run_id, _op)


def update_run_progress(run_id: str, current_episode: int) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "UPDATE runs SET current_episode = ?, updated_at = ? WHERE run_id = ?",
            (current_episode, utc_now().isoformat(), run_id),
        ),
    )


def require_run(run_id: str) -> RunState:
    run = get_run(run_id)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    return run


# ------------------------------------------------------------ arc plan ----

def save_arc_plan(run_id: str, plan: ArcPlan) -> None:
    def _op(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO arc_plans (run_id, premise, overview, total_episodes, status, version) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET premise=excluded.premise, overview=excluded.overview, "
            "total_episodes=excluded.total_episodes, status=excluded.status, version=excluded.version",
            (plan.run_id, plan.premise, plan.overview, plan.total_episodes, plan.status, plan.version),
        )
        conn.execute("DELETE FROM beats")
        for beat in plan.beats:
            conn.execute(
                "INSERT INTO beats (beat_id, episode_start, episode_end, title, summary, "
                "turning_point, character_ids, thread_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    beat.beat_id,
                    beat.episode_start,
                    beat.episode_end,
                    beat.title,
                    beat.summary,
                    int(beat.turning_point),
                    json.dumps(beat.character_ids),
                    json.dumps(beat.thread_ids),
                ),
            )

    _transact(run_id, _op)


def get_arc_plan(run_id: str) -> ArcPlan | None:
    def _op(conn: sqlite3.Connection) -> ArcPlan | None:
        row = conn.execute("SELECT * FROM arc_plans WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        beat_rows = conn.execute("SELECT * FROM beats ORDER BY episode_start").fetchall()
        beats = [
            Beat(
                beat_id=r["beat_id"],
                episode_start=r["episode_start"],
                episode_end=r["episode_end"],
                title=r["title"],
                summary=r["summary"],
                turning_point=bool(r["turning_point"]),
                character_ids=json.loads(r["character_ids"]),
                thread_ids=json.loads(r["thread_ids"]),
            )
            for r in beat_rows
        ]
        return ArcPlan(
            run_id=row["run_id"],
            premise=row["premise"],
            overview=row["overview"],
            total_episodes=row["total_episodes"],
            beats=beats,
            status=row["status"],
            version=row["version"],
        )

    return _query(run_id, _op)


def require_arc_plan(run_id: str) -> ArcPlan:
    plan = get_arc_plan(run_id)
    if plan is None:
        raise LookupError(f"run {run_id} has no arc plan")
    return plan


def save_plan_snapshot(run_id: str, version: int, text: str) -> None:
    """Frozen readable copy of a plan version. The live plan tables are overwritten on revision and later mixed with
    story-derived state, so history lives in plain files next to the database."""
    path = db.db_path(run_id).parent / f"plan_v{version}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def get_plan_snapshots(run_id: str) -> dict[int, str]:
    folder = db.db_path(run_id).parent
    snapshots = {}
    for path in folder.glob("plan_v*.txt"):
        snapshots[int(path.stem.removeprefix("plan_v"))] = path.read_text()
    return dict(sorted(snapshots.items()))


# ----------------------------------------------------------- characters ----

def _row_to_character(row: sqlite3.Row) -> Character:
    return Character(
        character_id=row["character_id"],
        name=row["name"],
        aliases=json.loads(row["aliases"]),
        role=row["role"],
        status=row["status"],
        traits=json.loads(row["traits"]),
        relationships=json.loads(row["relationships"]),
        planned_arc=row["planned_arc"],
        arc_stage=row["arc_stage"],
        last_seen_episode=row["last_seen_episode"],
    )


def upsert_character(run_id: str, character: Character) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO characters (character_id, name, aliases, role, status, traits, "
            "relationships, planned_arc, arc_stage, last_seen_episode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(character_id) DO UPDATE SET name=excluded.name, aliases=excluded.aliases, "
            "role=excluded.role, status=excluded.status, traits=excluded.traits, "
            "relationships=excluded.relationships, planned_arc=excluded.planned_arc, "
            "arc_stage=excluded.arc_stage, last_seen_episode=excluded.last_seen_episode",
            (
                character.character_id,
                character.name,
                json.dumps(character.aliases),
                character.role,
                character.status,
                json.dumps(character.traits),
                json.dumps(character.relationships),
                character.planned_arc,
                character.arc_stage,
                character.last_seen_episode,
            ),
        ),
    )


def get_characters(run_id: str) -> list[Character]:
    return _query(
        run_id,
        lambda conn: [_row_to_character(r) for r in conn.execute("SELECT * FROM characters").fetchall()],
    )


def get_character(run_id: str, character_id: str) -> Character | None:
    def _op(conn: sqlite3.Connection) -> Character | None:
        row = conn.execute(
            "SELECT * FROM characters WHERE character_id = ?", (character_id,)
        ).fetchone()
        return _row_to_character(row) if row else None

    return _query(run_id, _op)


def delete_character(run_id: str, character_id: str) -> None:
    _transact(run_id, lambda conn: conn.execute("DELETE FROM characters WHERE character_id = ?", (character_id,)))


def require_character(run_id: str, character_id: str) -> Character:
    character = get_character(run_id, character_id)
    if character is None:
        raise LookupError(f"character {character_id} not found in run {run_id}")
    return character


def find_character_by_name_or_alias(run_id: str, name: str) -> Character | None:
    """Case-insensitive match against name or any known alias.

    Lives here (not duplicated in extractor.py) because identity resolution is the
    single biggest risk to "no forgotten characters" at scale: if the writer calls
    someone "the old man" in episode 3 and "Mr. Verma" in episode 40, the extractor
    must resolve to the same character_id rather than minting a duplicate.
    """
    needle = name.strip().lower()
    for character in get_characters(run_id):
        if character.name.strip().lower() == needle:
            return character
        if needle in (alias.strip().lower() for alias in character.aliases):
            return character
    return None


# --------------------------------------------------------------- threads ----

def _row_to_thread(row: sqlite3.Row) -> Thread:
    return Thread(
        thread_id=row["thread_id"],
        description=row["description"],
        status=row["status"],
        planted_episode=row["planted_episode"],
        last_touched_episode=row["last_touched_episode"],
        resolution_target_episode=row["resolution_target_episode"],
    )


def upsert_thread(run_id: str, thread: Thread) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO threads (thread_id, description, status, planted_episode, "
            "last_touched_episode, resolution_target_episode) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET description=excluded.description, "
            "status=excluded.status, last_touched_episode=excluded.last_touched_episode, "
            "resolution_target_episode=excluded.resolution_target_episode",
            (
                thread.thread_id,
                thread.description,
                thread.status,
                thread.planted_episode,
                thread.last_touched_episode,
                thread.resolution_target_episode,
            ),
        ),
    )


def get_thread(run_id: str, thread_id: str) -> Thread | None:
    def _op(conn: sqlite3.Connection) -> Thread | None:
        row = conn.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        return _row_to_thread(row) if row else None

    return _query(run_id, _op)


def delete_thread(run_id: str, thread_id: str) -> None:
    _transact(run_id, lambda conn: conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,)))


def get_threads(run_id: str) -> list[Thread]:
    return _query(
        run_id, lambda conn: [_row_to_thread(r) for r in conn.execute("SELECT * FROM threads").fetchall()]
    )


def get_open_threads(run_id: str) -> list[Thread]:
    placeholders = ",".join("?" for _ in OPEN_THREAD_STATUSES)
    return _query(
        run_id,
        lambda conn: [
            _row_to_thread(r)
            for r in conn.execute(
                f"SELECT * FROM threads WHERE status IN ({placeholders}) "
                "ORDER BY resolution_target_episode IS NULL, resolution_target_episode ASC",
                OPEN_THREAD_STATUSES,
            ).fetchall()
        ],
    )


def get_stale_threads(run_id: str, current_episode: int, staleness: int = 15) -> list[Thread]:
    cutoff = current_episode - staleness
    return [t for t in get_open_threads(run_id) if t.last_touched_episode <= cutoff]


# ----------------------------------------------------------------- facts ----

def add_fact(run_id: str, fact: Fact) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO facts (fact_id, statement, established_episode, tags) VALUES (?, ?, ?, ?)",
            (fact.fact_id, fact.statement, fact.established_episode, json.dumps(fact.tags)),
        ),
    )


def delete_fact(run_id: str, fact_id: str) -> None:
    _transact(run_id, lambda conn: conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,)))


def clear_world_state(run_id: str) -> None:
    """Drops seeded characters/threads/facts. Only valid before episode 1 (plan regeneration)."""

    def _op(conn: sqlite3.Connection) -> None:
        for table in ("characters", "threads", "facts"):
            conn.execute(f"DELETE FROM {table}")

    _transact(run_id, _op)


def get_facts(run_id: str) -> list[Fact]:
    return _query(
        run_id,
        lambda conn: [
            Fact(
                fact_id=r["fact_id"],
                statement=r["statement"],
                established_episode=r["established_episode"],
                tags=json.loads(r["tags"]),
            )
            for r in conn.execute("SELECT * FROM facts").fetchall()
        ],
    )


# ------------------------------------------------------------- directives ----

def _row_to_directive(row: sqlite3.Row) -> Directive:
    return Directive(
        directive_id=row["directive_id"],
        text=row["text"],
        created_at_episode=row["created_at_episode"],
        scope=row["scope"],
        expires_after_episode=row["expires_after_episode"],
        status=row["status"],
    )


def add_directive(run_id: str, directive: Directive) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO directives (directive_id, text, created_at_episode, scope, "
            "expires_after_episode, status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                directive.directive_id,
                directive.text,
                directive.created_at_episode,
                directive.scope,
                directive.expires_after_episode,
                directive.status,
            ),
        ),
    )


def get_directives(run_id: str) -> list[Directive]:
    return _query(
        run_id,
        lambda conn: [_row_to_directive(row) for row in conn.execute("SELECT * FROM directives ORDER BY created_at_episode, rowid").fetchall()],
    )


def get_active_directives(run_id: str, current_episode: int) -> list[Directive]:
    def _op(conn: sqlite3.Connection) -> list[Directive]:
        rows = conn.execute("SELECT * FROM directives WHERE status = 'active'").fetchall()
        directives = [_row_to_directive(r) for r in rows]
        return [d for d in directives if d.active_for(current_episode)]

    return _query(run_id, _op)


def retire_directive(run_id: str, directive_id: str) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "UPDATE directives SET status = 'retired' WHERE directive_id = ?", (directive_id,)
        ),
    )


# -------------------------------------------------------------- episodes ----

def _row_to_episode(run_id: str, row: sqlite3.Row) -> Episode:
    return Episode(
        episode_no=row["episode_no"],
        run_id=run_id,
        beat_id=row["beat_id"],
        text=row["text"],
        summary=row["summary"],
        status=row["status"],
        critic_notes=json.loads(row["critic_notes"]),
        revision_count=row["revision_count"],
        human_feedback=row["human_feedback"],
        source=row["source"],
    )


def save_episode(run_id: str, episode: Episode) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO episodes (episode_no, beat_id, text, summary, status, critic_notes, "
            "revision_count, human_feedback, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(episode_no) DO UPDATE SET beat_id=excluded.beat_id, text=excluded.text, "
            "summary=excluded.summary, status=excluded.status, critic_notes=excluded.critic_notes, "
            "revision_count=excluded.revision_count, human_feedback=excluded.human_feedback, "
            "source=excluded.source",
            (
                episode.episode_no,
                episode.beat_id,
                episode.text,
                episode.summary,
                episode.status,
                json.dumps(episode.critic_notes),
                episode.revision_count,
                episode.human_feedback,
                episode.source,
            ),
        ),
    )


def get_episode(run_id: str, episode_no: int) -> Episode | None:
    def _op(conn: sqlite3.Connection) -> Episode | None:
        row = conn.execute("SELECT * FROM episodes WHERE episode_no = ?", (episode_no,)).fetchone()
        return _row_to_episode(run_id, row) if row else None

    return _query(run_id, _op)


def require_episode(run_id: str, episode_no: int) -> Episode:
    episode = get_episode(run_id, episode_no)
    if episode is None:
        raise LookupError(f"episode {episode_no} not found in run {run_id}")
    return episode


def get_episodes_before(run_id: str, episode_no: int, n: int) -> list[Episode]:
    """Up to n finalized episodes strictly before episode_no, oldest first."""
    placeholders = ",".join("?" for _ in FINALIZED_STATUSES)

    def _op(conn: sqlite3.Connection) -> list[Episode]:
        rows = conn.execute(
            f"SELECT * FROM episodes WHERE episode_no < ? AND status IN ({placeholders}) "
            "ORDER BY episode_no DESC LIMIT ?",
            (episode_no, *FINALIZED_STATUSES, n),
        ).fetchall()
        return [_row_to_episode(run_id, r) for r in reversed(rows)]

    return _query(run_id, _op)


def get_episodes_range(run_id: str, start: int, end: int | None = None, finalized_only: bool = True) -> list[Episode]:
    """Episodes from start to end inclusive (no upper bound when end is None)."""
    end = end if end is not None else MAX_EPISODE_NO
    placeholders = ",".join("?" for _ in FINALIZED_STATUSES)
    status_clause = f" AND status IN ({placeholders})" if finalized_only else ""
    params = (start, end, *FINALIZED_STATUSES) if finalized_only else (start, end)

    def _op(conn: sqlite3.Connection) -> list[Episode]:
        rows = conn.execute(
            f"SELECT * FROM episodes WHERE episode_no BETWEEN ? AND ?{status_clause} ORDER BY episode_no",
            params,
        ).fetchall()
        return [_row_to_episode(run_id, r) for r in rows]

    return _query(run_id, _op)


def count_episodes(run_id: str) -> int:
    return _query(run_id, lambda conn: conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"])


def mark_episodes_stale(run_id: str, from_episode: int) -> list[int]:
    def _op(conn: sqlite3.Connection) -> list[int]:
        rows = conn.execute(
            "SELECT episode_no FROM episodes WHERE episode_no >= ? AND status != 'stale'",
            (from_episode,),
        ).fetchall()
        affected = [r["episode_no"] for r in rows]
        conn.execute(
            "UPDATE episodes SET status = 'stale' WHERE episode_no >= ?", (from_episode,)
        )
        return affected

    return _transact(run_id, _op)


# ---------------------------------------------------------- state deltas ----

def save_state_delta(run_id: str, delta: StateDelta) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO state_deltas (episode_no, changes, new_entity_ids, extraction) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(episode_no) DO UPDATE SET changes=excluded.changes, "
            "new_entity_ids=excluded.new_entity_ids, extraction=excluded.extraction",
            (
                delta.episode_no,
                json.dumps([c.model_dump() for c in delta.changes]),
                json.dumps(delta.new_entity_ids),
                json.dumps(delta.extraction),
            ),
        ),
    )


def get_state_delta(run_id: str, episode_no: int) -> StateDelta | None:
    def _op(conn: sqlite3.Connection) -> StateDelta | None:
        row = conn.execute(
            "SELECT * FROM state_deltas WHERE episode_no = ?", (episode_no,)
        ).fetchone()
        if row is None:
            return None
        return StateDelta(
            run_id=run_id,
            episode_no=row["episode_no"],
            changes=[FieldChange(**c) for c in json.loads(row["changes"])],
            new_entity_ids=json.loads(row["new_entity_ids"]),
            extraction=json.loads(row["extraction"]),
        )

    return _query(run_id, _op)


def require_state_delta(run_id: str, episode_no: int) -> StateDelta:
    delta = get_state_delta(run_id, episode_no)
    if delta is None:
        raise LookupError(f"episode {episode_no} has no state delta in run {run_id}")
    return delta


def delete_state_deltas_from(run_id: str, from_episode: int) -> None:
    _transact(run_id, lambda conn: conn.execute("DELETE FROM state_deltas WHERE episode_no >= ?", (from_episode,)))


# ------------------------------------------------------- block summaries ----

def save_block_summary(run_id: str, block: BlockSummary) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO block_summaries (block_no, episode_start, episode_end, summary) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(block_no) DO UPDATE SET episode_start=excluded.episode_start, "
            "episode_end=excluded.episode_end, summary=excluded.summary",
            (block.block_no, block.episode_start, block.episode_end, block.summary),
        ),
    )


def delete_block_summaries_from(run_id: str, block_no: int) -> None:
    _transact(run_id, lambda conn: conn.execute("DELETE FROM block_summaries WHERE block_no >= ?", (block_no,)))


def get_block_summaries(run_id: str, ending_before: int) -> list[BlockSummary]:
    """Block summaries whose last episode is strictly before `ending_before`."""
    return _query(
        run_id,
        lambda conn: [
            BlockSummary(**dict(r))
            for r in conn.execute(
                "SELECT * FROM block_summaries WHERE episode_end < ? ORDER BY block_no", (ending_before,)
            ).fetchall()
        ],
    )


# -------------------------------------------------------------- llm calls ----

def log_llm_call(run_id: str, call: LLMCallLog) -> None:
    _transact(
        run_id,
        lambda conn: conn.execute(
            "INSERT INTO llm_calls (call_id, episode_no, stage, provider, model, "
            "prompt_tokens, completion_tokens, cost_usd, latency_ms, retry_count, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call.call_id,
                call.episode_no,
                call.stage,
                call.provider,
                call.model,
                call.prompt_tokens,
                call.completion_tokens,
                call.cost_usd,
                call.latency_ms,
                call.retry_count,
                call.timestamp.isoformat(),
            ),
        ),
    )


def sum_run_cost(run_id: str) -> dict:
    def _op(conn: sqlite3.Connection) -> dict:
        row = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS total_cost, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(latency_ms), 0) AS total_latency_ms, "
            "COALESCE(SUM(retry_count), 0) AS total_retries "
            "FROM llm_calls"
        ).fetchone()
        episodes_done = conn.execute(
            "SELECT COUNT(*) AS n FROM episodes WHERE status IN ('approved', 'edited')"
        ).fetchone()["n"]
        return {**dict(row), "episodes_done": episodes_done}

    return _query(run_id, _op)


def sum_episode_cost(run_id: str, episode_no: int) -> float:
    return _query(
        run_id,
        lambda conn: conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE episode_no = ?", (episode_no,)
        ).fetchone()["c"],
    )


def cost_by_stage(run_id: str) -> dict[str, dict]:
    return _query(
        run_id,
        lambda conn: {
            r["stage"]: {"calls": r["calls"], "cost_usd": r["cost"], "latency_ms": r["latency"]}
            for r in conn.execute(
                "SELECT stage, COUNT(*) AS calls, SUM(cost_usd) AS cost, SUM(latency_ms) AS latency "
                "FROM llm_calls GROUP BY stage"
            ).fetchall()
        },
    )


def stage_tokens(run_id: str, stage: str) -> int:
    return _query(
        run_id,
        lambda conn: conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS n FROM llm_calls WHERE stage = ?", (stage,)
        ).fetchone()["n"],
    )
