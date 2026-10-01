import pytest

from src.demo import DemoScript, DemoStopped, DirectiveStep, run_demo
from src.storage import db, repo
from tests.factories import make_draft
from tests.harness import build_story

SCRIPT = DemoScript(
    premise="a rider and the dead addresses",
    plan_length=20,
    plan_feedback="more ghosts",
    reject_at={2: "too slow"},
    directives=[
        DirectiveStep(episode=3, text="End on dialogue"),
        DirectiveStep(episode=4, text="Kill off {supporting}", scope="next_n_episodes", episodes=3),
    ],
    session_one_episodes=4,
    total_episodes=6,
)


def _story_with_two_plans():
    story = build_story()
    story.clients.planner.__dict__["_replies"].append(make_draft(20))
    story.clients.planner.__dict__["_replies"].insert(0, make_draft(20))
    return story


def test_demo_runs_every_scripted_intervention_and_exports_the_deliverables(runs_dir, tmp_path, capsys):
    story = _story_with_two_plans()
    sessions: list[int] = []

    def make_clients():
        sessions.append(1)
        return story.clients

    result = run_demo(SCRIPT, tmp_path / "demo", make_clients=make_clients)

    run_id = result.run_id
    assert len(sessions) == 2 and result.episodes_written == 6
    assert repo.require_arc_plan(run_id).version == 2 and repo.require_arc_plan(run_id).status == "approved"
    assert [e.episode_no for e in repo.get_episodes_range(run_id, 1)] == [1, 2, 3, 4, 5, 6]

    directives = repo.get_directives(run_id)
    assert [(d.created_at_episode, d.scope) for d in directives] == [(3, "permanent"), (4, "next_n_episodes")]
    assert directives[1].text == "Kill off Mr. Verma" and directives[1].expires_after_episode == 7

    names = {p.name for p in result.exported}
    assert {"arc_plan.txt", "arc_plan_v1.txt", "story.md", "human_decisions.md", "cost_report.txt", "trace_summary.log", "trace_full.jsonl"} <= names
    assert sum(1 for n in names if n.startswith("episode_")) == 6

    decisions = (tmp_path / "demo" / "human_decisions.md").read_text()
    assert "gate decision=reject" in decisions and "plan gate v1 decision=edit" in decisions
    assert "End on dialogue" in decisions and "Effect of each directive" in decisions and "← directive given here" in decisions
    assert "Rafiq alive → dead" in decisions

    printed = capsys.readouterr().out
    assert "phase 1/4" in printed and "phase 3/4" in printed and "[reviewer] episode 2: REJECT: too slow" in printed


def test_second_session_resumes_from_disk_without_redoing_work(runs_dir, tmp_path):
    story = _story_with_two_plans()
    result = run_demo(SCRIPT, tmp_path / "demo", make_clients=lambda: story.clients)
    drafted = [c for c in story.writer.calls if "WRITE EPISODE 1." in c[1]]
    assert len(drafted) == 1 and result.episodes_written == 6  # episodes finished in session 1 are never rewritten in session 2


def test_demo_stops_for_a_human_when_a_draft_cannot_be_fixed(runs_dir, tmp_path):
    story = build_story(word_counts=lambda episode, attempt: 800)
    story.clients.planner.__dict__["_replies"].append(make_draft(20))
    with pytest.raises(DemoStopped, match="demo --resume"):
        run_demo(SCRIPT, tmp_path / "demo", make_clients=lambda: story.clients)


def test_a_short_demo_never_writes_more_episodes_than_requested(runs_dir, tmp_path):
    story = _story_with_two_plans()
    result = run_demo(SCRIPT.model_copy(update={"total_episodes": 2}), tmp_path / "demo", make_clients=lambda: story.clients)
    assert result.episodes_written == 2


def test_a_stopped_demo_resumes_the_same_run_without_repeating_the_plan_edit_or_rejection(runs_dir, tmp_path):
    story = build_story(word_counts=lambda episode, attempt: 800 if episode == 4 else 450)
    story.clients.planner.__dict__["_replies"].append(make_draft(20))
    story.clients.planner.__dict__["_replies"].insert(0, make_draft(20))
    with pytest.raises(DemoStopped, match="demo --resume") as stopped:
        run_demo(SCRIPT, tmp_path / "demo", make_clients=lambda: story.clients)
    run_id = db.list_run_ids()[0]
    assert repo.require_run(run_id).current_episode == 4 and run_id in str(stopped.value)

    healed = build_story()
    result = run_demo(SCRIPT, tmp_path / "demo", make_clients=lambda: healed.clients, resume_run_id=run_id)

    assert result.run_id == run_id and result.episodes_written == 6
    assert len(db.list_run_ids()) == 1 and repo.require_arc_plan(run_id).version == 2
    assert not [c for c in healed.writer.calls if "WRITE EPISODE 1." in c[1] or "WRITE EPISODE 2." in c[1]]
    assert len(healed.clients.planner.calls) == 0
