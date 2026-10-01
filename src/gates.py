"""Human gates: the interactive console implementation and the auto-approve one used for unattended
runs. Both satisfy orchestrator.HumanGate; neither contains story logic."""

import os
import shlex
import subprocess
import tempfile
import textwrap
from pathlib import Path

from src.core.orchestrator import PlanDecision, ReviewDecision, ReviewRequest

WIDTH = 88
RULE = "-" * WIDTH


def edit_text(initial: str) -> str:
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write(initial)
        path = Path(handle.name)
    try:
        subprocess.run([*shlex.split(editor), str(path)], check=True)
        return path.read_text()
    finally:
        path.unlink(missing_ok=True)


def wrap(text: str) -> str:
    return "\n\n".join(textwrap.fill(p, WIDTH) for p in text.split("\n\n") if p.strip())


def ask(prompt: str) -> str:
    """Free-text prompt that treats end of input as an empty answer."""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _choose(prompt: str, valid: str) -> str:
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return "q"
        if answer and answer[0] in valid:
            return answer[0]
        print(f"  choose one of: {', '.join(valid)}")


def _ask_feedback() -> dict:
    text = ask("  Standing feedback for future episodes (blank for none): ")
    if not text:
        return {}
    scope = ask("  Applies to: [Enter] all future episodes, or a number N for just the next N episodes: ")
    if scope.isdigit() and int(scope) > 0:
        return {"feedback": text, "feedback_scope": "next_n_episodes", "feedback_episodes": int(scope)}
    return {"feedback": text}


class ConsoleGate:
    def review_plan(self, plan_text: str, version: int) -> PlanDecision:
        print(f"\n{RULE}\n{plan_text}\n{RULE}")
        choice = _choose("[a]pprove  [e]dit with feedback  [r]eject and regenerate  [q]uit > ", "aerq")
        if choice == "a":
            return PlanDecision(action="approve")
        if choice == "q":
            return PlanDecision(action="quit")
        prompt = "What should change in the plan? " if choice == "e" else "Any guidance for the new plan (blank for none)? "
        feedback = ask(prompt) or None
        return PlanDecision(action="edit" if choice == "e" else "reject", feedback=feedback)

    def review_episode(self, request: ReviewRequest) -> ReviewDecision:
        episode = request.episode
        print(f"\n{RULE}\nEPISODE {episode.episode_no}  |  {episode.word_count} words  |  {episode.revision_count} auto-revision(s)  |  cycle cost ${request.cycle_cost_usd:.3f}")
        if request.escalation:
            print(f"!! ESCALATED: {request.escalation}")
        if request.beat:
            print(f"Beat: {request.beat}")
        if request.directives:
            print("Your standing directives:")
            for directive in request.directives:
                print(f"  - {directive}")
        for note in request.critic_notes:
            print(f"  critic: {note}")
        print(f"{RULE}\n{wrap(episode.text)}\n{RULE}")

        choice = _choose("[a]pprove  [f] approve + steering feedback  [e]dit  [r]eject  [q]uit > ", "afeqr")
        if choice == "a":
            return ReviewDecision(action="approve")
        if choice == "f":
            return ReviewDecision(action="approve", **_ask_feedback())
        if choice == "q":
            return ReviewDecision(action="quit")
        if choice == "r":
            reason = ask("  What is wrong with this draft? ") or None
            return ReviewDecision(action="reject", reason=reason, **_ask_feedback())

        edited = edit_text(episode.text).strip()
        if edited == episode.text.strip():
            print("  no changes made; approving as written")
            return ReviewDecision(action="approve")
        return ReviewDecision(action="edit", edited_text=edited, **_ask_feedback())


class AutoGate:
    """Approves clean drafts without prompting. An escalated draft (the critic could not fix it within the bounds)
    stops the run instead of being shipped; it stays saved for a human to review and resume."""

    def review_plan(self, plan_text: str, version: int) -> PlanDecision:
        return PlanDecision(action="approve")

    def review_episode(self, request: ReviewRequest) -> ReviewDecision:
        number = request.episode.episode_no
        if request.escalation:
            print(f"Episode {number}: stopped for human review ({request.escalation})")
            return ReviewDecision(action="quit")
        print(f"Episode {number}: auto-approved ({request.episode.word_count} words, {len(request.critic_notes)} critic note(s))")
        return ReviewDecision(action="approve")
