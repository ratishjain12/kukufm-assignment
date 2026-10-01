import pytest

from src.core import planner
from src.core.planner import PlanLockedError
from src.models import Episode
from src.storage import repo
from tests.factories import make_draft
from tests.fakes import ScriptedClient


def test_generate_seeds_plan_characters_threads_facts(run_id):
    plan = planner.generate_arc_plan(run_id, ScriptedClient([make_draft(20)]), total_episodes=20)

    assert plan.status == "draft" and plan.version == 1
    assert plan.beats[0].episode_start == 1 and plan.beats[-1].episode_end == 20
    names = {c.name: c for c in repo.get_characters(run_id)}
    assert set(names) == {"Kavi", "Mr. Verma"}
    assert names["Kavi"].relationships == {names["Mr. Verma"].character_id: "reluctant ally"}
    assert plan.beats[0].character_ids == [names["Kavi"].character_id, names["Mr. Verma"].character_id]

    threads = repo.get_threads(run_id)
    assert {t.status for t in threads} == {"planned"}
    assert plan.beats[0].thread_ids == [next(t.thread_id for t in threads if t.description == "who owns the ledger")]
    assert [f.tags for f in repo.get_facts(run_id)] == [["core", "building"]]
    assert repo.get_arc_plan(run_id).overview == "three acts"


def test_gap_in_coverage_triggers_one_repair_with_problems_fed_back(run_id):
    broken = make_draft(20)
    broken.beats = broken.beats[:-1]
    client = ScriptedClient([broken, make_draft(20)])
    plan = planner.generate_arc_plan(run_id, client, total_episodes=20)
    assert plan.beats[-1].episode_end == 20
    assert "must end exactly at episode 20" in client.calls[1][1]


def test_a_beat_referencing_an_undefined_key_loses_that_link_instead_of_failing_the_plan(run_id):
    draft = make_draft(20)
    draft.beats[0].thread_keys = ["nope", "ledger"]
    draft.beats[0].character_keys = ["ghost", "kavi"]
    plan = planner.generate_arc_plan(run_id, ScriptedClient([draft]), total_episodes=20)
    assert len(plan.beats[0].thread_ids) == 1 and len(plan.beats[0].character_ids) == 1


def test_revise_replaces_world_state_bumps_version_and_sends_feedback(run_id):
    planner.generate_arc_plan(run_id, ScriptedClient([make_draft(20)]), total_episodes=20)
    revised = make_draft(20)
    revised.characters = [c for c in revised.characters if c.key != "verma"]
    revised.characters[0].relationships = []
    for beat in revised.beats:
        beat.character_keys = ["kavi"]
    client = ScriptedClient([revised])
    plan = planner.revise_arc_plan(run_id, client, "remove Verma")

    assert plan.version == 2
    assert [c.name for c in repo.get_characters(run_id)] == ["Kavi"]
    assert "remove Verma" in client.calls[0][1]
    assert "Mr. Verma" in client.calls[0][1]


def test_plan_locked_once_episodes_exist(run_id):
    planner.generate_arc_plan(run_id, ScriptedClient([make_draft(20)]), total_episodes=20)
    repo.save_episode(run_id, Episode(episode_no=1, run_id=run_id, beat_id="b", text="x"))
    with pytest.raises(PlanLockedError):
        planner.revise_arc_plan(run_id, ScriptedClient([]), "anything")


def test_approve_sets_status(run_id):
    planner.generate_arc_plan(run_id, ScriptedClient([make_draft(20)]), total_episodes=20)
    assert planner.approve_arc_plan(run_id).status == "approved"
    assert repo.get_arc_plan(run_id).status == "approved"


def test_schema_sent_to_model_forbids_extra_properties(run_id):
    client = ScriptedClient([make_draft(20)])
    planner.generate_arc_plan(run_id, client, total_episodes=20)
    assert client.calls[0][2]["additionalProperties"] is False


def test_plan_schema_requires_the_fields_that_carry_plan_content():
    """Optional fields get skipped by constrained decoding; these feed character cards, beat links and core facts."""
    defs = planner.PlanDraft.model_json_schema()["$defs"]
    assert {"traits", "relationships", "planned_arc"} <= set(defs["PlannedCharacter"]["required"])
    assert {"character_keys", "thread_keys"} <= set(defs["PlannedBeat"]["required"])
    assert "tags" in defs["PlannedFact"]["required"]
    assert "aliases" not in defs["PlannedCharacter"]["required"]


def test_a_structurally_broken_plan_gets_three_attempts_before_failing(run_id):
    broken = make_draft(20)
    broken.beats = broken.beats[:-1]
    client = ScriptedClient([broken, broken, make_draft(20)])
    assert planner.generate_arc_plan(run_id, client, total_episodes=20).beats[-1].episode_end == 20
    assert len(client.calls) == 3
