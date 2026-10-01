"""Reproducible end-to-end demo run: `python main.py demo`.

One command runs the whole scenario against the real models, prints every step, and exports the deliverables. The human
side is a scripted reviewer whose decisions live in DemoScript (data, not code), so the run is repeatable and auditable:
the plan gate (edit then approve), an episode rejection, two steering directives, a stop and a resume in a fresh session.
"""

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from src.core import cost, orchestrator, planner
from src.core.orchestrator import PlanDecision, ReviewDecision, ReviewRequest, StateIntegrityError
from src.export import export_run
from src.llm.base import LLMError
from src.llm.factory import ClientSet, build_clients
from src.llm.structured import StructuredOutputError
from src.logging_config import configure_logging, trace_path
from src.models import DirectiveScope
from src.storage import repo

MAX_ESCALATION_REJECTIONS = 2


class DemoStopped(Exception):
    """The scripted reviewer could not continue and a human is needed."""


class DirectiveStep(BaseModel):
    episode: int
    text: str  # "{supporting}" is replaced with the first non-protagonist character of the plan
    scope: DirectiveScope = "permanent"
    episodes: int | None = None


class DemoScript(BaseModel):
    premise: str
    plan_length: int = 200
    plan_feedback: str
    reject_at: dict[int, str] = Field(default_factory=dict)
    directives: list[DirectiveStep] = Field(default_factory=list)
    session_one_episodes: int = 12
    total_episodes: int = 18


DEFAULT_SCRIPT = DemoScript(
    premise="A delivery rider realizes every address on today's route belongs to someone who died in the same building.",
    plan_feedback=(
        "Keep this a ghost story: for roughly the first 60 episodes do not explain the mechanism (no technology, "
        "corporations or science) and let the dead stay ambiguous, and keep the ending partly ambiguous. Give every "
        "major character a distinct voice and a concrete goal."
    ),
    reject_at={3: "Show more and tell less: cut the explanation and stay inside one tense scene with concrete sensory detail."},
    directives=[
        DirectiveStep(
            episode=4,
            text="End every episode on a line of spoken dialogue, a line inside quotation marks. Never end on a message, a notification, a sound, or an object appearing.",
        ),
        DirectiveStep(
            episode=8,
            text="Kill off {supporting} within the next few episodes: the death happens on the page, is shocking, and changes what the protagonist does next.",
            scope="next_n_episodes",
            episodes=5,
        ),
    ],
)


class DemoResult(BaseModel):
    run_id: str
    episodes_written: int
    exported: list[Path]


def _say(message: str) -> None:
    print(f"\n>> {message}", flush=True)


class DemoReviewer:
    """Scripted stand-in for the human: every decision is announced, and it never approves a draft that still has blockers."""

    def __init__(self, script: DemoScript, run_id: str = "", *, plan_edited: bool = False, rejected: set[int] | None = None):
        self.script = script
        self.run_id = run_id
        self._rejected: set[int] = rejected or set()
        self._escalations: defaultdict[int, int] = defaultdict(int)
        self._plan_edited = plan_edited

    def _supporting_name(self) -> str:
        characters = [c for c in repo.get_characters(self.run_id) if "protagonist" not in c.role.lower()]
        return characters[0].name if characters else "the closest ally"

    def review_plan(self, plan_text: str, version: int) -> PlanDecision:
        if not self._plan_edited:
            self._plan_edited = True
            _say(f"[reviewer] plan v{version}: EDIT with feedback: {self.script.plan_feedback}")
            return PlanDecision(action="edit", feedback=self.script.plan_feedback)
        _say(f"[reviewer] plan v{version}: APPROVE")
        return PlanDecision(action="approve")

    def review_episode(self, request: ReviewRequest) -> ReviewDecision:
        number = request.episode.episode_no
        if request.escalation:
            self._escalations[number] += 1
            if self._escalations[number] > MAX_ESCALATION_REJECTIONS:
                _say(f"[reviewer] episode {number}: still has blockers after {MAX_ESCALATION_REJECTIONS} rejections; stopping for a human")
                return ReviewDecision(action="quit")
            reason = "Fix these before resubmitting: " + "; ".join(request.critic_notes[:3])
            _say(f"[reviewer] episode {number}: REJECT (critic escalated: {request.escalation})")
            return ReviewDecision(action="reject", reason=reason)
        if number in self.script.reject_at and number not in self._rejected:
            self._rejected.add(number)
            _say(f"[reviewer] episode {number}: REJECT: {self.script.reject_at[number]}")
            return ReviewDecision(action="reject", reason=self.script.reject_at[number])
        step = next((s for s in self.script.directives if s.episode == number), None)
        if step is None:
            _say(f"[reviewer] episode {number}: APPROVE ({request.episode.word_count} words)")
            return ReviewDecision(action="approve")
        text = step.text.replace("{supporting}", self._supporting_name())
        _say(f"[reviewer] episode {number}: APPROVE + FEEDBACK ({step.scope}): {text}")
        return ReviewDecision(action="approve", feedback=text, feedback_scope=step.scope, feedback_episodes=step.episodes)


