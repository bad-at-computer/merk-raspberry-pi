from __future__ import annotations

import re
from dataclasses import dataclass

EN_DASH = "\u2013"
EN_DASH_ANOTHER = "\u2014"

RE_POSITION = re.compile(r"Position\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)
RE_ESTIMATE = re.compile(
    rf"Estimated\s+(?:of\s+)?(\d+)\s*(?:to|[{EN_DASH}{EN_DASH_ANOTHER}-])"
    rf"\s*(\d+)\s*min(?:utes)?"
    rf"|Estimated\s+(\d+)\s*min(?:utes)?",
    re.IGNORECASE,
)

CLOUDFLARE_MARKERS = (
    "verifying you are human",
    "verify you are human",
    "just a moment",
    "cf-chl",
    "attention required",
    "enable javascript and cookies",
    "access denied",
    "checking your browser",
)

FINAL_MESSAGE = "Final notification for this queue"


@dataclass(frozen=True)
class QueueState:
    position: int
    total: int
    est_low: int | None = None
    est_high: int | None = None


def parse_queue_state(text: str) -> QueueState | None:
    if not text:
        return None
    pos = RE_POSITION.search(text)
    if not pos:
        return None
    position = int(pos.group(1))
    total = int(pos.group(2))
    est = RE_ESTIMATE.search(text)
    est_low = est_high = None
    if est:
        if est.group(1) is not None:
            est_low = int(est.group(1))
            est_high = int(est.group(2))
        else:
            est_low = int(est.group(3))
            est_high = int(est.group(3))
    return QueueState(position, total, est_low, est_high)


def has_text(text: str, needle: str) -> bool:
    if not text:
        return False
    return needle.lower() in text.lower()


def has_join_button(text: str) -> bool:
    return has_text(text, "Join queue") and not has_text(text, "Leave queue")


def has_leave_button(text: str) -> bool:
    return has_text(text, "Leave queue")


def has_task_ready(text: str) -> bool:
    return has_text(text, "Your task is ready")


def is_cloudflare_challenge(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in CLOUDFLARE_MARKERS)


def monitor_position(
    previous: int | None,
    current: int | None,
    large_band: int = 100,
    large_drop: int = 50,
    small_drop: int = 10,
    final_position: int = 10,
) -> tuple[bool, bool]:
    """Return (should_notify, is_final). Never fires when the position rises."""
    if previous is None or current is None:
        return False, False

    if current < final_position:
        return True, True
    if current >= large_band:
        threshold = large_drop
    else:
        threshold = small_drop

    if previous - current > threshold:
        return True, False
    return False, False