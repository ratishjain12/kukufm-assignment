import pytest

from src import cli
from src.storage import db, repo
from tests.harness import build_story


@pytest.fixture
def story(runs_dir, monkeypatch):
    s = build_story()
    monkeypatch.setattr(cli, "build_clients", lambda provider=None: s.clients)
    return s


def _run_id() -> str:
    return db.list_run_ids()[0]


def _new(extra=()):
    cli.main(["new", "a rider and the dead addresses", "--episodes", "20", "--auto-approve", *extra])


def test_full_flow_new_run_show_cost_edit_directives_list(story, capsys):
    _new()
    run_id = _run_id()
    assert repo.get_arc_plan(run_id).status == "approved"

    cli.main(["run", run_id, "--count", "3", "--auto-approve"])
    assert repo.get_run(run_id).current_episode == 4
    assert "Next episode: 4" in capsys.readouterr().out

    cli.main(["show", run_id])
    out = capsys.readouterr().out
    assert "PLAN VERSION 1 (approved)" in out and "Rafiq dies." in out
    cli.main(["show", run_id, "--episode", "2"])
    assert "EPISODE 2 [approved" in capsys.readouterr().out

    cli.main(["cost-report", run_id])
    assert "Projection to 20 episodes" in capsys.readouterr().out

    cli.main(["directives", run_id, "--add", "slow the romance", "--next", "3"])
    assert "slow the romance" in capsys.readouterr().out
    directive_id = repo.get_active_directives(run_id, 4)[0].directive_id
    cli.main(["directives", run_id, "--retire", directive_id])
    assert capsys.readouterr().out.strip() == ""

    cli.main(["list"])
    assert run_id in capsys.readouterr().out


def test_edit_episode_with_plot_change_invalidates_when_flag_given(story, tmp_path, capsys):
    _new()
    run_id = _run_id()
    cli.main(["run", run_id, "--count", "4", "--auto-approve"])
    edited = tmp_path / "ep2.txt"
    edited.write_text("Rafiq survives. EDITED-ALIVE")

    cli.main(["edit-episode", run_id, "2", "--file", str(edited), "--invalidate"])

    out = capsys.readouterr().out
    assert "character 'Rafiq': status dead -> alive" in out and "Stale: [3, 4]" in out
    assert repo.get_run(run_id).current_episode == 3


def test_edit_episode_prompts_with_recommended_default(story, tmp_path, monkeypatch, capsys):
    _new()
    run_id = _run_id()
    cli.main(["run", run_id, "--count", "3", "--auto-approve"])
    edited = tmp_path / "ep2.txt"
    edited.write_text("Rafiq survives. EDITED-ALIVE")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    cli.main(["edit-episode", run_id, "2", "--file", str(edited)])
    assert repo.get_episode(run_id, 3).status == "stale"


def test_run_refuses_unapproved_plan(story):
    cli.main(["new", "premise", "--episodes", "20", "--auto-approve"])
    run_id = _run_id()
    plan = repo.get_arc_plan(run_id)
    plan.status = "draft"
    repo.save_arc_plan(run_id, plan)
    with pytest.raises(SystemExit, match="not approved"):
        cli.main(["run", run_id, "--auto-approve"])


def test_unknown_run_id_is_rejected_without_creating_a_database(runs_dir):
    with pytest.raises(SystemExit, match="No such run"):
        cli.main(["show", "run_typo"])
    assert not (runs_dir / "run_typo").exists()


def test_missing_credentials_give_a_friendly_error(runs_dir, monkeypatch):
    def boom(provider=None):
        raise KeyError("ANTHROPIC_API_KEY")

    monkeypatch.setattr(cli, "build_clients", boom)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main(["new", "premise", "--auto-approve"])


def test_llm_failures_exit_nonzero_with_resume_hint(story, capsys):
    _new()
    run_id = _run_id()

    def fail(*args, **kwargs):
        raise cli.LLMError("provider down", retryable=False)

    story.writer.generate = fail
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", run_id, "--count", "1", "--auto-approve"])
    assert exc.value.code == 1
    assert "Progress so far is saved" in capsys.readouterr().err


def test_rebuild_state_command_reports_the_episodes_it_rederived(story, capsys):
    _new()
    run_id = _run_id()
    cli.main(["run", run_id, "--count", "2", "--auto-approve"])
    capsys.readouterr()
    cli.main(["rebuild-state", run_id])
    assert "Re-derived world state from 2 episode(s): [1, 2]" in capsys.readouterr().out


def test_export_command_writes_the_demo_folder(story, tmp_path, capsys):
    _new()
    run_id = _run_id()
    cli.main(["run", run_id, "--count", "2", "--auto-approve"])
    capsys.readouterr()
    cli.main(["export", run_id, "--out", str(tmp_path / "demo")])
    out = capsys.readouterr().out
    assert "episode_001.md" in out and "human_decisions.md" in out
    assert (tmp_path / "demo" / "story.md").exists()


def test_edit_episode_confirmation_survives_end_of_input_and_takes_the_recommended_default(story, tmp_path, monkeypatch):
    _new()
    run_id = _run_id()
    cli.main(["run", run_id, "--count", "3", "--auto-approve"])
    edited = tmp_path / "ep2.txt"
    edited.write_text("Rafiq survives. EDITED-ALIVE")

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    cli.main(["edit-episode", run_id, "2", "--file", str(edited)])
    assert repo.get_episode(run_id, 3).status == "stale"


def test_every_command_that_takes_a_run_id_explains_it(capsys):
    for command in ("run", "resume", "show", "export", "directives", "edit-episode", "rebuild-state", "cost-report", "review-plan"):
        with pytest.raises(SystemExit):
            cli.main([command, "--help"])
    assert capsys.readouterr().out.count("see the `list` command") == 9
