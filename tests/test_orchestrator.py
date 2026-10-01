import json

import pytest

from src import config
from src.core import orchestrator
from src.core.critic import CriticVerdict, Issue
from src.core.orchestrator import PlanDecision, ReviewDecision
from src.storage import repo
from tests.factories import make_draft
from tests.harness import ScriptedGate, approve, build_story, seed_plan


def _failing(n: int) -> CriticVerdict:
    return CriticVerdict(issues=[Issue(category="contradiction", severity="blocker", description="bad", conflicts_with="The building has seven floors")], hook_strong=True, hook_note="")


def _rafiq(run_id: str):
    return repo.find_character_by_name_or_alias(run_id, "Rafiq")


def _write(run_id, story, gate, count):
    return orchestrator.run_episodes(run_id, story.clients, gate, count=count)


def test_approved_episodes_advance_progress_and_update_world_state(run_id):
    story = build_story()
    seed_plan(run_id, story)
    written, outcome = _write(run_id, story, ScriptedGate(), 3)

    assert outcome == "written" and [e.episode_no for e in written] == [1, 2, 3]
    assert repo.get_run(run_id).current_episode == 4
    assert _rafiq(run_id).status == "dead"
    assert repo.find_character_by_name_or_alias(run_id, "Mira") is not None
    assert repo.get_episode(run_id, 2).summary == "Rafiq dies."
    assert repo.get_state_delta(run_id, 2) is not None
    assert repo.cost_by_stage(run_id)["draft"]["calls"] == 3


def test_feedback_becomes_directive_that_steers_next_episode_prompt(run_id):
    story = build_story()
    seed_plan(run_id, story)
    gate = ScriptedGate([approve(feedback="slow down the romance", feedback_scope="next_n_episodes", feedback_episodes=2)])
    _write(run_id, story, gate, 3)

    directive = repo.get_active_directives(run_id, 2)[0]
    assert directive.text == "slow down the romance" and directive.expires_after_episode == 3
    assert "slow down the romance" not in story.writer.calls[0][1].split("HUMAN STEERING DIRECTIVES")[1].split("CHARACTERS IN PLAY")[0]
    assert "- slow down the romance" in story.writer.calls[1][1]
    assert "- slow down the romance" in story.writer.calls[2][1]
    assert repo.get_active_directives(run_id, 4) == []


def test_reject_with_feedback_regenerates_same_episode_steered_by_new_directive(run_id):
    story = build_story()
    seed_plan(run_id, story)
    gate = ScriptedGate([ReviewDecision(action="reject", reason="too slow", feedback="kill off Rafiq soon")])
    _write(run_id, story, gate, 1)

    assert len(story.writer.calls) == 2
    retry_prompt = story.writer.calls[1][1]
    assert "REVISION REQUIRED" in retry_prompt and "too slow" in retry_prompt
    assert "- kill off Rafiq soon" in retry_prompt
    assert repo.get_episode(run_id, 1).status == "approved"


def test_human_edit_becomes_canonical_and_state_is_extracted_from_it(run_id):
    story = build_story()
    seed_plan(run_id, story)
    gate = ScriptedGate([ReviewDecision(action="edit", edited_text="Rafiq waves. EDITED-ALIVE")])
    _write(run_id, story, gate, 1)

    episode = repo.get_episode(run_id, 1)
    assert (episode.status, episode.source, episode.text) == ("edited", "human_edited", "Rafiq waves. EDITED-ALIVE")
    assert _rafiq(run_id).status == "alive"








def test_quit_keeps_draft_and_resume_reuses_it_without_rewriting(run_id):
    story = build_story()
    seed_plan(run_id, story)
    _, outcome = _write(run_id, story, ScriptedGate([ReviewDecision(action="quit")]), 1)
    assert outcome == "quit" and repo.get_episode(run_id, 1).status == "critiqued"
    drafted_text = repo.get_episode(run_id, 1).text

    gate = ScriptedGate()
    _write(run_id, story, gate, 1)
    assert len(story.writer.calls) == 1
    assert gate.requests[0].episode.text == drafted_text
    assert repo.get_run(run_id).current_episode == 2


def test_crash_between_approval_and_extraction_is_recovered_without_regenerating(run_id):
    story = build_story()
    seed_plan(run_id, story)
    _write(run_id, story, ScriptedGate(), 1)
    repo.delete_state_deltas_from(run_id, 1)
    repo.update_run_progress(run_id, 1)

    gate = ScriptedGate()
    outcome, _ = orchestrator.write_next_episode(run_id, story.clients, gate)
    assert outcome == "written" and gate.requests == []
    assert len(story.writer.calls) == 1
    assert repo.get_state_delta(run_id, 1) is not None and repo.get_run(run_id).current_episode == 2


def test_run_stops_at_end_of_plan_and_on_run_budget(run_id, monkeypatch):
    story = build_story()
    seed_plan(run_id, story)
    repo.update_run_progress(run_id, 20)
    written, outcome = _write(run_id, story, ScriptedGate(), 5)
    assert [e.episode_no for e in written] == [20] and outcome == "complete"

    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 0.000001)
    monkeypatch.setattr(orchestrator.repo, "sum_run_cost", lambda _: {"total_cost": 1.0})
    assert _write(run_id, story, ScriptedGate(), 5) == ([], "budget")


def test_block_summary_created_at_episode_10_and_fed_to_episode_11(run_id):
    story = build_story()
    seed_plan(run_id, story)
    _write(run_id, story, ScriptedGate(), 11)
    assert repo.get_block_summaries(run_id, 11)[0].summary == "BLOCK-RECAP"
    assert "Episodes 1-10: BLOCK-RECAP" in story.writer.calls[-1][1]


