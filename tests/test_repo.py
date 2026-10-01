from src.models import (
    ArcPlan,
    Beat,
    BlockSummary,
    Character,
    Directive,
    Episode,
    FieldChange,
    LLMCallLog,
    StateDelta,
    Thread,
)
from src.storage import repo


def _episode(run_id: str, no: int, status: str = "approved", source: str = "generated") -> Episode:
    return Episode(episode_no=no, run_id=run_id, beat_id="b", text="w " * 10, summary=f"s{no}", status=status, source=source)


def test_arc_plan_round_trip_keeps_overview_and_beats(run_id):
    beat = Beat(episode_start=1, episode_end=5, title="Setup", summary="intro")
    repo.save_arc_plan(run_id, ArcPlan(run_id=run_id, premise="p", overview="three acts", beats=[beat]))
    loaded = repo.get_arc_plan(run_id)
    assert loaded.overview == "three acts"
    assert loaded.beat_for_episode(3).title == "Setup"
    assert loaded.beat_for_episode(6) is None


def test_character_alias_lookup_and_planned_arc(run_id):
    char = Character(name="Mr. Verma", aliases=["the old man"], planned_arc="denial to confession")
    repo.upsert_character(run_id, char)
    found = repo.find_character_by_name_or_alias(run_id, "The Old Man")
    assert found.character_id == char.character_id
    assert found.planned_arc == "denial to confession"
    assert repo.find_character_by_name_or_alias(run_id, "nobody") is None


def test_delete_character_thread_fact(run_id):
    char = Character(name="A")
    thread = Thread(description="t", planted_episode=1, last_touched_episode=1)
    repo.upsert_character(run_id, char)
    repo.upsert_thread(run_id, thread)
    repo.delete_character(run_id, char.character_id)
    repo.delete_thread(run_id, thread.thread_id)
    assert repo.get_characters(run_id) == []
    assert repo.get_thread(run_id, thread.thread_id) is None


def test_open_and_stale_threads(run_id):
    repo.upsert_thread(run_id, Thread(description="old", planted_episode=1, last_touched_episode=1, resolution_target_episode=50))
    repo.upsert_thread(run_id, Thread(description="done", status="resolved", planted_episode=1, last_touched_episode=2))
    assert [t.description for t in repo.get_open_threads(run_id)] == ["old"]
    assert len(repo.get_stale_threads(run_id, current_episode=20, staleness=15)) == 1
    assert repo.get_stale_threads(run_id, current_episode=10, staleness=15) == []


def test_directive_expiry_window(run_id):
    repo.add_directive(run_id, Directive(text="slow romance", created_at_episode=1, scope="next_n_episodes", expires_after_episode=5))
    assert len(repo.get_active_directives(run_id, 3)) == 1
    assert repo.get_active_directives(run_id, 10) == []


def test_episodes_before_only_returns_finalized_and_earlier(run_id):
    for no, status in [(1, "approved"), (2, "edited"), (3, "stale"), (4, "critiqued")]:
        repo.save_episode(run_id, _episode(run_id, no, status))
    assert [e.episode_no for e in repo.get_episodes_before(run_id, 5, 10)] == [1, 2]
    assert [e.episode_no for e in repo.get_episodes_before(run_id, 3, 1)] == [2]


def test_mark_stale_only_touches_episodes_from_the_given_one_and_is_idempotent(run_id):
    for no in (1, 2, 3):
        repo.save_episode(run_id, _episode(run_id, no))
    assert repo.mark_episodes_stale(run_id, 2) == [2, 3]
    assert repo.mark_episodes_stale(run_id, 2) == []
    assert repo.get_episode(run_id, 1).status == "approved"
    assert [e.episode_no for e in repo.get_episodes_range(run_id, 1)] == [1]
    assert [e.episode_no for e in repo.get_episodes_range(run_id, 1, finalized_only=False)] == [1, 2, 3]


def test_state_delta_round_trip_and_delete_from(run_id):
    change = FieldChange(entity_type="character", entity_id="c", field="status", old_value="alive", new_value="dead")
    repo.save_state_delta(run_id, StateDelta(run_id=run_id, episode_no=1, changes=[change]))
    repo.save_state_delta(run_id, StateDelta(run_id=run_id, episode_no=2, changes=[change]))
    assert repo.get_state_delta(run_id, 1).changes[0].new_value == "dead"
    repo.delete_state_deltas_from(run_id, 2)
    assert repo.get_state_delta(run_id, 2) is None
    assert repo.get_state_delta(run_id, 1) is not None


def test_block_summaries_filtered_by_end_episode(run_id):
    repo.save_block_summary(run_id, BlockSummary(block_no=1, episode_start=1, episode_end=10, summary="a"))
    repo.save_block_summary(run_id, BlockSummary(block_no=2, episode_start=11, episode_end=20, summary="b"))
    assert [b.block_no for b in repo.get_block_summaries(run_id, ending_before=15)] == [1]
    repo.save_block_summary(run_id, BlockSummary(block_no=1, episode_start=1, episode_end=10, summary="a2"))
    assert repo.get_block_summaries(run_id, ending_before=11)[0].summary == "a2"


def test_cost_queries(run_id):
    for stage, ep, cost in [("draft", 1, 0.5), ("critic", 1, 0.1), ("draft", 2, 0.4)]:
        repo.log_llm_call(
            run_id,
            LLMCallLog(run_id=run_id, episode_no=ep, stage=stage, provider="p", model="m", prompt_tokens=1, completion_tokens=1, cost_usd=cost, latency_ms=10),
        )
    assert repo.sum_episode_cost(run_id, 1) == 0.6
    assert repo.cost_by_stage(run_id)["draft"]["calls"] == 2
    assert repo.sum_run_cost(run_id)["calls"] == 3
