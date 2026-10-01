from src.core.planner import (
    PlanDraft,
    PlannedBeat,
    PlannedCharacter,
    PlannedFact,
    PlannedRelationship,
    PlannedThread,
)


def make_draft(total: int = 20, beat_len: int = 5) -> PlanDraft:
    beats = [
        PlannedBeat(
            episode_start=start,
            episode_end=min(start + beat_len - 1, total),
            title=f"Beat {start}",
            summary=f"things happen from {start}",
            turning_point=start == 1 + beat_len,
            character_keys=["kavi", "verma"],
            thread_keys=["ledger"] if start == 1 else [],
        )
        for start in range(1, total + 1, beat_len)
    ]
    return PlanDraft(
        overview="three acts",
        characters=[
            PlannedCharacter(
                key="kavi", name="Kavi", aliases=["the rider"], role="protagonist", traits=["stubborn"],
                planned_arc="denial -> doubt -> truth", starting_arc_stage="denial",
                relationships=[PlannedRelationship(other_key="verma", description="reluctant ally")],
            ),
            PlannedCharacter(
                key="verma", name="Mr. Verma", aliases=["the old man"], role="supporting", traits=["secretive"],
                planned_arc="hides -> confesses", starting_arc_stage="hiding", relationships=[],
            ),
        ],
        threads=[
            PlannedThread(key="ledger", description="who owns the ledger", planted_episode=1, resolution_target_episode=total),
            PlannedThread(key="basement", description="what is in the basement", planted_episode=8, resolution_target_episode=total),
        ],
        beats=beats,
        facts=[PlannedFact(statement="The building has seven floors", tags=["core", "building"])],
    )


def words(n: int, prefix: str = "w") -> str:
    """n distinct words, so two calls with different prefixes share no 5-grams."""
    return " ".join(f"{prefix}{i}" for i in range(n))


def make_context(run_id: str = "run_x", episode_no: int = 5, recent: list | None = None, roster: list | None = None, directives: list | None = None, facts: list | None = None):
    from src.models import Beat, EpisodeContext

    return EpisodeContext(
        run_id=run_id,
        episode_no=episode_no,
        premise="a rider and the dead addresses",
        overview="three acts",
        beat=Beat(beat_id="b", episode_start=1, episode_end=10, title="The Route", summary="the first address"),
        roster=roster or [],
        recent_episodes=recent or [],
        directives=directives or [],
        facts=facts or [],
    )


def make_extraction(**overrides):
    from src.core.extractor import Extraction

    base = {"summary": "stuff happens", "characters": [], "threads": [], "new_facts": []}
    return Extraction(**{**base, **overrides})


def ext_char(name: str, status: str = "alive", character_id: str | None = None, **kw):
    from src.core.extractor import ExtractedCharacter

    defaults = {"role": None, "new_aliases": [], "new_traits": [], "relationships": [], "arc_stage": None, "appears_on_page": True}
    return ExtractedCharacter(character_id=character_id, name=name, status=status, **{**defaults, **kw})


def ext_thread(description: str, status: str, thread_id: str | None = None):
    from src.core.extractor import ExtractedThread

    return ExtractedThread(thread_id=thread_id, description=description, status=status, evidence="")
