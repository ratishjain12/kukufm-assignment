"""State machine for plan -> write -> critique -> human gate -> persist, plus resume and retroactive
edits. All human interaction goes through the HumanGate protocol so this module does no I/O and the
same flow is driven by the CLI or by scripted gates in tests."""

import logging
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from src import config
from src.core import critic, extractor, memory, planner, state_delta, summarizer, writer
from src.core.extractor import Extraction
from src.core.formatting import format_plan
from src.core.writer import Revision
from src.llm.factory import ClientSet
from src.models import (
    FINALIZED_STATUSES,
    CriticResult,
    Directive,
    DirectiveScope,
    Episode,
    EpisodeContext,
)
from src.storage import repo

logger = logging.getLogger(__name__)


class PlanDecision(BaseModel):
    action: Literal["approve", "edit", "reject", "quit"]
    feedback: str | None = None


class ReviewDecision(BaseModel):
    action: Literal["approve", "edit", "reject", "quit"]
    edited_text: str | None = None
    reason: str | None = None
    feedback: str | None = None
    feedback_scope: Literal["permanent", "next_n_episodes"] = "permanent"
    feedback_episodes: int | None = None


class ReviewRequest(BaseModel):
    episode: Episode
    critic_notes: list[str] = Field(default_factory=list)
    escalation: str | None = None
    cycle_cost_usd: float = 0.0
    beat: str = ""
    directives: list[str] = Field(default_factory=list)


class HumanGate(Protocol):
    def review_plan(self, plan_text: str, version: int) -> PlanDecision: ...

    def review_episode(self, request: ReviewRequest) -> ReviewDecision: ...


StepOutcome = Literal["written", "quit", "complete", "budget"]


class EditPreview(BaseModel):
    episode_no: int
    new_extraction: Extraction
    hard_differences: list[str]
    later_episodes: list[int]


class EditImpact(BaseModel):
    episode_no: int
    stale_episodes: list[int] = Field(default_factory=list)
    hard_differences: list[str] = Field(default_factory=list)
    accepted_inconsistency: bool = False


class StateIntegrityError(Exception):
    """Finalized episodes exist without recorded state deltas, so world state cannot be trusted."""


def find_state_gaps(run_id: str) -> list[int]:
    """Finalized episodes before the current position that have no state delta (for example after an interrupted rebuild)."""
    current = repo.require_run(run_id).current_episode
    return [e.episode_no for e in repo.get_episodes_range(run_id, 1, current - 1) if repo.get_state_delta(run_id, e.episode_no) is None]


# ---------------------------------------------------------------- plan ----

def review_plan(run_id: str, clients: ClientSet, gate: HumanGate) -> bool:
    """Loops the plan gate until approved (True) or the human quits (False)."""
    while True:
        plan = repo.require_arc_plan(run_id)
        text = format_plan(plan, repo.get_characters(run_id), repo.get_threads(run_id), repo.get_facts(run_id))
        decision = gate.review_plan(text, plan.version)
        logger.info("plan gate v%d decision=%s", plan.version, decision.action)
        if decision.action == "approve":
            planner.approve_arc_plan(run_id)
            return True
        if decision.action == "quit":
            return False
        if decision.action == "edit":
            if not decision.feedback:
                logger.warning("plan edit requested without feedback; ignoring")
                continue
            planner.revise_arc_plan(run_id, clients.planner, decision.feedback)
        else:
            planner.generate_arc_plan(run_id, clients.planner, plan.total_episodes, feedback=decision.feedback or "")


# ------------------------------------------------------------ directives ----

def add_directive(run_id: str, episode_no: int, text: str, scope: DirectiveScope = "permanent", episodes: int | None = None) -> Directive:
    """Turns human feedback into standing steering. Stored as an absolute expiry episode so it can be
    read without re-deriving the window."""
    expires_after = None
    if scope == "next_n_episodes":
        if episodes is None or episodes < 1:
            raise ValueError("next_n_episodes directives need a positive episode count")
        expires_after = episode_no + episodes
    directive = Directive(text=text, created_at_episode=episode_no, scope=scope, expires_after_episode=expires_after)
    repo.add_directive(run_id, directive)
    logger.info("directive added id=%s scope=%s expires_after=%s text=%r", directive.directive_id, scope, directive.expires_after_episode, text)
    return directive


