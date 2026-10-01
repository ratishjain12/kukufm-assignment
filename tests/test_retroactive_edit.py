import pytest

from src.core import orchestrator
from src.storage import repo
from tests.harness import ScriptedGate, build_story, seed_plan


def _written_story(run_id: str, episodes: int = 4):
    story = build_story()
    seed_plan(run_id, story)
    orchestrator.run_episodes(run_id, story.clients, ScriptedGate(), count=episodes)
    return story


def _rafiq_status(run_id: str) -> str:
    return repo.find_character_by_name_or_alias(run_id, "Rafiq").status


def _summarizer_calls(story) -> int:
    return sum(1 for _, _, schema in story.extractor.calls if schema is None)


def test_cosmetic_edit_costs_one_call_and_invalidates_nothing(run_id):
    story = _written_story(run_id)
    calls_before = len(story.extractor.calls)

    preview = orchestrator.preview_edit(run_id, story.clients, 2, "Rafiq dies, but better written.")
    assert preview.hard_differences == [] and preview.later_episodes == [3, 4]
    impact = orchestrator.commit_edit(run_id, story.clients, preview, "Rafiq dies, but better written.", invalidate_later=False)

    assert impact.stale_episodes == [] and not impact.accepted_inconsistency
    assert len(story.extractor.calls) - calls_before == 1
    episode = repo.get_episode(run_id, 2)
    assert (episode.status, episode.source, episode.text) == ("edited", "human_edited", "Rafiq dies, but better written.")
    assert _rafiq_status(run_id) == "dead" and repo.find_character_by_name_or_alias(run_id, "Mira")
    assert repo.get_episode(run_id, 3).status == "approved"
    assert repo.get_run(run_id).current_episode == 5


def test_plot_changing_edit_rolls_back_state_marks_later_episodes_stale_and_rewinds_run(run_id):
    story = _written_story(run_id)
    text = "Rafiq survives. EDITED-ALIVE"

    preview = orchestrator.preview_edit(run_id, story.clients, 2, text)
    assert preview.hard_differences == ["character 'Rafiq': status dead -> alive"]
    impact = orchestrator.commit_edit(run_id, story.clients, preview, text, invalidate_later=True)

    assert impact.stale_episodes == [3, 4]
    assert _rafiq_status(run_id) == "alive"
    assert repo.find_character_by_name_or_alias(run_id, "Mira") is None
    assert repo.get_state_delta(run_id, 3) is None and repo.get_state_delta(run_id, 4) is None
    assert repo.get_state_delta(run_id, 2).extraction["summary"] == "Rafiq lives."
    assert repo.get_run(run_id).current_episode == 3
    assert [repo.get_episode(run_id, n).status for n in (1, 2, 3, 4)] == ["approved", "edited", "stale", "stale"]


def test_regeneration_after_edit_sees_edited_episode_not_stale_future(run_id):
    story = _written_story(run_id)
    text = "Rafiq survives. EDITED-ALIVE"
    preview = orchestrator.preview_edit(run_id, story.clients, 2, text)
    orchestrator.commit_edit(run_id, story.clients, preview, text, invalidate_later=True)
    writer_calls_before = len(story.writer.calls)

    written, _ = orchestrator.run_episodes(run_id, story.clients, ScriptedGate(), count=2)

    assert [e.episode_no for e in written] == [3, 4]
    prompt_for_3 = story.writer.calls[writer_calls_before][1]
    assert "EDITED-ALIVE" in prompt_for_3 and "--- Episode 4 ---" not in prompt_for_3
    assert "Rafiq: alive" in prompt_for_3
    assert repo.get_episode(run_id, 3).status == "approved" and repo.get_episode(run_id, 3).text.startswith("e3v2_")
    assert repo.find_character_by_name_or_alias(run_id, "Mira") is not None
    assert repo.get_run(run_id).current_episode == 5


def test_keeping_later_episodes_despite_plot_change_is_recorded_as_accepted_inconsistency(run_id):
    story = _written_story(run_id)
    text = "Rafiq survives. EDITED-ALIVE"
    preview = orchestrator.preview_edit(run_id, story.clients, 2, text)
    impact = orchestrator.commit_edit(run_id, story.clients, preview, text, invalidate_later=False)

    assert impact.accepted_inconsistency and impact.stale_episodes == []
    assert _rafiq_status(run_id) == "dead"
    assert repo.get_episode(run_id, 3).status == "approved"
    assert repo.get_run(run_id).current_episode == 5


def test_editing_the_latest_episode_replaces_its_state_without_staling_anything(run_id):
    story = _written_story(run_id, episodes=2)
    text = "Rafiq survives. EDITED-ALIVE"
    preview = orchestrator.preview_edit(run_id, story.clients, 2, text)
    assert preview.later_episodes == []
    impact = orchestrator.commit_edit(run_id, story.clients, preview, text, invalidate_later=False)

    assert impact.stale_episodes == [] and _rafiq_status(run_id) == "alive"
    assert repo.get_run(run_id).current_episode == 3


def test_only_finalized_episodes_can_be_edited(run_id):
    story = _written_story(run_id, episodes=2)
    with pytest.raises(ValueError):
        orchestrator.preview_edit(run_id, story.clients, 9, "x")


def test_cosmetic_edit_refreshes_the_containing_block_summary(run_id):
    story = _written_story(run_id, episodes=10)
    assert _summarizer_calls(story) == 1
    preview = orchestrator.preview_edit(run_id, story.clients, 5, "Episode five, polished.")
    orchestrator.commit_edit(run_id, story.clients, preview, "Episode five, polished.", invalidate_later=False)
    assert _summarizer_calls(story) == 2


def test_plot_edit_inside_a_block_drops_that_blocks_stale_summary(run_id):
    story = _written_story(run_id, episodes=10)
    assert len(repo.get_block_summaries(run_id, 11)) == 1
    text = "Rafiq survives. EDITED-ALIVE"
    preview = orchestrator.preview_edit(run_id, story.clients, 2, text)
    orchestrator.commit_edit(run_id, story.clients, preview, text, invalidate_later=True)

    assert repo.get_block_summaries(run_id, 11) == []
    assert repo.get_run(run_id).current_episode == 3


def test_rebuild_state_rederives_world_state_from_stored_texts(run_id):
    from tests.factories import ext_char, make_extraction

    story = _written_story(run_id, episodes=3)
    assert _rafiq_status(run_id) == "dead"

    story.extractions[2] = make_extraction(summary="Rafiq survives after all.", characters=[ext_char("Rafiq", "alive")])
    rebuilt = orchestrator.rebuild_state(run_id, story.clients)

    assert rebuilt == [1, 2, 3]
    assert _rafiq_status(run_id) == "alive"
    assert repo.get_episode(run_id, 2).summary == "Rafiq survives after all."
    assert repo.get_state_delta(run_id, 2).extraction["summary"] == "Rafiq survives after all."
    assert repo.get_run(run_id).current_episode == 4


def test_rebuild_from_a_later_episode_leaves_earlier_state_alone(run_id):
    story = _written_story(run_id, episodes=3)
    calls_before = len(story.extractor.calls)
    assert orchestrator.rebuild_state(run_id, story.clients, from_episode=3) == [3]
    assert len(story.extractor.calls) - calls_before == 1
    assert _rafiq_status(run_id) == "dead"
