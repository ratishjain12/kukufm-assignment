from src.core.orchestrator import ReviewRequest
from src.gates import AutoGate, ConsoleGate
from src.models import Episode


def _request(text: str = "hello world " * 40) -> ReviewRequest:
    return ReviewRequest(episode=Episode(episode_no=3, run_id="r", beat_id="b", text=text), critic_notes=["[pacing] slow"])


def _keys(monkeypatch, *answers: str) -> None:
    queue = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(queue))


def test_approve(monkeypatch, capsys):
    _keys(monkeypatch, "a")
    assert ConsoleGate().review_episode(_request()).action == "approve"
    out = capsys.readouterr().out
    assert "EPISODE 3" in out and "critic: [pacing] slow" in out


def test_invalid_key_reprompts(monkeypatch):
    _keys(monkeypatch, "x", "", "a")
    assert ConsoleGate().review_episode(_request()).action == "approve"


def test_approve_with_scoped_feedback(monkeypatch):
    _keys(monkeypatch, "f", "slow down the romance", "5")
    decision = ConsoleGate().review_episode(_request())
    assert (decision.action, decision.feedback, decision.feedback_scope, decision.feedback_episodes) == ("approve", "slow down the romance", "next_n_episodes", 5)


def test_approve_with_permanent_feedback(monkeypatch):
    _keys(monkeypatch, "f", "kill off Rafiq", "")
    decision = ConsoleGate().review_episode(_request())
    assert decision.feedback_scope == "permanent" and decision.feedback_episodes is None


def test_reject_collects_reason_and_optional_feedback(monkeypatch):
    _keys(monkeypatch, "r", "the hook is weak", "")
    decision = ConsoleGate().review_episode(_request())
    assert (decision.action, decision.reason, decision.feedback) == ("reject", "the hook is weak", None)


def test_edit_returns_edited_text_and_unchanged_edit_approves(monkeypatch):
    monkeypatch.setattr("src.gates.edit_text", lambda initial: "my rewrite")
    _keys(monkeypatch, "e", "")
    decision = ConsoleGate().review_episode(_request())
    assert (decision.action, decision.edited_text) == ("edit", "my rewrite")

    monkeypatch.setattr("src.gates.edit_text", lambda initial: initial)
    _keys(monkeypatch, "e")
    assert ConsoleGate().review_episode(_request()).action == "approve"


def test_eof_quits_instead_of_crashing(monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert ConsoleGate().review_episode(_request()).action == "quit"
    assert ConsoleGate().review_plan("plan", 1).action == "quit"


def test_plan_gate_edit_collects_feedback(monkeypatch):
    _keys(monkeypatch, "e", "more heist")
    decision = ConsoleGate().review_plan("plan text", 2)
    assert (decision.action, decision.feedback) == ("edit", "more heist")


def test_auto_gate_approves_clean_drafts_but_stops_on_escalation(capsys):
    assert AutoGate().review_episode(_request()).action == "approve"
    escalated = _request().model_copy(update={"escalation": "cost cap"})
    assert AutoGate().review_episode(escalated).action == "quit"
    assert "cost cap" in capsys.readouterr().out
    assert AutoGate().review_plan("p", 1).action == "approve"


def test_gate_screen_shows_beat_position_and_standing_directives(monkeypatch, capsys):
    _keys(monkeypatch, "q")
    request = _request().model_copy(update={"beat": "The Route (episode 3 of 8 in this beat)", "directives": ["End on dialogue", "Slow the romance"]})
    ConsoleGate().review_episode(request)
    out = capsys.readouterr().out
    assert "Beat: The Route (episode 3 of 8 in this beat)" in out
    assert "Your standing directives:" in out and "  - End on dialogue" in out and "  - Slow the romance" in out


def test_gate_screen_omits_empty_sections(monkeypatch, capsys):
    _keys(monkeypatch, "q")
    ConsoleGate().review_episode(_request())
    out = capsys.readouterr().out
    assert "Beat:" not in out and "standing directives" not in out
