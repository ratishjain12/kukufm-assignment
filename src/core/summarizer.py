import logging

from src.core.memory import BLOCK_SIZE, block_range
from src.llm.base import LLMClient, call_with_tracking
from src.models import BlockSummary
from src.prompts import render
from src.storage import repo

logger = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 500


def summarize_block(client: LLMClient, run_id: str, block_no: int) -> BlockSummary | None:
    """Summary of a completed block, built from per-episode summaries only (never from other block
    summaries), so summarization depth never exceeds two levels."""
    start, end = block_range(block_no)
    episodes = repo.get_episodes_range(run_id, start, end)
    if len(episodes) != BLOCK_SIZE:
        return None
    lines = "\n".join(f"Episode {e.episode_no}: {e.summary or e.text[:200]}" for e in episodes)
    prompt = render("summarizer", start=str(start), end=str(end), summaries=lines)
    response = call_with_tracking(
        client, run_id=run_id, stage="summarize", system=prompt.system, prompt=prompt.user,
        max_tokens=SUMMARY_MAX_TOKENS, episode_no=end,
    )
    block = BlockSummary(block_no=block_no, episode_start=start, episode_end=end, summary=response.text.strip())
    repo.save_block_summary(run_id, block)
    logger.info("block summary saved block=%d episodes=%d-%d", block_no, start, end)
    return block


def sync_block_summaries(client: LLMClient, run_id: str, from_block: int = 1) -> list[int]:
    """Create any missing summary for a fully finalized block, starting at from_block."""
    existing = {b.block_no for b in repo.get_block_summaries(run_id, ending_before=10**9)}
    created: list[int] = []
    block_no = from_block
    while True:
        start, end = block_range(block_no)
        if len(repo.get_episodes_range(run_id, start, end)) != BLOCK_SIZE:
            return created
        if block_no not in existing and summarize_block(client, run_id, block_no):
            created.append(block_no)
        block_no += 1
