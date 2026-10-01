from src.core import cost
from src.models import Episode, LLMCallLog
from src.storage import repo


def _call(run_id, stage, usd, ms, tokens=100):
    repo.log_llm_call(
        run_id,
        LLMCallLog(run_id=run_id, stage=stage, provider="p", model="m", prompt_tokens=tokens, completion_tokens=0, cost_usd=usd, latency_ms=ms),
    )


def test_no_projection_before_first_finalized_episode(run_id):
    _call(run_id, "plan", 1.0, 1000)
    assert cost.project(run_id, 200) is None


def test_projection_separates_one_time_plan_from_per_episode_average(run_id):
    _call(run_id, "plan", 1.0, 60_000, tokens=1000)
    for no in (1, 2):
        repo.save_episode(run_id, Episode(episode_no=no, run_id=run_id, beat_id="b", text="t", status="approved"))
        _call(run_id, "draft", 0.30, 20_000, tokens=500)
        _call(run_id, "critic", 0.10, 10_000, tokens=100)

    p = cost.project(run_id, 200)
    assert p.episodes_done == 2 and p.priced
    assert p.plan_usd == 1.0 and p.per_episode_usd == 0.40
    assert p.projected_total_usd == 1.0 + 0.40 * 200
    assert p.per_episode_llm_seconds == 30 and p.per_episode_tokens == 600
    assert p.projected_total_tokens == 1000 + 600 * 200
    assert round(p.projected_llm_hours, 3) == round((60 + 30 * 200) / 3600, 3)


def test_unpriced_runs_are_flagged(run_id):
    repo.save_episode(run_id, Episode(episode_no=1, run_id=run_id, beat_id="b", text="t", status="approved"))
    _call(run_id, "draft", 0.0, 1000)
    assert cost.project(run_id, 200).priced is False
