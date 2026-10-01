import json
import logging

from src.logging_config import configure_logging


def test_trace_file_gets_debug_json_lines_with_run_id(run_id):
    path = configure_logging(run_id)
    logging.getLogger("src.test").debug("hello %s", "world")
    logging.getLogger("httpx").info("noise should be dropped")
    for handler in logging.getLogger().handlers:
        handler.flush()

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["message"] for r in records] == ["hello world"]
    assert records[0]["run_id"] == run_id
    assert records[0]["level"] == "DEBUG"


def test_configure_twice_does_not_duplicate_handlers(run_id):
    configure_logging(run_id)
    configure_logging(run_id)
    assert len(logging.getLogger().handlers) == 2
