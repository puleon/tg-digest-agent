"""Rendering and callback encoding for the bot — pure functions, tested without Telegram.

Callback data is at most 64 bytes: ``fb:<signal>:<post_id>:<ctx>`` where ctx is ``s:<trace>``
for a search answer (trace ids are 32 hex chars), ``d:<digest_id>`` for an issue or ``v:-`` for
the saved list (whose only button is "remove"), and ``why:<digest_id>:<post_id>`` for the
explanation button.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

SIGNAL_EMOJI = {"like": "👍", "dislike": "👎", "save": "💾"}
_CITATION = re.compile(r"\[post (\d+)\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BULLET = re.compile(r"^[ \t]*[*-][ \t]+", re.M)
MAX_MESSAGE = 4000


@dataclass(frozen=True)
class Feedback:
    signal: str
    post_id: int
    context: str  # search | digest | saved
    digest_id: int | None = None
    trace_id: str | None = None


def encode_feedback(fb: Feedback) -> str:
    if fb.context == "digest":
        tail = f"d:{fb.digest_id}"
    elif fb.context == "saved":
        tail = "v:-"
    else:
        tail = f"s:{fb.trace_id or '-'}"
    data = f"fb:{fb.signal[0]}:{fb.post_id}:{tail}"
    assert len(data.encode()) <= 64, data
    return data


def decode_feedback(data: str) -> Feedback | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "fb":
        return None
    signal = {"l": "like", "d": "dislike", "s": "save", "u": "unsave"}.get(parts[1])
    if signal is None or not parts[2].isdigit():
        return None
    post_id = int(parts[2])
    if parts[3] == "d" and parts[4].isdigit():
        return Feedback(signal, post_id, "digest", digest_id=int(parts[4]))
    if parts[3] == "s":
        return Feedback(signal, post_id, "search", trace_id=None if parts[4] == "-" else parts[4])
    if parts[3] == "v":
        return Feedback(signal, post_id, "saved")
    return None


def encode_why(digest_id: int, post_id: int) -> str:
    return f"why:{digest_id}:{post_id}"


def decode_why(data: str) -> tuple[int, int] | None:
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == "why" and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None


def feedback_row(label: str, fb_of: dict[str, Feedback]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=label, callback_data="noop")] + [
        InlineKeyboardButton(text=SIGNAL_EMOJI[s], callback_data=encode_feedback(fb))
        for s, fb in fb_of.items()
    ]


def search_keyboard(
    posts: list[dict[str, Any]], trace_id: str | None
) -> InlineKeyboardMarkup | None:
    rows = []
    for i, p in enumerate(posts[:8], 1):
        fbs = {s: Feedback(s, int(p["post_id"]), "search", trace_id=trace_id) for s in SIGNAL_EMOJI}
        rows.append(feedback_row(f"{i}", fbs))
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def digest_keyboard(digest_id: int, items: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    rows = []
    for i, it in enumerate(items[:12], 1):
        pid = int(it["post_id"])
        fbs = {s: Feedback(s, pid, "digest", digest_id=digest_id) for s in SIGNAL_EMOJI}
        row = feedback_row(f"{i}", fbs)
        row.append(InlineKeyboardButton(text="❓", callback_data=encode_why(digest_id, pid)))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def snippet(text: str | None, limit: int = 60) -> str:
    one_line = " ".join((text or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def source_line(n: int, post: dict[str, Any]) -> str:
    """``N. <the post's first words, linked to the post> — channel, date``. The channel goes
    without ``@``: Telegram turns ``@name`` into a link to the channel by itself, and a list
    of channel links was all the reader could click before."""
    label = html.escape(snippet(post.get("text")) or "пост без текста")
    url = post.get("url")
    head = f'<a href="{html.escape(url)}">{label}</a>' if url else label
    return f"{n}. {head} — {html.escape(str(post.get('channel') or '?'))}, {post.get('date')}"


def render_search(body: dict[str, Any]) -> str:
    """Answer with ``[post N]`` citations turned into numbered links to the posts."""
    posts = {int(p["post_id"]): p for p in body.get("posts", [])}
    order = [int(p["post_id"]) for p in body.get("posts", [])]
    numbers = {pid: i + 1 for i, pid in enumerate(order)}

    def link(m: re.Match[str]) -> str:
        pid = int(m.group(1))
        p = posts.get(pid)
        n = numbers.get(pid)
        if p and p.get("url") and n:
            return f'<a href="{html.escape(p["url"])}">[{n}]</a>'
        return f"[{n}]" if n else f"[post {pid}]"

    text = _CITATION.sub(link, html.escape(body.get("answer", "")))  # escape first, then link
    # the model writes Markdown; the bot sends HTML, where ** and * stay literal
    text = _BULLET.sub("• ", _BOLD.sub(r"<b>\1</b>", text))
    if body.get("posts"):
        text += "\n\n" + "\n".join(
            source_line(numbers[int(p["post_id"])], p) for p in body["posts"][:8]
        )
    meta = f"{body.get('mode')} · {body.get('iterations')} шаг(ов) · {body.get('seconds')} с"
    return (text + f"\n\n<i>{meta}</i>")[:MAX_MESSAGE]


def render_digest(body: dict[str, Any]) -> str:
    lines = []
    if body.get("intro"):
        lines.append(f"<b>{html.escape(body['intro'])}</b>")
    for i, it in enumerate(body.get("items", [])[:12], 1):
        title = html.escape(it.get("title") or f"post {it.get('post_id')}")
        url = html.escape(it.get("url") or "")
        head = f'{i}. <a href="{url}">{title}</a>' if url else f"{i}. {title}"
        lines.append(f"{head}  <i>[{it.get('topic')} · @{it.get('channel')}]</i>")
        if it.get("why"):
            lines.append(f"   {html.escape(it['why'])}")
    if body.get("gaps"):
        lines.append(f"\n<i>не хватило кандидатов: {html.escape(str(body['gaps']))}</i>")
    return "\n".join(lines)[:MAX_MESSAGE] or "Пусто: в окне выпуска нет подходящих постов."


def render_profile(body: dict[str, Any]) -> str:
    if body.get("new"):
        return (
            "Профиля ещё нет. Поставьте несколько 👍/👎 под результатами поиска или выпуском "
            "дайджеста — и он появится."
        )
    tw = body.get("topic_weights") or {}
    topics = ", ".join(f"{t} {w:.2f}" for t, w in sorted(tw.items(), key=lambda kv: -kv[1]))
    neg = ", ".join(body.get("negative_prefs") or []) or "—"
    return (
        f"<b>Профиль</b>\nтемы: {html.escape(topics)}\nоценок: {body.get('votes', 0)}, "
        f"доля лайков: {body.get('like_rate', 0):.2f}\nцентров интересов: "
        f"{body.get('interest_centroids', 0)}\nисключения: {html.escape(neg)}"
    )


def render_saved(body: dict[str, Any]) -> str:
    items = body.get("items") or []
    if not items:
        return (
            "Сохранённых постов нет. Нажмите 💾 под ответом поиска или пунктом выпуска — "
            "пост появится здесь."
        )
    total = int(body.get("total") or len(items))
    head = f"<b>Сохранённое: {total}</b>"
    if total > len(items):
        head += f" <i>(последние {len(items)})</i>"
    lines = [head, *(source_line(i, it) for i, it in enumerate(items, 1))]
    lines.append("\n<i>✕ N убирает пост из списка; на профиль это не влияет.</i>")
    return "\n".join(lines)[:MAX_MESSAGE]


def saved_keyboard(items: list[dict[str, Any]], *, per_row: int = 4) -> InlineKeyboardMarkup | None:
    buttons = [
        InlineKeyboardButton(
            text=f"✕ {i}",
            callback_data=encode_feedback(Feedback("unsave", int(it["post_id"]), "saved")),
        )
        for i, it in enumerate(items, 1)
    ]
    rows = [buttons[k : k + per_row] for k in range(0, len(buttons), per_row)]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
