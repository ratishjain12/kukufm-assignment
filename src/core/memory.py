import logging

from src.models import (
    Beat,
    Character,
    EpisodeContext,
    EpisodeSummary,
    Fact,
    Thread,
)
from src.storage import repo

logger = logging.getLogger(__name__)

BLOCK_SIZE = 10
RECENT_VERBATIM = 3
UPCOMING_BEATS = 2
MAX_RELEVANT_CHARACTERS = 10
CHARACTER_RECENCY_WINDOW = 10
MAX_OPEN_THREADS = 8
STALE_THREAD_AFTER = 15
MAX_FACTS = 15
MAX_AUTHOR_NOTES = 10
MAX_THREADS_TO_INTRODUCE = 4
CORE_FACT_TAG = "core"


def block_no_for(episode_no: int) -> int:
    return (episode_no - 1) // BLOCK_SIZE + 1


def block_range(block_no: int) -> tuple[int, int]:
    return (block_no - 1) * BLOCK_SIZE + 1, block_no * BLOCK_SIZE


def _select_characters(roster: list[Character], beat: Beat, episode_no: int) -> list[Character]:
    def relevant(c: Character) -> bool:
        recent = c.last_seen_episode >= episode_no - CHARACTER_RECENCY_WINDOW and c.last_seen_episode > 0
        return c.role == "protagonist" or c.character_id in beat.character_ids or recent

    chosen = sorted(
        (c for c in roster if relevant(c)),
        key=lambda c: (c.character_id not in beat.character_ids, c.role != "protagonist", -c.last_seen_episode),
    )
    if len(chosen) > MAX_RELEVANT_CHARACTERS:
        logger.warning("episode %d: %d relevant characters, truncated to %d", episode_no, len(chosen), MAX_RELEVANT_CHARACTERS)
    return chosen[:MAX_RELEVANT_CHARACTERS]


def _select_threads(run_id: str, beat: Beat, episode_no: int) -> tuple[list[Thread], list[Thread]]:
    open_threads = repo.get_open_threads(run_id)
    stale_ids = {t.thread_id for t in repo.get_stale_threads(run_id, episode_no, STALE_THREAD_AFTER)}
    ordered = sorted(
        open_threads,
        key=lambda t: (t.thread_id not in beat.thread_ids, t.thread_id not in stale_ids),
    )
    if len(ordered) > MAX_OPEN_THREADS:
        logger.warning(
            "episode %d: open-thread overload, %d open, showing %d (beat threads and neglected threads first)",
            episode_no, len(ordered), MAX_OPEN_THREADS,
        )
    selected = ordered[:MAX_OPEN_THREADS]
    return selected, [t for t in selected if t.thread_id in stale_ids]


def _select_threads_to_introduce(run_id: str, beat: Beat, episode_no: int) -> list[Thread]:
    due = [
        t for t in repo.get_threads(run_id)
        if t.status == "planned" and (t.thread_id in beat.thread_ids or t.planted_episode <= episode_no)
    ]
    return sorted(due, key=lambda t: t.planted_episode)[:MAX_THREADS_TO_INTRODUCE]


def _select_author_notes(run_id: str) -> list[Fact]:
    """Plan-seeded world truths (established_episode 0): what the author knows, not what the reader has been told."""
    return [f for f in repo.get_facts(run_id) if f.established_episode == 0][:MAX_AUTHOR_NOTES]


def _select_facts(run_id: str, beat: Beat, characters: list[Character]) -> list[Fact]:
    beat_text = f"{beat.title} {beat.summary}".lower()
    wanted = {name.lower() for c in characters for name in (c.name, *c.aliases)}
    facts = [f for f in repo.get_facts(run_id) if f.established_episode > 0]

    def relevant(f: Fact) -> bool:
        tags = {t.lower() for t in f.tags}
        return CORE_FACT_TAG in tags or bool(tags & wanted) or any(t in beat_text for t in tags)

    chosen = sorted((f for f in facts if relevant(f)), key=lambda f: (CORE_FACT_TAG not in f.tags, -f.established_episode))
    if len(chosen) > MAX_FACTS:
        logger.warning("%d relevant facts, truncated to %d", len(chosen), MAX_FACTS)
    return chosen[:MAX_FACTS]


def assemble_context(run_id: str, episode_no: int) -> EpisodeContext:
    """Layered, bounded context for writing episode N: plan excerpt + structured state + two tiers
    of summaries + the last few episodes verbatim. Never a replay of all prior prose."""
    run = repo.require_run(run_id)
    plan = repo.require_arc_plan(run_id)
    beat = plan.beat_for_episode(episode_no)
    if beat is None:
        raise ValueError(f"arc plan has no beat covering episode {episode_no}")

    upcoming = [b for b in plan.beats if b.episode_start > beat.episode_end][:UPCOMING_BEATS]
    roster = repo.get_characters(run_id)
    relevant = _select_characters(roster, beat, episode_no)
    open_threads, neglected = _select_threads(run_id, beat, episode_no)

    recent = repo.get_episodes_before(run_id, episode_no, RECENT_VERBATIM)
    blocks = repo.get_block_summaries(run_id, ending_before=episode_no)
    contiguous = [b.block_no for b in blocks] == list(range(1, len(blocks) + 1))
    if not contiguous:
        logger.warning("episode %d: block summaries are not contiguous, falling back to per-episode summaries", episode_no)
        blocks = []
    uncovered_start = blocks[-1].episode_end + 1 if blocks else 1
    one_liner_end = (recent[0].episode_no if recent else episode_no) - 1
    older = repo.get_episodes_range(run_id, uncovered_start, one_liner_end) if one_liner_end >= uncovered_start else []

    return EpisodeContext(
        run_id=run_id,
        episode_no=episode_no,
        premise=run.premise,
        overview=plan.overview,
        beat=beat,
        upcoming_beats=upcoming,
        relevant_characters=relevant,
        roster=roster,
        open_threads=open_threads,
        neglected_threads=neglected,
        threads_to_introduce=_select_threads_to_introduce(run_id, beat, episode_no),
        directives=repo.get_active_directives(run_id, episode_no),
        facts=_select_facts(run_id, beat, relevant),
        author_notes=_select_author_notes(run_id),
        block_summaries=blocks,
        episode_summaries=[EpisodeSummary(episode_no=e.episode_no, summary=e.summary) for e in older if e.summary],
        recent_episodes=recent,
    )
