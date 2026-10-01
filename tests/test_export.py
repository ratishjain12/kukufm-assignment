from src.core import orchestrator, planner
from src.core.orchestrator import ReviewDecision
from src.export import export_run
from src.logging_config import configure_logging
from src.storage import repo
from tests.factories import make_draft
from tests.fakes import ScriptedClient
from tests.harness import ScriptedGate, approve, build_story, seed_plan


def _run_story(run_id: str, episodes: int = 3, gate: ScriptedGate | None = None):
    configure_logging(run_id)
    story = build_story()
    seed_plan(run_id, story)
    orchestrator.run_episodes(run_id, story.clients, gate or ScriptedGate(), count=episodes)
    return story


def test_export_writes_consistently_named_files(run_id, tmp_path):
    _run_story(run_id)
    out = tmp_path / "demo"
    written = export_run(run_id, out)

    names = sorted(str(p.relative_to(out)) for p in written)
    assert names == [
        "cost_report.txt",
        "episodes/episode_001.md",
        "episodes/episode_002.md",
        "episodes/episode_003.md",
        "human_decisions.md",
        "plan/arc_plan.txt",
        "story.md",
        "trace_full.jsonl",
        "trace_summary.log",
    ]
    episode = (out / "episodes" / "episode_002.md").read_text()
    assert episode.startswith("# Episode 2\n") and "**Summary:** Rafiq dies." in episode and "approved" in episode
    story = (out / "story.md").read_text()
    assert story.startswith("# test premise") and story.count("## Episode") == 3
    assert "Run " in (out / "cost_report.txt").read_text()
    assert (out / "trace_full.jsonl").read_text().count("llm call detail") > 0
    assert "finalized" in (out / "trace_summary.log").read_text()
    assert "llm call detail" not in (out / "trace_summary.log").read_text()


def test_human_decisions_capture_feedback_directives_and_edits(run_id, tmp_path):
    gate = ScriptedGate([approve(feedback="slow down the romance", feedback_scope="next_n_episodes", feedback_episodes=5), ReviewDecision(action="edit", edited_text="My own words. " * 40)])
    _run_story(run_id, episodes=2, gate=gate)
    out = tmp_path / "demo"
    export_run(run_id, out)

    text = (out / "human_decisions.md").read_text()
    assert "slow down the romance" in text and "through episode 6" in text
    assert "Effect of each directive" in text and "← directive given here" in text
    assert "Episode 2: edited by hand" in text
    assert "gate decision=approve" in text and "gate decision=edit" in text and "directive added" in text


def test_plan_history_is_kept_and_the_final_plan_is_the_approved_version_not_live_state(run_id, tmp_path):
    configure_logging(run_id)
    story = build_story()
    seed_plan(run_id, story, approve=False)
    revised = make_draft(20)
    revised.overview = "revised overview"
    planner.revise_arc_plan(run_id, ScriptedClient([revised]), "more heist")
    planner.approve_arc_plan(run_id)
    orchestrator.run_episodes(run_id, story.clients, ScriptedGate(), count=2)

    out = tmp_path / "demo"
    export_run(run_id, out)

    assert sorted(p.name for p in (out / "plan").iterdir()) == ["arc_plan.txt", "arc_plan_v1.txt"]
    assert "revised overview" in (out / "plan" / "arc_plan.txt").read_text()
    assert "three acts" in (out / "plan" / "arc_plan_v1.txt").read_text()
    assert "Rafiq" not in (out / "plan" / "arc_plan.txt").read_text()  # characters created while writing are not part of the plan


def test_reexport_replaces_stale_exports_but_never_touches_other_files(run_id, tmp_path):
    _run_story(run_id, episodes=2)
    out = tmp_path / "demo"
    (out / "episodes").mkdir(parents=True)
    (out / "episodes" / "episode_099.md").write_text("stale")
    (out / "plan").mkdir()
    (out / "plan" / "arc_plan_v7.txt").write_text("stale")
    (out / "README.md").write_text("hand-written, keep me")

    export_run(run_id, out)

    assert not (out / "episodes" / "episode_099.md").exists()
    assert not (out / "plan" / "arc_plan_v7.txt").exists()
    assert (out / "README.md").read_text() == "hand-written, keep me"


def test_export_handles_a_run_with_no_trace_log(run_id, tmp_path):
    story = build_story()
    seed_plan(run_id, story)
    out = tmp_path / "demo"
    export_run(run_id, out)
    assert (out / "trace_summary.log").read_text() == "no trace log found\n"
    assert "no trace log found" in (out / "human_decisions.md").read_text()


def test_plan_snapshots_exist_for_every_version(run_id):
    story = build_story()
    seed_plan(run_id, story, approve=False)
    planner.revise_arc_plan(run_id, ScriptedClient([make_draft(20)]), "again")
    assert sorted(repo.get_plan_snapshots(run_id)) == [1, 2]
