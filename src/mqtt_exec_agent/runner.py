"""Pure action-execution logic: no MQTT dependency, stays testable with plain unit tests.

Deliberately the only place subprocess.run is called from, and always with a list-form
argv straight from config -- never shell=True, never any string built from an MQTT
payload. The payload only ever selects *which* already-configured action runs; it can
never contribute to *what* runs.
"""
from __future__ import annotations

import datetime
import subprocess
import time
from dataclasses import dataclass

ACTION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ActionResult:
    success: bool
    returncode: int | None
    timestamp: str
    error: str | None = None


def run_action(command: list[str], timeout: float = ACTION_TIMEOUT_SECONDS) -> ActionResult:
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return ActionResult(success=False, returncode=None, timestamp=timestamp, error="timed out")
    except OSError as exc:
        return ActionResult(success=False, returncode=None, timestamp=timestamp, error=str(exc))

    error = None if proc.returncode == 0 else (proc.stderr or "").strip()[-500:] or None
    return ActionResult(success=proc.returncode == 0, returncode=proc.returncode,
                         timestamp=timestamp, error=error)


class Debouncer:
    """Per-key minimum-interval gate, keyed on a monotonic clock so an NTP adjustment
    can't perturb it -- same reasoning as the sibling ups-shutdown-agent's BatteryTracker."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last_allowed_at: dict[str, float] = {}

    def allow(self, key: str, min_interval_seconds: float) -> bool:
        if min_interval_seconds <= 0:
            return True
        now = self._clock()
        last = self._last_allowed_at.get(key)
        if last is not None and (now - last) < min_interval_seconds:
            return False
        self._last_allowed_at[key] = now
        return True
