import pytest

from src.core import memory
from src.core.formatting import format_context
from src.models import (
    ArcPlan,
    Beat,
    BlockSummary,
    Character,
    Directive,
    Episode,
    Fact,
    Thread,
)
from src.storage import repo


def _seed(run_id: str, beats: list[Beat] | None = None) -> None:
    beats = beats or [
        Beat(beat_id="b1", episode_start=1, episode_end=100, title="Act 1", summary="act one"),
        Beat(beat_id="b2", episode_start=101, episode_end=200, title="Act 2", summary="act two"),
    ]
    repo.save_arc_plan(run_id, ArcPlan(run_id=run_id, premise="p", overview="o", beats=beats))


def _finalized(run_id: str, no: int, summary: str | None = None) -> None:
    repo.save_episode(run_id, Episode(episode_no=no, run_id=run_id, beat_id="b1", text=f"text {no}", summary=summary or f"sum {no}", status="approved"))


def test_context_layers_for_midstory_episode(run_id):
    _seed(run_id)
    for no in range(1, 16):
        _finalized(run_id, no)
    repo.save_block_summary(run_id, BlockSummary(block_no=1, episode_start=1, episode_end=10, summary="block one"))

    ctx = memory.assemble_context(run_id, 16)

    assert [e.episode_no for e in ctx.recent_episodes] == [13, 14, 15]
    assert [b.block_no for b in ctx.block_summaries] == [1]
    assert [s.episode_no for s in ctx.episode_summaries] == [11, 12]
    assert [b.title for b in ctx.upcoming_beats] == ["Act 2"]


def test_missing_block_summary_falls_back_to_per_episode_summaries(run_id):
    _seed(run_id)
    for no in range(1, 15):
        _finalized(run_id, no)
    ctx = memory.assemble_context(run_id, 15)
    assert ctx.block_summaries == []
    assert [s.episode_no for s in ctx.episode_summaries] == list(range(1, 12))


def test_stale_future_episodes_never_enter_context(run_id):
    _seed(run_id)
    for no in range(1, 6):
        _finalized(run_id, no)
    repo.mark_episodes_stale(run_id, 3)
    ctx = memory.assemble_context(run_id, 3)
    assert [e.episode_no for e in ctx.recent_episodes] == [1, 2]


def test_open_threads_capped_with_beat_and_neglected_first(run_id):
    _seed(run_id, [Beat(beat_id="b1", episode_start=1, episode_end=200, title="t", summary="s", thread_ids=["t_beat"])])
    repo.upsert_thread(run_id, Thread(thread_id="t_beat", description="beat", planted_episode=1, last_touched_episode=19))
    repo.upsert_thread(run_id, Thread(thread_id="t_old", description="neglected", planted_episode=1, last_touched_episode=1))
    for i in range(12):
        repo.upsert_thread(run_id, Thread(thread_id=f"t{i}", description=f"fill{i}", planted_episode=1, last_touched_episode=19))
    ctx = memory.assemble_context(run_id, 20)
    assert len(ctx.open_threads) == memory.MAX_OPEN_THREADS
    assert ctx.open_threads[0].thread_id == "t_beat"
    assert ctx.open_threads[1].thread_id == "t_old"
    assert [t.thread_id for t in ctx.neglected_threads] == ["t_old"]


def test_character_selection_keeps_protagonist_beat_and_recent_but_roster_has_all(run_id):
    _seed(run_id, [Beat(beat_id="b1", episode_start=1, episode_end=200, title="t", summary="s", character_ids=["c_beat"])])
    repo.upsert_character(run_id, Character(character_id="c_hero", name="Hero", role="protagonist"))
    repo.upsert_character(run_id, Character(character_id="c_beat", name="Beaty"))
    repo.upsert_character(run_id, Character(character_id="c_recent", name="Recent", last_seen_episode=45))
    repo.upsert_character(run_id, Character(character_id="c_gone", name="Gone", status="dead", last_seen_episode=3))
    ctx = memory.assemble_context(run_id, 50)
    assert {c.name for c in ctx.relevant_characters} == {"Hero", "Beaty", "Recent"}
    assert {c.name for c in ctx.roster} == {"Hero", "Beaty", "Recent", "Gone"}


def test_facts_core_always_and_tag_matched(run_id):
    _seed(run_id)
    repo.upsert_character(run_id, Character(character_id="c1", name="Kavi", role="protagonist"))
    repo.add_fact(run_id, Fact(statement="building has 7 floors", established_episode=1, tags=["core"]))
    repo.add_fact(run_id, Fact(statement="Kavi rides a red scooter", established_episode=2, tags=["kavi"]))
    repo.add_fact(run_id, Fact(statement="irrelevant", established_episode=3, tags=["someone else"]))
    ctx = memory.assemble_context(run_id, 5)
    assert [f.statement for f in ctx.facts] == ["building has 7 floors", "Kavi rides a red scooter"]


def test_directives_and_formatting(run_id):
    _seed(run_id)
    repo.add_directive(run_id, Directive(text="slow the romance", created_at_episode=1))
    text = format_context(memory.assemble_context(run_id, 1))
    assert "slow the romance" in text["directives"]
    assert text["history"] == "(nothing earlier than the episodes below)"


def test_missing_beat_raises(run_id):
    _seed(run_id, [Beat(beat_id="b1", episode_start=1, episode_end=5, title="t", summary="s")])
    with pytest.raises(ValueError):
        memory.assemble_context(run_id, 9)


def test_block_helpers():
    assert memory.block_no_for(1) == 1 and memory.block_no_for(10) == 1 and memory.block_no_for(11) == 2
    assert memory.block_range(3) == (21, 30)


def test_context_tells_the_writer_which_share_of_a_multi_episode_beat_this_episode_is(run_id):
    _seed(run_id, [Beat(beat_id="b1", episode_start=1, episode_end=8, title="Act", summary="s")])
    text = format_context(memory.assemble_context(run_id, 3))
    assert "episode 3 of 8 in this beat" in text["beat"]


def test_plan_facts_are_author_notes_and_page_facts_are_established_facts(run_id):
    _seed(run_id)
    repo.add_fact(run_id, Fact(statement="the Vein runs under the city", established_episode=0, tags=["core"]))
    repo.add_fact(run_id, Fact(statement="Kavi found a shoe in the ash", established_episode=4, tags=["core"]))
    ctx = memory.assemble_context(run_id, 6)
    assert [f.statement for f in ctx.author_notes] == ["the Vein runs under the city"]
    assert [f.statement for f in ctx.facts] == ["Kavi found a shoe in the ash"]
    text = format_context(ctx)
    assert "the Vein runs under the city" in text["author_notes"] and "the Vein" not in text["facts"]
