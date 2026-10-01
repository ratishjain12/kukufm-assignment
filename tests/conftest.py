import logging

import pytest

from src import config
from src.storage import db, repo


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "RUNS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def run_id(runs_dir) -> str:
    return repo.create_run("test premise").run_id


@pytest.fixture(autouse=True)
def isolate_stopping_rules(monkeypatch):
    """Tests must not depend on a developer's local .env (for example MAX_RUN_COST_USD)."""
    monkeypatch.setattr(config, "MAX_REVISIONS", 2)
    monkeypatch.setattr(config, "MAX_EPISODE_COST_USD", 0.50)
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 0.0)


@pytest.fixture(autouse=True)
def reset_root_logging():
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
