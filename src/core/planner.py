import logging

from pydantic import Field

from src.core.formatting import format_plan
from src.llm.base import LLMClient
from src.llm.structured import generate_model
from src.models import ArcPlan, Beat, Character, Fact, StrictModel, Thread, gen_id
from src.prompts import render
from src.storage import repo

logger = logging.getLogger(__name__)

PLANNER_MAX_TOKENS = 16000
MAX_PLAN_ATTEMPTS = 3


class PlanLockedError(Exception):
    """Plan edits are only allowed before episode 1 is written; later steering goes through directives."""


class PlanInvalidError(Exception):
    pass


class PlannedRelationship(StrictModel):
    other_key: str
    description: str


class PlannedCharacter(StrictModel):
    key: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str
    traits: list[str]
    planned_arc: str
    starting_arc_stage: str
    relationships: list[PlannedRelationship]


class PlannedThread(StrictModel):
    key: str
    description: str
    planted_episode: int
    resolution_target_episode: int


class PlannedBeat(StrictModel):
    episode_start: int
    episode_end: int
    title: str
    summary: str
    turning_point: bool
    character_keys: list[str]
    thread_keys: list[str]


class PlannedFact(StrictModel):
    statement: str
    tags: list[str]


class PlanDraft(StrictModel):
    overview: str
    characters: list[PlannedCharacter]
    threads: list[PlannedThread]
    beats: list[PlannedBeat]
    facts: list[PlannedFact]


def validate_draft(draft: PlanDraft, total_episodes: int) -> list[str]:
    problems: list[str] = []
    char_keys = {c.key for c in draft.characters}
    thread_keys = {t.key for t in draft.threads}
    if len(char_keys) != len(draft.characters):
        problems.append("character keys must be unique")
    if len(thread_keys) != len(draft.threads):
        problems.append("thread keys must be unique")
    if not any(c.role == "protagonist" for c in draft.characters):
        problems.append('no character has role "protagonist"')

    expected_start = 1
    for beat in sorted(draft.beats, key=lambda b: b.episode_start):
        if beat.episode_start != expected_start:
            problems.append(f"beats must be contiguous: expected a beat starting at episode {expected_start}, found {beat.episode_start}")
            break
        if beat.episode_end < beat.episode_start:
            problems.append(f"beat '{beat.title}' ends before it starts")
            break
        expected_start = beat.episode_end + 1
    else:
        if expected_start != total_episodes + 1:
            problems.append(f"beats must end exactly at episode {total_episodes}, they end at {expected_start - 1}")

    for thread in draft.threads:
        if not 1 <= thread.planted_episode <= total_episodes or not 1 <= thread.resolution_target_episode <= total_episodes:
            problems.append(f"thread '{thread.key}' has episodes outside 1-{total_episodes}")
    return problems[:12]


def _generate_valid_draft(run_id: str, client: LLMClient, premise: str, total_episodes: int, existing: str, feedback: str) -> PlanDraft:
    prompt = render(
        "planner",
        premise=premise,
        total_episodes=str(total_episodes),
        existing_plan_section=f"Current plan to revise:\n{existing}" if existing else "",
        feedback_section=f"Human feedback to incorporate (this overrides the current plan where they conflict):\n{feedback}" if feedback else "",
    )
    user = prompt.user
    for attempt in range(MAX_PLAN_ATTEMPTS):
        draft = generate_model(client, PlanDraft, run_id=run_id, stage="plan", system=prompt.system, prompt=user, max_tokens=PLANNER_MAX_TOKENS)
        problems = validate_draft(draft, total_episodes)
        if not problems:
            return draft
        logger.warning("plan attempt %d invalid: %s", attempt, "; ".join(problems))
        user = prompt.user + "\n\nYour previous plan had these problems; fix all of them in the full plan:\n- " + "\n- ".join(problems)
    raise PlanInvalidError("planner could not produce a valid plan: " + "; ".join(problems))


def _resolve_keys(keys: list[str], ids: dict[str, str], beat_title: str) -> list[str]:
    """Maps plan keys to ids; a reference to a key the plan never defined is dropped (a dangling link, not a plan failure)."""
    unknown = [k for k in keys if k not in ids]
    if unknown:
        logger.warning("beat '%s' referenced undefined keys %s; dropped", beat_title, unknown)
    return [ids[k] for k in keys if k in ids]