def test_plan_review_edit_then_approve_and_quit(run_id):
    story = build_story()
    seed_plan(run_id, story, approve=False)
    revised = make_draft(20)
    revised.overview = "revised overview"
    story.clients.planner.__dict__["_replies"].append(revised)

    gate = ScriptedGate(plan_decisions=[PlanDecision(action="edit", feedback="more heist"), PlanDecision(action="approve")])
    assert orchestrator.review_plan(run_id, story.clients, gate) is True
    plan = repo.get_arc_plan(run_id)
    assert (plan.version, plan.status, plan.overview) == (2, "approved", "revised overview")
    assert orchestrator.review_plan(run_id, story.clients, ScriptedGate(plan_decisions=[PlanDecision(action="quit")])) is False


def test_plan_reject_regenerates_fresh(run_id):
    story = build_story()
    seed_plan(run_id, story, approve=False)
    story.clients.planner.__dict__["_replies"].append(make_draft(20))
    gate = ScriptedGate(plan_decisions=[PlanDecision(action="reject"), PlanDecision(action="approve")])
    orchestrator.review_plan(run_id, story.clients, gate)
    assert repo.get_arc_plan(run_id).version == 2


def test_add_directive_validates_window(run_id):
    with pytest.raises(ValueError):
        orchestrator.add_directive(run_id, 1, "x", scope="next_n_episodes", episodes=None)




def test_run_refuses_to_continue_when_finalized_episodes_have_no_recorded_state(run_id):
    story = build_story()
    seed_plan(run_id, story)
    _write(run_id, story, ScriptedGate(), 2)
    repo.delete_state_deltas_from(run_id, 1)

    assert orchestrator.find_state_gaps(run_id) == [1, 2]
    with pytest.raises(orchestrator.StateIntegrityError, match="rebuild-state"):
        _write(run_id, story, ScriptedGate(), 1)

    orchestrator.rebuild_state(run_id, story.clients)
    assert orchestrator.find_state_gaps(run_id) == []
    assert len(_write(run_id, story, ScriptedGate(), 1)[0]) == 1


def test_review_request_carries_beat_position_and_the_directives_in_force(run_id):
    story = build_story()
    seed_plan(run_id, story)
    gate = ScriptedGate([approve(feedback="End on dialogue")])
    _write(run_id, story, gate, 2)

    first, second = gate.requests
    assert first.beat == "Beat 1 (episode 1 of 5 in this beat)" and first.directives == []
    assert second.beat == "Beat 1 (episode 2 of 5 in this beat)" and second.directives == ["End on dialogue"]


TOO_LONG = lambda episode, attempt: 800


def test_overlong_drafts_trigger_bounded_rewrites_then_escalate_to_the_human(run_id):
    story = build_story(word_counts=TOO_LONG)
    seed_plan(run_id, story)
    gate = ScriptedGate()
    _write(run_id, story, gate, 1)

    assert len(story.writer.calls) == 1 + config.MAX_REVISIONS
    request = gate.requests[0]
    assert "after 2 rewrite(s)" in request.escalation
    assert any("Episode is 800 words" in n for n in request.critic_notes)
    assert repo.get_episode(run_id, 1).revision_count == config.MAX_REVISIONS


def test_one_rewrite_fixes_the_length_and_passes(run_id):
    story = build_story(word_counts=lambda episode, attempt: 800 if attempt == 1 else 500)
    seed_plan(run_id, story)
    gate = ScriptedGate()
    _write(run_id, story, gate, 1)

    assert len(story.writer.calls) == 2 and gate.requests[0].escalation is None
    assert "REVISION REQUIRED" in story.writer.calls[1][1] and "cut roughly" in story.writer.calls[1][1]


def test_judge_advice_alone_never_causes_a_rewrite(run_id):
    advice = CriticVerdict(issues=[Issue(category="continuity", severity="blocker", description="x", conflicts_with="The building has seven floors")], hook_strong=False, hook_note="same device")
    story = build_story(critic_verdict=lambda n: advice)
    seed_plan(run_id, story)
    gate = ScriptedGate()
    _write(run_id, story, gate, 1)
    assert len(story.writer.calls) == 1 and gate.requests[0].escalation is None
    assert any("(advice)" in n for n in gate.requests[0].critic_notes)


def test_cost_cap_stops_rewrites_early(run_id, tmp_path, monkeypatch):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"fake/fake-model": {"input_per_mtok": 1_000_000.0, "output_per_mtok": 0.0}}))
    monkeypatch.setattr(config, "PRICING_FILE", pricing)
    monkeypatch.setattr(config, "MAX_EPISODE_COST_USD", 50.0)
    story = build_story(word_counts=TOO_LONG)
    seed_plan(run_id, story)
    gate = ScriptedGate()
    _write(run_id, story, gate, 1)

    assert len(story.writer.calls) == 1
    assert "cost cap" in gate.requests[0].escalation


def test_escalation_ships_the_best_draft_not_the_last(run_id):
    def verdict_for_call(n: int) -> CriticVerdict:
        calls = len(story.critic.calls)
        findings = [Issue(category="pacing", severity="minor", description="x")] * {1: 3, 2: 1, 3: 2}[calls]
        return CriticVerdict(issues=findings, hook_strong=True, hook_note="")

    story = build_story(critic_verdict=verdict_for_call, word_counts=TOO_LONG)
    seed_plan(run_id, story)
    gate = ScriptedGate()
    _write(run_id, story, gate, 1)

    assert len(story.writer.calls) == 3
    assert repo.get_episode(run_id, 1).text.startswith("e1v2_")  # same blocker everywhere; the draft with the least advice wins
    assert gate.requests[0].escalation
