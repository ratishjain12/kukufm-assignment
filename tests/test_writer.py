from src.core import writer
from src.core.writer import Revision
from src.models import Directive
from src.storage import repo
from tests.factories import make_context
from tests.fakes import ScriptedClient


def test_draft_uses_draft_stage_and_includes_directives(run_id):
    ctx = make_context(run_id, directives=[Directive(text="slow the romance", created_at_episode=1)])
    client = ScriptedClient(["Rain on the scooter seat."])
    text = writer.draft_episode(client, ctx)
    assert text == "Rain on the scooter seat."
    assert "slow the romance" in client.calls[0][1]
    assert "WRITE EPISODE 5" in client.calls[0][1]
    assert "REVISION REQUIRED" not in client.calls[0][1]
    assert repo.cost_by_stage(run_id)["draft"]["calls"] == 1


def test_revision_includes_previous_draft_notes_and_uses_revise_stage(run_id):
    client = ScriptedClient(["new text"])
    writer.draft_episode(client, make_context(run_id), Revision(previous_draft="OLD DRAFT", notes=["fix the hook"]))
    prompt = client.calls[0][1]
    assert "OLD DRAFT" in prompt and "fix the hook" in prompt
    assert "revise" in repo.cost_by_stage(run_id)


def test_heading_lines_are_stripped(run_id):
    client = ScriptedClient(["# Episode 5\n**Episode 5: The Route**\nThe door was open."])
    assert writer.draft_episode(client, make_context(run_id)) == "The door was open."
