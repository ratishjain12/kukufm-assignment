import re
from collections.abc import Callable
from typing import NamedTuple

from src.core import planner
from src.core.critic import CriticVerdict
from src.core.extractor import Extraction
from src.core.orchestrator import PlanDecision, ReviewDecision, ReviewRequest
from src.llm.factory import ClientSet
from tests.factories import ext_char, make_draft, make_extraction, words
from tests.fakes import ScriptedClient


class Story(NamedTuple):
    clients: ClientSet
    writer: ScriptedClient
    critic: ScriptedClient
    extractor: ScriptedClient
    extractions: dict[int, Extraction]


def with_evidence(extraction: Extraction, text: str) -> Extraction:
    """The fake extractor 'quotes' the first words of the episode, as a real one must for threads and status changes."""
    quote = " ".join(text.split()[:12])
    return extraction.model_copy(
        update={
            "characters": [c.model_copy(update={"evidence": quote}) for c in extraction.characters],
            "threads": [th.model_copy(update={"evidence": quote}) for th in extraction.threads],
        }
    )


def default_extractions() -> dict[int, Extraction]:
    return {
        1: make_extraction(summary="Rafiq appears.", characters=[ext_char("Rafiq")]),
        2: make_extraction(summary="Rafiq dies.", characters=[ext_char("Rafiq", "dead")]),
        3: make_extraction(summary="Mira arrives.", characters=[ext_char("Mira")]),
    }


def build_story(
    critic_verdict: Callable[[int], CriticVerdict] | None = None,
    word_counts: Callable[[int, int], int] | None = None,
) -> Story:
    """word_counts(episode_no, attempt_no) sets each draft's length; over 700 words trips the objective length rule."""
    extractions = default_extractions()
    write_counts: dict[int, int] = {}

    def writer_reply(system: str, prompt: str, schema) -> str:
        n = int(re.search(r"WRITE EPISODE (\d+)", prompt).group(1))
        write_counts[n] = write_counts.get(n, 0) + 1
        length = word_counts(n, write_counts[n]) if word_counts else 500
        return words(length, f"e{n}v{write_counts[n]}_")

    def critic_reply(system: str, prompt: str, schema) -> str:
        n = int(re.search(r"\(episode (\d+)\)", prompt).group(1))
        verdict = critic_verdict(n) if critic_verdict else CriticVerdict(issues=[], hook_strong=True, hook_note="")
        return verdict.model_dump_json()

    def extractor_reply(system: str, prompt: str, schema) -> str:
        if schema is None:
            return "BLOCK-RECAP"
        match = re.search(r"EPISODE (\d+) TEXT:\n(.*?)\n\nCHARACTER ROSTER", prompt, re.DOTALL)
        n, text = int(match.group(1)), match.group(2)
        if "EDITED-ALIVE" in text:
            return with_evidence(make_extraction(summary="Rafiq lives.", characters=[ext_char("Rafiq", "alive")]), text).model_dump_json()
        default = extractions.get(n, make_extraction(summary=f"Episode {n} happens."))
        return with_evidence(default, text).model_dump_json()

    writer, critic, extractor = ScriptedClient(writer_reply), ScriptedClient(critic_reply), ScriptedClient(extractor_reply)
    planner_client = ScriptedClient([make_draft(20)])
    return Story(ClientSet(planner_client, writer, critic, extractor), writer, critic, extractor, extractions)


def seed_plan(run_id: str, story: Story, approve: bool = True) -> None:
    planner.generate_arc_plan(run_id, story.clients.planner, total_episodes=20)
    if approve:
        planner.approve_arc_plan(run_id)


class ScriptedGate:
    """Pops queued decisions; once exhausted it approves everything."""

    def __init__(self, episode_decisions: list[ReviewDecision] | None = None, plan_decisions: list[PlanDecision] | None = None):
        self.episode_decisions = list(episode_decisions or [])
        self.plan_decisions = list(plan_decisions or [])
        self.requests: list[ReviewRequest] = []

    def review_plan(self, plan_text: str, version: int) -> PlanDecision:
        return self.plan_decisions.pop(0) if self.plan_decisions else PlanDecision(action="approve")

    def review_episode(self, request: ReviewRequest) -> ReviewDecision:
        self.requests.append(request)
        return self.episode_decisions.pop(0) if self.episode_decisions else ReviewDecision(action="approve")


def approve(**kw) -> ReviewDecision:
    return ReviewDecision(action="approve", **kw)