# --------------------------------------------------------------- episode ----

class _Draft(BaseModel):
    text: str
    critic_notes: list[str]
    revisions: int
    escalation: str | None
    cycle_cost_usd: float


def _rank(result: CriticResult) -> tuple[int, int]:
    return len(result.blockers), len(result.minor_notes)


def _produce_draft(run_id: str, clients: ClientSet, context: EpisodeContext, revision: Revision | None) -> _Draft:
    """One draft cycle: write, critique, and rewrite while there are blockers, then ship the BEST draft seen.

    Blockers are objective (word count, recycled phrasing, stray glyphs) or verified direct conflicts with facts, roster
    or directives; the judge's other findings are advice and never trigger a rewrite. Bounds: MAX_REVISIONS rewrites and
    MAX_EPISODE_COST_USD of spend within this cycle. Rewrites can make a draft worse, so the last draft is not assumed best.
    """
    episode_no = context.episode_no
    baseline = repo.sum_episode_cost(run_id, episode_no)
    text = writer.draft_episode(clients.writer, context, revision)
    attempts: list[tuple[str, CriticResult]] = []
    revisions = 0
    escalation = None
    while True:
        result = critic.check(clients.critic, context, text)
        attempts.append((text, result))
        spent = repo.sum_episode_cost(run_id, episode_no) - baseline
        if result.passed:
            break
        if revisions >= config.MAX_REVISIONS:
            escalation = f"blockers remain after {revisions} rewrite(s)"
            break
        if spent >= config.MAX_EPISODE_COST_USD:
            escalation = f"episode cost cap reached (${spent:.2f} >= ${config.MAX_EPISODE_COST_USD:.2f})"
            break
        revisions += 1
        logger.warning("episode %d blockers, rewrite %d/%d: %s", episode_no, revisions, config.MAX_REVISIONS, "; ".join(result.blockers))
        text = writer.draft_episode(clients.writer, context, Revision(previous_draft=text, notes=result.blockers))

    best = min(range(len(attempts)), key=lambda i: (_rank(attempts[i][1]), -i))
    if escalation:
        logger.error("episode %d escalated to human: %s; shipping draft %d of %d", episode_no, escalation, best + 1, len(attempts))
    best_text, best_result = attempts[best]
    return _Draft(text=best_text, critic_notes=best_result.notes, revisions=revisions, escalation=escalation, cycle_cost_usd=spent)


def _finalize(run_id: str, clients: ClientSet, episode: Episode) -> None:
    """Idempotent post-approval step: extract state, summarize completed blocks, advance the run."""
    n = episode.episode_no
    if repo.get_state_delta(run_id, n) is None:
        extraction = extractor.extract(clients.extractor, run_id, n, episode.text)
        repo.save_state_delta(run_id, state_delta.apply_extraction(run_id, n, extraction, episode.text))
        episode.summary = extraction.summary
        repo.save_episode(run_id, episode)
    summarizer.sync_block_summaries(clients.extractor, run_id, from_block=memory.block_no_for(n))
    repo.update_run_progress(run_id, n + 1)
    logger.info("episode %d finalized status=%s source=%s", n, episode.status, episode.source)


def _review_view(run_id: str, episode_no: int) -> tuple[str, list[str]]:
    """What the reviewer needs next to the draft: where this episode sits in its beat, and the feedback still in force."""
    context = memory.assemble_context(run_id, episode_no)
    beat = context.beat
    position = f"episode {episode_no - beat.episode_start + 1} of {beat.episode_end - beat.episode_start + 1}"
    return f"{beat.title} ({position} in this beat)", [d.text for d in context.directives]


