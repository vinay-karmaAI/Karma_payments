from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as _logger  

if TYPE_CHECKING:
    from loguru import Logger  
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


_logger.remove()


_logger.add(
    sys.stdout,
    level="DEBUG",
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    backtrace=True,
    diagnose=True,
)

_logger.add(
    str(LOG_DIR / "app-{time:YYYY-MM-DD}.log"),
    level="INFO",
    rotation="00:00",
    retention="30 days",
    compression="zip",
    serialize=True,
    backtrace=True,
    diagnose=False,
    enqueue=True,
)

_logger.add(
    str(LOG_DIR / "error-{time:YYYY-MM-DD}.log"),
    level="ERROR",
    rotation="00:00",
    retention="60 days",
    compression="zip",
    serialize=True,
    backtrace=True,
    diagnose=True,
    enqueue=True,
)

_logger.add(
    str(LOG_DIR / "audit-{time:YYYY-MM-DD}.log"),
    level="INFO",
    rotation="00:00",
    retention="365 days",
    compression="zip",
    filter=lambda r: "AUDIT" in r["extra"],
    serialize=True,
    enqueue=True,
)
_logger.add(
    str(LOG_DIR / "reconciliation-{time:YYYY-MM-DD}.log"),
    level="INFO",
    rotation="00:00",
    retention="90 days",
    compression="zip",
    filter=lambda r: "RECON" in r["extra"],
    serialize=True,
    enqueue=True,
)


logger: Logger = _logger  


def audit_log(action: str, user: str = "system", **details: object) -> None:
    """Emit a structured audit event."""
    logger.bind(AUDIT=True).info(
        json.dumps({"action": action, "user": user, "details": details})
    )


def recon_log(run_id: str, event: str, **details: object) -> None:
    """Emit a reconciliation event."""
    logger.bind(RECON=True).info(
        json.dumps({"run_id": run_id, "event": event, "details": details})
    )


__all__ = ["logger", "audit_log", "recon_log"]