def _continue_hint(run_id: str) -> str:
    return f"progress is saved; continue with `uv run python -u main.py demo --resume {run_id}`"


def _require_progress(outcome: str, run_id: str) -> None:
    if outcome in ("quit", "budget"):
        raise DemoStopped(f"demo stopped early ({outcome}); {_continue_hint(run_id)}")


def _resume_reviewer(script: DemoScript, run_id: str) -> DemoReviewer:
    """A reviewer that knows what the earlier session already did, so it never repeats a plan edit or a scripted rejection."""
    plan = repo.get_arc_plan(run_id)
    next_episode = repo.require_run(run_id).current_episode
    rejected = {n for n in script.reject_at if n < next_episode}
    return DemoReviewer(script, run_id, plan_edited=plan is not None and plan.version > 1, rejected=rejected)


def _run_phases(
    script: DemoScript, run_id: str, reviewer: DemoReviewer, clients: ClientSet, make_clients: Callable[[], ClientSet]
) -> None:
    plan = repo.get_arc_plan(run_id)
    if plan is None:
        _say(f"phase 1/4: plan {script.plan_length} episodes, then the plan gate")
        planner.generate_arc_plan(run_id, clients.planner, total_episodes=script.plan_length)
    if repo.require_arc_plan(run_id).status != "approved" and not orchestrator.review_plan(run_id, clients, reviewer):
        raise DemoStopped(f"plan was not approved; {_continue_hint(run_id)}")

    written = repo.require_run(run_id).current_episode - 1
    first_session = min(script.session_one_episodes, script.total_episodes) - written
    if first_session > 0:
        _say(f"phase 2/4: session 1, {first_session} episodes from episode {written + 1}")
        _, outcome = orchestrator.run_episodes(run_id, clients, reviewer, count=first_session)
        _require_progress(outcome, run_id)

    remaining = script.total_episodes - (repo.require_run(run_id).current_episode - 1)
    if remaining > 0:
        _say("phase 3/4: session 2 (new clients, state resumed from disk)")
        _, outcome = orchestrator.run_episodes(run_id, make_clients(), reviewer, count=remaining)
        _require_progress(outcome, run_id)


def run_demo(
    script: DemoScript, out_dir: Path, make_clients: Callable[[], ClientSet] = build_clients, resume_run_id: str | None = None
) -> DemoResult:
    clients = make_clients()
    if resume_run_id:
        run_id = resume_run_id
        reviewer = _resume_reviewer(script, run_id)
        configure_logging(run_id)
        _say(f"resuming run {run_id} at episode {repo.require_run(run_id).current_episode}. Live trace: tail -F {trace_path(run_id)}")
    else:
        run_id = repo.create_run(script.premise).run_id
        reviewer = DemoReviewer(script, run_id)
        configure_logging(run_id)
        _say(f"run {run_id} created. Live trace: tail -F {trace_path(run_id)}")

    try:
        _run_phases(script, run_id, reviewer, clients, make_clients)
    except (LLMError, StructuredOutputError, StateIntegrityError) as exc:
        raise DemoStopped(f"{exc}\n{_continue_hint(run_id)}") from exc

    _say("phase 4/4: export")
    exported = export_run(run_id, out_dir)
    for path in exported:
        print(f"   {path}")
    print("\n" + cost.format_report(run_id), flush=True)
    return DemoResult(run_id=run_id, episodes_written=repo.require_run(run_id).current_episode - 1, exported=exported)