def write_next_episode(run_id: str, clients: ClientSet, gate: HumanGate) -> tuple[StepOutcome, Episode | None]:
    run = repo.require_run(run_id)
    plan = repo.require_arc_plan(run_id)
    n = run.current_episode
    if n > plan.total_episodes:
        return "complete", None

    existing = repo.get_episode(run_id, n)
    if existing and existing.status in FINALIZED_STATUSES:
        _finalize(run_id, clients, existing)
        return "written", existing

    episode = existing if existing and existing.status == "critiqued" else None
    escalation: str | None = None
    cycle_cost = 0.0
    revision: Revision | None = None

    while True:
        if episode is None:
            context = memory.assemble_context(run_id, n)
            draft = _produce_draft(run_id, clients, context, revision)
            escalation, cycle_cost = draft.escalation, draft.cycle_cost_usd
            episode = Episode(
                episode_no=n, run_id=run_id, beat_id=context.beat.beat_id, text=draft.text, status="critiqued",
                critic_notes=draft.critic_notes, revision_count=draft.revisions,
            )
            repo.save_episode(run_id, episode)

        beat, directives = _review_view(run_id, n)
        decision = gate.review_episode(
            ReviewRequest(
                episode=episode, critic_notes=episode.critic_notes, escalation=escalation, cycle_cost_usd=cycle_cost,
                beat=beat, directives=directives,
            )
        )
        logger.info("episode %d gate decision=%s feedback=%s", n, decision.action, bool(decision.feedback))
        if decision.feedback:
            add_directive(run_id, n, decision.feedback, decision.feedback_scope, decision.feedback_episodes)
            episode.human_feedback = decision.feedback

        if decision.action == "quit":
            repo.save_episode(run_id, episode)
            return "quit", None
        if decision.action == "reject":
            notes = [decision.reason or "The human reviewer rejected this draft; take a clearly different approach."]
            if decision.feedback:
                notes.append(f"Reviewer feedback: {decision.feedback}")
            revision = Revision(previous_draft=episode.text, notes=notes)
            episode = None
            continue
        if decision.action == "edit":
            if not decision.edited_text or not decision.edited_text.strip():
                logger.warning("edit requested with empty text; asking again")
                continue
            episode.text = decision.edited_text.strip()
            episode.status, episode.source = "edited", "human_edited"
        else:
            episode.status = "approved"
        repo.save_episode(run_id, episode)
        _finalize(run_id, clients, episode)
        return "written", episode


def run_episodes(run_id: str, clients: ClientSet, gate: HumanGate, count: int | None = None) -> tuple[list[Episode], StepOutcome]:
    gaps = find_state_gaps(run_id)
    if gaps:
        raise StateIntegrityError(f"episodes {gaps} are finalized but have no recorded state; run `rebuild-state` first")
    written: list[Episode] = []
    while count is None or len(written) < count:
        if config.MAX_RUN_COST_USD and repo.sum_run_cost(run_id)["total_cost"] >= config.MAX_RUN_COST_USD:
            logger.error("run budget reached: $%.2f", config.MAX_RUN_COST_USD)
            return written, "budget"
        outcome, episode = write_next_episode(run_id, clients, gate)
        if outcome != "written" or episode is None:
            return written, outcome
        written.append(episode)
    return written, "written"


# ------------------------------------------------------- retroactive edit ----

def _later_finalized(run_id: str, episode_no: int) -> list[int]:
    return [e.episode_no for e in repo.get_episodes_range(run_id, episode_no + 1)]


def preview_edit(run_id: str, clients: ClientSet, episode_no: int, new_text: str) -> EditPreview:
    """Read-only: re-extract the edited text and diff plot-level claims against what the original said."""
    original = repo.get_episode(run_id, episode_no)
    old_delta = repo.get_state_delta(run_id, episode_no)
    if original is None or original.status not in FINALIZED_STATUSES or old_delta is None:
        raise ValueError(f"episode {episode_no} is not finalized, so it cannot be retroactively edited")
    new_extraction = extractor.extract(clients.extractor, run_id, episode_no, new_text)
    diffs = extractor.hard_differences(Extraction.model_validate(old_delta.extraction), new_extraction)
    logger.info("edit preview episode=%d hard_differences=%d", episode_no, len(diffs))
    return EditPreview(
        episode_no=episode_no, new_extraction=new_extraction, hard_differences=diffs,
        later_episodes=_later_finalized(run_id, episode_no),
    )


