import json
import logging
import logging.config
from pathlib import Path

from src.storage import db

NOISY_LOGGERS = ("httpx", "httpcore", "anthropic", "openai", "botocore", "boto3", "urllib3")


class JsonLinesFormatter(logging.Formatter):
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": self.run_id,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def trace_path(run_id: str) -> Path:
    return db.db_path(run_id).parent / "trace.log"


def configure_logging(run_id: str) -> Path:
    """Console (INFO, readable) + runs/<run_id>/trace.log (DEBUG, JSON lines).

    Safe to call repeatedly: dictConfig replaces the root handlers each time.
    """
    path = trace_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "datefmt": "%H:%M:%S"},
                "json": {"()": JsonLinesFormatter, "run_id": run_id},
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "level": "INFO", "formatter": "console"},
                "file": {
                    "class": "logging.FileHandler",
                    "level": "DEBUG",
                    "formatter": "json",
                    "filename": str(path),
                    "encoding": "utf-8",
                },
            },
            "root": {"level": "DEBUG", "handlers": ["console", "file"]},
            "loggers": {name: {"level": "WARNING"} for name in NOISY_LOGGERS},
        }
    )
    return path
