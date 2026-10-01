from src.core import summarizer
from src.models import Episode
from src.storage import repo
from tests.fakes import ScriptedClient


def _finalize(run_id: str, start: int, end: int) -> None:
    for no in range(start, end + 1):
        repo.save_episode(run_id, Episode(episode_no=no, run_id=run_id, beat_id="b", text="t", summary=f"s{no}", status="approved"))


def test_incomplete_block_is_not_summarized(run_id):
    _finalize(run_id, 1, 9)
    client = ScriptedClient(["x"])
    assert summarizer.sync_block_summaries(client, run_id) == []
    assert client.calls == []


def test_complete_block_summarized_once_from_episode_summaries(run_id):
    _finalize(run_id, 1, 10)
    client = ScriptedClient(["block one recap"])
    assert summarizer.sync_block_summaries(client, run_id) == [1]
    assert "Episode 3: s3" in client.calls[0][1]
    assert repo.get_block_summaries(run_id, 11)[0].summary == "block one recap"
    assert summarizer.sync_block_summaries(client, run_id) == []
    assert len(client.calls) == 1


def test_sync_creates_all_missing_complete_blocks_from_given_block(run_id):
    _finalize(run_id, 1, 25)
    client = ScriptedClient(["a", "b"])
    assert summarizer.sync_block_summaries(client, run_id) == [1, 2]