def commit_edit(run_id: str, clients: ClientSet, preview: EditPreview, new_text: str, invalidate_later: bool) -> EditImpact:
    """Apply a retroactive edit.

    invalidate_later (or no later episodes): roll live state back to the end of episode N-1 by reverting
    deltas newest-first, apply the new reading of N, flag N+1.. stale and point the run at N+1 so they
    are regenerated in order. Otherwise only the text and stored reading change; world state keeps the
    old reading, and any hard differences are an inconsistency the human has knowingly accepted.
    """
    n = preview.episode_no
    episode = repo.require_episode(run_id, n)
    episode.text, episode.summary = new_text.strip(), preview.new_extraction.summary
    episode.status, episode.source = "edited", "human_edited"
    old_delta = repo.require_state_delta(run_id, n)
    later = preview.later_episodes

    if not later or invalidate_later:
        for no in range(max([n, *later]), n - 1, -1):
            delta = repo.get_state_delta(run_id, no)
            if delta:
                state_delta.revert_delta(run_id, delta)
        repo.delete_state_deltas_from(run_id, n)
        repo.save_state_delta(run_id, state_delta.apply_extraction(run_id, n, preview.new_extraction, new_text.strip()))
        repo.save_episode(run_id, episode)
        stale = repo.mark_episodes_stale(run_id, n + 1) if later else []
        repo.delete_block_summaries_from(run_id, memory.block_no_for(n))
        summarizer.sync_block_summaries(clients.extractor, run_id, from_block=memory.block_no_for(n))
        if stale:
            repo.update_run_progress(run_id, n + 1)
        logger.info("episode %d edited; rolled back state, %d later episode(s) marked stale", n, len(stale))
        return EditImpact(episode_no=n, stale_episodes=stale, hard_differences=preview.hard_differences)

    repo.save_state_delta(run_id, old_delta.model_copy(update={"extraction": preview.new_extraction.model_dump()}))
    repo.save_episode(run_id, episode)
    summarizer.summarize_block(clients.extractor, run_id, memory.block_no_for(n))
    accepted = bool(preview.hard_differences)
    if accepted:
        logger.warning("episode %d edited; kept %d later episode(s) despite %d hard difference(s): %s", n, len(later), len(preview.hard_differences), "; ".join(preview.hard_differences))
    return EditImpact(episode_no=n, hard_differences=preview.hard_differences, accepted_inconsistency=accepted)


def rebuild_state(run_id: str, clients: ClientSet, from_episode: int = 1) -> list[int]:
    """Re-derive world state from the stored episode texts, from from_episode onward.

    State is derived data: revert the recorded deltas newest-first, then re-extract and re-apply each finalized
    episode in order. Use it after changing the extractor model or prompt. Stale or unreviewed episodes are untouched.
    """
    episodes = repo.get_episodes_range(run_id, from_episode)
    for episode in reversed(episodes):
        delta = repo.get_state_delta(run_id, episode.episode_no)
        if delta:
            state_delta.revert_delta(run_id, delta)
    repo.delete_state_deltas_from(run_id, from_episode)
    for episode in episodes:
        extraction = extractor.extract(clients.extractor, run_id, episode.episode_no, episode.text)
        repo.save_state_delta(run_id, state_delta.apply_extraction(run_id, episode.episode_no, extraction, episode.text))
        episode.summary = extraction.summary
        repo.save_episode(run_id, episode)
    first_block = memory.block_no_for(from_episode)
    repo.delete_block_summaries_from(run_id, first_block)
    summarizer.sync_block_summaries(clients.extractor, run_id, from_block=first_block)
    logger.info("state rebuilt from episode %d: %d episode(s) re-extracted", from_episode, len(episodes))
    return [e.episode_no for e in episodes]
