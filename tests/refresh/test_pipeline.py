from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tgdigest.refresh import pipeline
from tgdigest.refresh.pipeline import Progress, run_refresh
from tgdigest.refresh.report import render
from tgdigest.refresh.state import RefreshFiles


class Sent:
    def __init__(self) -> None:
        self.messages: list[tuple[list[int], str]] = []

    async def __call__(self, chats: list[int], text: str) -> None:
        self.messages.append((chats, text))


def step(result: dict[str, Any], calls: list[str], name: str):  # type: ignore[no-untyped-def]
    async def fn(progress: Progress) -> dict[str, Any]:
        calls.append(name)
        return result

    return fn


async def test_a_run_records_every_step_and_reports_to_the_requester(tmp_path: Path) -> None:
    files, sent = RefreshFiles(tmp_path), Sent()
    calls: list[str] = []
    steps = [
        ("collect", step({"channels": 28, "inserted": 90}, calls, "collect")),
        ("ingest", step({"selected": 90, "processed": 89, "failed": 1}, calls, "ingest")),
    ]
    status = await run_refresh(files, steps, trigger="bot", requested_by=7, notify=sent)

    assert status.state == "ok" and calls == ["collect", "ingest"] and status.step is None
    stored = files.read_status()
    assert stored is not None and stored["state"] == "ok"
    assert [s["state"] for s in stored["steps"]] == ["ok", "ok"]
    assert stored["steps"][1]["result"]["processed"] == 89
    assert len(files.history.read_text().splitlines()) == 1
    [(chats, text)] = sent.messages
    assert chats == [7] and "Корпус обновлён (из бота)" in text
    assert "сбор: +90 постов из 28 каналов" in text and "обогащение: 89 из 90, ошибок 1" in text


async def test_a_failed_step_stops_the_run_and_a_failed_night_tells_everyone(
    tmp_path: Path,
) -> None:
    files, sent = RefreshFiles(tmp_path), Sent()
    calls: list[str] = []

    async def boom(progress: Progress) -> dict[str, Any]:
        raise RuntimeError("model server down")

    steps = [
        ("collect", step({"inserted": 3}, calls, "collect")),
        ("ingest", boom),
        ("index", step({}, calls, "index")),
    ]
    status = await run_refresh(files, steps, trigger="timer", notify=sent, allowed={9, 4})

    assert status.state == "failed" and calls == ["collect"]  # index never ran
    assert status.error is not None and status.error.startswith("ingest: RuntimeError")
    assert [s.state for s in status.steps] == ["ok", "failed"]
    [(chats, text)] = sent.messages
    assert chats == [4, 9] and "не удалось" in text and "model server down" in text


async def test_a_quiet_night_sends_nothing(tmp_path: Path) -> None:
    sent = Sent()
    await run_refresh(RefreshFiles(tmp_path), [], trigger="timer", notify=sent, allowed={1})
    assert sent.messages == []


async def test_progress_reaches_the_status_file_while_the_step_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "PROGRESS_EVERY_S", 0.0)
    files = RefreshFiles(tmp_path)
    seen: list[dict[str, Any]] = []

    async def ingest(progress: Progress) -> dict[str, Any]:
        progress({"selected": 10, "processed": 4})
        seen.append(json.loads(files.status.read_text()))
        return {"selected": 10, "processed": 10}

    await run_refresh(files, [("ingest", ingest)], trigger="cli")
    [mid] = seen
    assert mid["state"] == "running" and mid["step"] == "ingest"
    assert mid["steps"][0]["result"] == {"selected": 10, "processed": 4}
    assert "обогащение: 4 из 10" in render(mid, running=True)


def test_the_lock_is_the_truth_about_a_run(tmp_path: Path) -> None:
    files = RefreshFiles(tmp_path)
    assert not files.is_running()
    with files.hold_lock() as held:
        assert held and files.is_running()
        with files.hold_lock(wait_s=0) as second:
            assert not second
    assert not files.is_running()


def test_a_bot_request_is_taken_once_and_a_stale_one_is_dropped(tmp_path: Path) -> None:
    files = RefreshFiles(tmp_path)
    files.write_request(364)
    request = files.take_request()
    assert request is not None and request["requested_by"] == 364
    assert files.take_request() is None

    files.write_request(364)
    stale = json.loads(files.request.read_text()) | {"requested_at": time.time() - 3600}
    files.request.write_text(json.dumps(stale))
    assert files.take_request() is None and not files.request.exists()


def test_render_tells_running_interrupted_and_missing_runs_apart() -> None:
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    status = {
        "trigger": "timer",
        "state": "running",
        "step": "index",
        "started_at": (now - timedelta(hours=2, minutes=5)).isoformat(),
        "steps": [{"name": "index", "state": "running", "result": {}}],
    }
    assert "Идёт обновление корпуса (по расписанию): шаг «индекс», 2 ч 05 мин" in render(
        status, running=True, now=now
    )
    assert "прервано на шаге «индекс»" in render(status, running=False, now=now)
    assert render(None, running=False) == "Обновлений корпуса ещё не было."
