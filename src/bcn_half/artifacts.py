"""Create timestamped diagnostic folders that capture their own workflow log."""

import logging
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("bcn-half")


def open_artifacts(prefix: str = "") -> tuple[Path, logging.FileHandler]:
    """Create a new artifacts folder and start copying log records into it."""
    folder = Path("artifacts") / (
        prefix + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    folder.mkdir(parents=True)
    handler = logging.FileHandler(folder / "workflow.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    return folder, handler


def close_log(handler: logging.Handler) -> None:
    """Stop copying log records into the artifacts folder."""
    log.removeHandler(handler)
    handler.close()
