import logging
import re
from typing import Literal

from src.core.formatting import format_context
from src.llm.base import LLMClient
from src.llm.structured import generate_model
from src.models import Character, CriticResult, EpisodeContext, Fact, StrictModel
from src.prompts import render
from src.storage import repo

logger = logging.getLogger(__name__)

MIN_WORDS = 400
MAX_WORDS = 700
TARGET_WORDS = 560
NGRAM = 5
MAX_RECYCLED_NGRAM_RATIO = 0.10
CRITIC_MAX_TOKENS = 1500
MAX_DRAFT_FACTS = 15
MAX_REPORTED_ISSUES = 5
MAX_WORD_REPEATS = 10
MIN_OVERUSED_WORD_LENGTH = 5
# Function words of five or more letters recur naturally in 600 words; only content words can be a tic.
FUNCTION_WORDS = frozenset(
    ["their", "there", "these", "those", "would", "could", "should", "about", "which", "while", "where", "after", "before", "other", "through", "because", "again", "against", "being", "between", "during", "under", "until", "since", "still", "every", "never", "always", "another"]
)
STRAY_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


class Issue(StrictModel):
    category: Literal["contradiction", "directive", "thread", "repetition", "dead_character", "continuity", "hook", "pacing"]
    severity: Literal["blocker", "minor"]
    description: str
    conflicts_with: str = ""


class CriticVerdict(StrictModel):
    issues: list[Issue]
    hook_strong: bool
    hook_note: str


def _ngrams(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i : i + NGRAM]) for i in range(len(words) - NGRAM + 1)}


def recycled_phrasing(draft: str, context: EpisodeContext) -> tuple[float, int | None]:
    """Largest fraction of the draft's 5-word sequences already present in a single recent episode."""
    draft_grams = _ngrams(draft)
    best_ratio, best_episode = 0.0, None
    for episode in context.recent_episodes:
        if not draft_grams:
            break
        ratio = len(draft_grams & _ngrams(episode.text)) / len(draft_grams)
        if ratio > best_ratio:
            best_ratio, best_episode = ratio, episode.episode_no
    return best_ratio, best_episode


def overused_words(draft: str, roster: list[Character]) -> list[tuple[str, int]]:
    """Content words repeated far beyond natural prose (a motif gone mechanical). Character names are exempt."""
    names = {part for c in roster for name in (c.name, *c.aliases) for part in re.findall(r"[a-z']+", name.lower())}
    counts: dict[str, int] = {}
    for word in re.findall(r"[a-z']+", draft.lower()):
        if len(word) >= MIN_OVERUSED_WORD_LENGTH and word not in names and word not in FUNCTION_WORDS:
            counts[word] = counts.get(word, 0) + 1
    return sorted(((w, n) for w, n in counts.items() if n > MAX_WORD_REPEATS), key=lambda item: -item[1])


def dead_characters_mentioned(draft: str, roster: list[Character]) -> list[str]:
    found = []
    for c in roster:
        if c.status != "dead":
            continue
        if any(re.search(rf"\b{re.escape(name)}\b", draft, re.IGNORECASE) for name in (c.name, *c.aliases) if name):
            found.append(c.name)
    return found


def deterministic_checks(draft: str, context: EpisodeContext) -> tuple[list[str], list[str]]:
    """(blockers, hints): word count and phrasing recycling are hard; dead mentions are hints for the judge."""
    blockers, hints = [], []
    words = len(draft.split())
    if words < MIN_WORDS:
        blockers.append(
            f"Episode is {words} words; it must be {MIN_WORDS}-{MAX_WORDS}. Aim for about {TARGET_WORDS}: add roughly "
            f"{TARGET_WORDS - words} words of concrete scene detail, not summary."
        )
    elif words > MAX_WORDS:
        blockers.append(
            f"Episode is {words} words; it must be {MIN_WORDS}-{MAX_WORDS}. Aim for about {TARGET_WORDS}: cut roughly "
            f"{words - TARGET_WORDS} words by removing whole sentences or minor beats, not by compressing every sentence."
        )

    ratio, source = recycled_phrasing(draft, context)
    if ratio > MAX_RECYCLED_NGRAM_RATIO:
        blockers.append(f"{ratio:.0%} of the phrasing is recycled from episode {source}. Write fresh sentences and a different scene shape.")

    for word, count in overused_words(draft, context.roster)[:3]:
        blockers.append(f"The word '{word}' appears {count} times, which reads as a tic. Cut most of them and vary the language.")

    if STRAY_SCRIPT.search(draft):
        blockers.append("Stray CJK characters appear in the text; remove them and keep the prose in English.")

    dead = dead_characters_mentioned(draft, context.roster)
    if dead:
        hints.append(f"Dead characters named in the draft: {', '.join(dead)}.")
    return blockers, hints


def facts_to_verify(draft: str, context: EpisodeContext) -> list[Fact]:
    """The writer only saw facts selected by tag relevance to the beat, so a contradiction with any
    other fact would be invisible to both. Re-retrieve facts by the entities the draft actually names."""
    shown = {f.fact_id for f in context.facts}
    extra = [
        f for f in repo.get_facts(context.run_id)
        if f.fact_id not in shown and f.established_episode > 0 and any(re.search(rf"\b{re.escape(tag)}\b", draft, re.IGNORECASE) for tag in f.tags if tag)
    ]
    return [*context.facts, *extra[:MAX_DRAFT_FACTS]]


def check(client: LLMClient, context: EpisodeContext, draft: str) -> CriticResult:
    """Only objective rules (word count, recycled phrasing, stray glyphs) block a draft and trigger a rewrite.

    The LLM judge's findings are advice for the human editor. Measured on real runs, its "contradiction" and "directive"
    blockers were almost always wrong (a new name flagged as a conflict, a directive cited before any existed), so letting
    them trigger rewrites only cost money and made drafts worse. They still rank drafts and appear at the review gate.
    """
    blockers, hints = deterministic_checks(draft, context)
    facts = facts_to_verify(draft, context)
    prompt = render(
        "critic",
        **{**format_context(context), "facts": "\n".join(f"- {f.statement}" for f in facts) or "(none)"},
        hints="\n".join(hints) or "(none)",
        draft=draft,
    )
    verdict = generate_model(
        client, CriticVerdict, run_id=context.run_id, stage="critic",
        system=prompt.system, prompt=prompt.user, max_tokens=CRITIC_MAX_TOKENS, episode_no=context.episode_no,
    )
    advice = []
    for issue in verdict.issues[:MAX_REPORTED_ISSUES]:
        cited = f" (cites: {issue.conflicts_with.strip()})" if issue.conflicts_with.strip() else ""
        advice.append(f"[{issue.category}] {issue.description}{cited} (advice)")
    if not verdict.hook_strong:
        advice.append(f"[hook] Weak or repeated ending: {verdict.hook_note} (advice)")

    result = CriticResult(passed=not blockers, blockers=blockers, minor_notes=advice)
    logger.info("critic episode=%d passed=%s blockers=%d advice=%d", context.episode_no, result.passed, len(blockers), len(advice))
    return result
