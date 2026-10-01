from pydantic import BaseModel

from src.storage import repo


class CostProjection(BaseModel):
    episodes_done: int
    total_episodes: int
    spent_usd: float
    plan_usd: float
    per_episode_usd: float
    projected_total_usd: float
    per_episode_llm_seconds: float
    projected_llm_hours: float
    per_episode_tokens: int
    projected_total_tokens: int
    by_stage: dict[str, dict]
    priced: bool


def project(run_id: str, total_episodes: int) -> CostProjection | None:
    """Linear extrapolation from measured spend: plan once, then the observed per-episode average
    (which includes revisions, rejections, extraction and block summaries — real rework, not the ideal
    case). LLM time only; human review time is not included."""
    summary = repo.sum_run_cost(run_id)
    done = summary["episodes_done"]
    if done == 0:
        return None
    by_stage = repo.cost_by_stage(run_id)
    plan = by_stage.get("plan", {"cost_usd": 0.0, "latency_ms": 0})
    plan_tokens = repo.stage_tokens(run_id, "plan")
    per_ep_cost = (summary["total_cost"] - plan["cost_usd"]) / done
    per_ep_seconds = (summary["total_latency_ms"] - plan["latency_ms"]) / 1000 / done
    all_tokens = summary["prompt_tokens"] + summary["completion_tokens"]
    per_ep_tokens = (all_tokens - plan_tokens) // done
    return CostProjection(
        episodes_done=done,
        total_episodes=total_episodes,
        spent_usd=summary["total_cost"],
        plan_usd=plan["cost_usd"],
        per_episode_usd=per_ep_cost,
        projected_total_usd=plan["cost_usd"] + per_ep_cost * total_episodes,
        per_episode_llm_seconds=per_ep_seconds,
        projected_llm_hours=(plan["latency_ms"] / 1000 + per_ep_seconds * total_episodes) / 3600,
        per_episode_tokens=per_ep_tokens,
        projected_total_tokens=plan_tokens + per_ep_tokens * total_episodes,
        by_stage=by_stage,
        priced=summary["total_cost"] > 0,
    )


def format_report(run_id: str) -> str:
    plan = repo.get_arc_plan(run_id)
    total = plan.total_episodes if plan else 200
    summary = repo.sum_run_cost(run_id)
    lines = [
        f"Run {run_id}: {summary['calls']} LLM calls, {summary['episodes_done']} episodes done, {summary['total_retries']} retries",
        f"Spent ${summary['total_cost']:.4f}  |  tokens in/out {summary['prompt_tokens']:,}/{summary['completion_tokens']:,}  |  LLM time {summary['total_latency_ms'] / 1000:.0f}s",
        "",
        "By stage:",
    ]
    for stage, row in sorted(repo.cost_by_stage(run_id).items()):
        lines.append(f"  {stage:<10} calls={row['calls']:<4} ${row['cost_usd']:.4f}  {row['latency_ms'] / 1000:.0f}s")
    projection = project(run_id, total)
    if projection is None:
        return "\n".join([*lines, "", "No finalized episodes yet; nothing to project."])
    lines += [
        "",
        f"Projection to {total} episodes (measured average incl. revisions/rejections; LLM time only, excludes human review):",
        f"  per episode: ${projection.per_episode_usd:.4f}, {projection.per_episode_llm_seconds:.0f}s, {projection.per_episode_tokens:,} tokens",
        f"  total:       ${projection.projected_total_usd:.2f}, {projection.projected_llm_hours:.1f}h of LLM time, {projection.projected_total_tokens:,} tokens",
    ]
    if not projection.priced:
        lines.append("  WARNING: cost is $0 because pricing.json has no rates for the models used; fill it in (see .env.example).")
    return "\n".join(lines)
