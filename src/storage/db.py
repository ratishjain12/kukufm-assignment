"""SQLite connection management. One database file per run, at runs/<run_id>/state.db.

Tables deliberately do NOT carry a run_id column: the file itself is the run boundary,
so a redundant column could only ever drift from the filename. repo.py re-attaches
run_id to returned models from the run_id argument already in the caller's hand.
"""

import sqlite3
from pathlib import Path

RUNS_DIR = Path("runs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    premise TEXT NOT NULL,
    current_episode INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS arc_plans (
    run_id TEXT PRIMARY KEY,
    premise TEXT NOT NULL,
    overview TEXT NOT NULL,
    total_episodes INTEGER NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS beats (
    beat_id TEXT PRIMARY KEY,
    episode_start INTEGER NOT NULL,
    episode_end INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    turning_point INTEGER NOT NULL,
    character_ids TEXT NOT NULL,
    thread_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    character_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    traits TEXT NOT NULL,
    relationships TEXT NOT NULL,
    planned_arc TEXT NOT NULL,
    arc_stage TEXT NOT NULL,
    last_seen_episode INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    planted_episode INTEGER NOT NULL,
    last_touched_episode INTEGER NOT NULL,
    resolution_target_episode INTEGER
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    established_episode INTEGER NOT NULL,
    tags TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directives (
    directive_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at_episode INTEGER NOT NULL,
    scope TEXT NOT NULL,
    expires_after_episode INTEGER,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_no INTEGER PRIMARY KEY,
    beat_id TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    critic_notes TEXT NOT NULL,
    revision_count INTEGER NOT NULL,
    human_feedback TEXT,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_deltas (
    episode_no INTEGER PRIMARY KEY,
    changes TEXT NOT NULL,
    new_entity_ids TEXT NOT NULL,
    extraction TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS block_summaries (
    block_no INTEGER PRIMARY KEY,
    episode_start INTEGER NOT NULL,
    episode_end INTEGER NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id TEXT PRIMARY KEY,
    episode_no INTEGER,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);
"""


def db_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "state.db"


def connect(run_id: str) -> sqlite3.Connection:
    path = db_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def list_run_ids() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(p.parent.name for p in RUNS_DIR.glob("*/state.db"))
