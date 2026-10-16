"""Files a refresh run shares with the API: status, lock, one-shot request, history.

They live under ``<data_dir>/refresh/``; the API and the job share the working directory, so
the API reads a run's progress without talking to the process doing it. The lock is the source
of truth for "is a run going": a run killed mid-way leaves ``state=running`` in the status
file, but no lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UNIT = "tgdigest-refresh.service"


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class StepStatus:
    name: str
    state: str = "running"
    """running | ok | failed"""
    started_at: str = ""
    finished_at: str | None = None
    seconds: float | None = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefreshStatus:
    run_id: str
    trigger: str
    """bot | timer | cli"""
    requested_by: int | None = None
    state: str = "running"
    """running | ok | failed"""
    started_at: str = ""
    finished_at: str | None = None
    step: str | None = None
    steps: list[StepStatus] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class RefreshFiles:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "refresh"
        self.status = self.root / "status.json"
        self.lock = self.root / "lock"
        self.request = self.root / "request.json"
        self.history = self.root / "history.jsonl"

    def read_status(self) -> dict[str, Any] | None:
        try:
            return dict(json.loads(self.status.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def write_status(self, status: RefreshStatus) -> None:
        """Atomic: a reader never sees half a file."""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.status.with_suffix(".tmp")
        tmp.write_text(json.dumps(status.to_json(), ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.status)

    def append_history(self, status: RefreshStatus) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.history.open("a", encoding="utf-8") as f:
            f.write(json.dumps(status.to_json(), ensure_ascii=False) + "\n")

    @contextlib.contextmanager
    def hold_lock(self, *, wait_s: float = 10.0) -> Iterator[bool]:
        """Yields whether this process holds the run lock. Waits a little: the API's
        ``is_running`` probe takes the lock for an instant right before it starts the unit."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a+") as fh:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.5)
            try:
                yield True
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def is_running(self) -> bool:
        if not self.lock.exists():
            return False
        with self.lock.open("a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False

    def vision_pass(self) -> dict[str, Any] | None:
        """The ingest step's counts while a running refresh reads images with the vision model —
        the text model is unloaded then, and a request for it would make the shared router evict
        a model on its own (likely the assistant's: Gemma is busy, the assistant may be idle)."""
        status = self.read_status()
        if not status or status.get("state") != "running" or status.get("step") != "ingest":
            return None
        result = (status.get("steps") or [{}])[-1].get("result") or {}
        vision = result.get("phase") == "vision" or ("posts" in result and "selected" not in result)
        return dict(result) if vision and self.is_running() else None

    def write_request(self, requested_by: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"requested_by": requested_by, "requested_at": time.time()}
        self.request.write_text(json.dumps(payload), encoding="utf-8")

    def take_request(self, *, max_age_s: float = 600.0) -> dict[str, Any] | None:
        """The bot's request, consumed once; a stale one (the unit never started) is dropped."""
        try:
            payload = dict(json.loads(self.request.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        finally:
            self.request.unlink(missing_ok=True)
        if time.time() - float(payload.get("requested_at", 0)) > max_age_s:
            return None
        return payload
