"""A refresh run in words — one renderer for the bot's ``/update status`` and the job's own
message at the end, so both say the same thing. Pure functions over the status JSON."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

STEP_NAMES = {
    "collect": "сбор",
    "views": "просмотры",
    "ingest": "обогащение",
    "links": "ссылки (кино)",
    "entities": "сущности (кино)",
    "dedup": "дубли",
    "index": "индекс",
    "profiles": "профили",
}
TRIGGERS = {"bot": "из бота", "timer": "по расписанию", "cli": "из терминала"}


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"{int(seconds)} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин"


def _since(iso: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(iso)).total_seconds()


def step_line(name: str, r: dict[str, Any]) -> str:
    title = STEP_NAMES.get(name, name)
    if name == "collect":
        text = f"+{r.get('inserted', 0)} постов из {r.get('channels', 0)} каналов"
        if r.get("errors"):
            text += f", каналов с ошибкой: {r['errors']}"
    elif name == "views":
        text = "пропущено" if r.get("skipped") else f"обновлено {r.get('counters_updated', 0)}"
    elif name == "ingest":
        text = f"{r.get('processed', 0)} из {r.get('selected', '?')}"
        if r.get("failed"):
            text += f", ошибок {r['failed']}"
    elif name == "links":
        text = f"пересказано {r.get('summarized', 0)} из {r.get('selected', 0)}"
    elif name == "entities":
        text = f"с сущностями {r.get('with_entities', 0)} из {r.get('selected', 0)}"
    elif name == "dedup":
        text = f"{r.get('clusters', 0)} кластеров, новых подписей {r.get('signatures', 0)}"
    elif name == "index":
        text = f"+{r.get('indexed', 0)} новых, обновлено {r.get('payload_updated', 0)}"
        if r.get("total"):
            text += f", всего {r['total']}"
    elif name == "profiles":
        text = f"пересобрано {r.get('users', 0)}"
    else:
        text = ", ".join(f"{k}={v}" for k, v in r.items())
    return f"{title}: {text}"


def render(status: dict[str, Any] | None, *, running: bool, now: datetime | None = None) -> str:
    """HTML for Telegram. ``running`` comes from the lock, not from the file."""
    if status is None:
        return "Обновлений корпуса ещё не было."
    now = now or datetime.now(UTC)
    trigger = TRIGGERS.get(status.get("trigger", ""), status.get("trigger", ""))
    state = status.get("state")
    step = STEP_NAMES.get(status.get("step") or "", status.get("step") or "")
    if state == "running" and running:
        head = f"⏳ Идёт обновление корпуса ({trigger}): шаг «{step}», " + _duration(
            _since(status["started_at"], now)
        )
    elif state == "running":
        head = f"⚠️ Обновление ({trigger}) прервано на шаге «{step}»: процесс остановлен"
    elif state == "ok":
        took = _since(status["started_at"], datetime.fromisoformat(status["finished_at"]))
        head = f"✅ Корпус обновлён ({trigger}) за {_duration(took)}"
    else:
        head = f"❌ Обновление ({trigger}) не удалось: {status.get('error') or 'ошибка'}"
    lines = [f"<b>{html.escape(head)}</b>"]
    for s in status.get("steps", []):
        mark = {"ok": "•", "failed": "✗", "running": "…"}.get(s.get("state", ""), "•")
        if s.get("state") == "running" and not s.get("result"):  # no counts reported yet
            line = STEP_NAMES.get(s["name"], s["name"])
        else:
            line = step_line(s["name"], s.get("result") or {})
        if s.get("seconds") is not None:
            line += f" ({_duration(float(s['seconds']))})"
        lines.append(f"{mark} {html.escape(line)}")
    if state != "running" and status.get("finished_at"):
        done = datetime.fromisoformat(status["finished_at"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"<i>завершено {done} UTC</i>")
    return "\n".join(lines)