def _persist(run_id: str, premise: str, total_episodes: int, version: int, draft: PlanDraft) -> ArcPlan:
    char_ids = {c.key: gen_id("char") for c in draft.characters}
    thread_ids = {t.key: gen_id("thread") for t in draft.threads}

    repo.clear_world_state(run_id)
    for c in draft.characters:
        repo.upsert_character(
            run_id,
            Character(
                character_id=char_ids[c.key],
                name=c.name,
                aliases=c.aliases,
                role=c.role,
                traits=c.traits,
                planned_arc=c.planned_arc,
                arc_stage=c.starting_arc_stage,
                relationships={char_ids[r.other_key]: r.description for r in c.relationships if r.other_key in char_ids},
            ),
        )
    for t in draft.threads:
        repo.upsert_thread(
            run_id,
            Thread(
                thread_id=thread_ids[t.key],
                description=t.description,
                status="planned",
                planted_episode=t.planted_episode,
                last_touched_episode=t.planted_episode,
                resolution_target_episode=t.resolution_target_episode,
            ),
        )
    for f in draft.facts:
        repo.add_fact(run_id, Fact(statement=f.statement, established_episode=0, tags=[tag.lower() for tag in f.tags]))

    plan = ArcPlan(
        run_id=run_id,
        premise=premise,
        overview=draft.overview,
        total_episodes=total_episodes,
        version=version,
        beats=[
            Beat(
                episode_start=b.episode_start,
                episode_end=b.episode_end,
                title=b.title,
                summary=b.summary,
                turning_point=b.turning_point,
                character_ids=_resolve_keys(b.character_keys, char_ids, b.title),
                thread_ids=_resolve_keys(b.thread_keys, thread_ids, b.title),
            )
            for b in sorted(draft.beats, key=lambda b: b.episode_start)
        ],
    )
    repo.save_arc_plan(run_id, plan)
    repo.save_plan_snapshot(run_id, plan.version, format_plan(plan, repo.get_characters(run_id), repo.get_threads(run_id), repo.get_facts(run_id)))
    return plan


def _require_unlocked(run_id: str) -> None:
    if repo.count_episodes(run_id) > 0:
        raise PlanLockedError("plan is locked once episodes exist; use feedback directives to steer the story")


def generate_arc_plan(run_id: str, client: LLMClient, total_episodes: int = 200, feedback: str = "") -> ArcPlan:
    """Fresh plan from the run's premise (also used for 'reject and regenerate')."""
    _require_unlocked(run_id)
    run = repo.require_run(run_id)
    previous = repo.get_arc_plan(run_id)
    version = previous.version + 1 if previous else 1
    draft = _generate_valid_draft(run_id, client, run.premise, total_episodes, existing="", feedback=feedback)
    plan = _persist(run_id, run.premise, total_episodes, version, draft)
    logger.info("arc plan v%d generated: %d beats, %d characters, %d threads", plan.version, len(plan.beats), len(draft.characters), len(draft.threads))
    return plan


def revise_arc_plan(run_id: str, client: LLMClient, feedback: str) -> ArcPlan:
    """Re-plans with the current plan plus human feedback. Replaces the plan wholesale and logs what moved."""
    _require_unlocked(run_id)
    current = repo.require_arc_plan(run_id)
    characters, threads = repo.get_characters(run_id), repo.get_threads(run_id)
    existing = format_plan(current, characters, threads, repo.get_facts(run_id))
    draft = _generate_valid_draft(run_id, client, current.premise, current.total_episodes, existing=existing, feedback=feedback)
    plan = _persist(run_id, current.premise, current.total_episodes, current.version + 1, draft)

    before, after = {c.name for c in characters}, {c.name for c in draft.characters}
    logger.info(
        "arc plan revised v%d -> v%d: beats %d -> %d, characters added=%s removed=%s",
        current.version, plan.version, len(current.beats), len(plan.beats), sorted(after - before), sorted(before - after),
    )
    return plan


def approve_arc_plan(run_id: str) -> ArcPlan:
    plan = repo.require_arc_plan(run_id)
    plan.status = "approved"
    repo.save_arc_plan(run_id, plan)
    logger.info("arc plan v%d approved", plan.version)
    return plan
