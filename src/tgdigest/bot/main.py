"""aiogram 3 bot over the HTTP API (SPEC §6.8).

Commands: text → search; /digest [minutes]; /why <post_id>; /profile; /schedule <hour|off>;
/help. Every 👍/👎/💾 press becomes a feedback row and a Langfuse score through the API.
A background loop sends the daily issue to subscribed chats at their hour (UTC).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
from datetime import UTC, datetime

import httpx
import structlog
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from tgdigest.bot.client import ApiClient
from tgdigest.bot.format import (
    decode_feedback,
    decode_why,
    digest_keyboard,
    render_digest,
    render_profile,
    render_search,
    search_keyboard,
)
from tgdigest.config import Settings

log = structlog.get_logger(__name__)

HELP = (
    "Я ищу по индексу Telegram-каналов о фантастике, кино и юморе и собираю персональный "
    "дайджест.\n\n"
    "• напишите запрос — найду и отвечу с ссылками на посты;\n"
    "• /digest [минут] — свежий выпуск под ваш профиль;\n"
    "• кнопки 👍 👎 💾 под ответами учат профиль, ❓ объясняет, почему пост попал в выпуск;\n"
    "• /why &lt;id поста&gt; — то же объяснение командой;\n"
    "• /profile — что бот о вас знает; /schedule 9 — выпуск каждый день в 9:00 UTC, "
    "/schedule off — выключить."
)


def make_router(api: ApiClient, allowed: set[int]) -> Router:
    router = Router()

    def permitted(m: Message | CallbackQuery) -> bool:
        user = m.from_user
        return not allowed or (user is not None and user.id in allowed)

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message) -> None:
        if not permitted(message):
            return
        await message.answer(HELP)

    @router.message(Command("digest"))
    async def digest(message: Message, command: CommandObject) -> None:
        if not permitted(message):
            return
        minutes = 10
        if command.args and command.args.strip().isdigit():
            minutes = max(3, min(40, int(command.args.strip())))
        note = await message.answer("Собираю выпуск — на CPU это несколько минут…")
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)  # type: ignore[union-attr]
        try:
            body = await api.digest(message.chat.id, minutes=minutes)
        except httpx.HTTPError as exc:
            await note.edit_text(f"Не получилось: {html.escape(repr(exc)[:200])}")
            return
        await note.edit_text(
            render_digest(body),
            reply_markup=digest_keyboard(int(body["digest_id"] or 0), body["items"])
            if body.get("digest_id")
            else None,
            disable_web_page_preview=True,
        )

    @router.message(Command("why"))
    async def why(message: Message, command: CommandObject) -> None:
        if not permitted(message):
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /why &lt;id поста из последнего выпуска&gt;")
            return
        latest = await api.latest_digest(message.chat.id)
        if latest is None:
            await message.answer("Выпусков ещё не было — /digest")
            return
        answer = await api.why(int(latest["digest_id"]), int(command.args.strip()))
        await message.answer(
            html.escape(answer["explanation"]) if answer else "Этого поста нет в последнем выпуске."
        )

    @router.message(Command("profile"))
    async def profile(message: Message) -> None:
        if not permitted(message):
            return
        await message.answer(render_profile(await api.profile(message.chat.id)))

    @router.message(Command("schedule"))
    async def schedule(message: Message, command: CommandObject) -> None:
        if not permitted(message):
            return
        arg = (command.args or "").strip().lower()
        if arg in ("off", "stop", "нет"):
            await api.schedule(message.chat.id, None)
            await message.answer("Ежедневный выпуск выключен.")
        elif arg.isdigit() and 0 <= int(arg) <= 23:
            await api.schedule(message.chat.id, int(arg))
            await message.answer(f"Буду присылать выпуск каждый день в {int(arg):02d}:00 UTC.")
        else:
            await message.answer("Использование: /schedule &lt;час 0–23 UTC&gt; или /schedule off")

    @router.message(F.text & ~F.text.startswith("/"))
    async def search(message: Message) -> None:
        if not permitted(message) or not message.text:
            return
        note = await message.answer("Ищу… на CPU это может занять пару минут.")
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)  # type: ignore[union-attr]
        try:
            body = await api.search(message.text, message.chat.id)
        except httpx.HTTPError as exc:
            await note.edit_text(f"Поиск не удался: {html.escape(repr(exc)[:200])}")
            return
        await note.edit_text(
            render_search(body),
            reply_markup=search_keyboard(body.get("posts", []), body.get("trace_id")),
            disable_web_page_preview=True,
        )

    @router.callback_query(F.data.startswith("fb:"))
    async def on_feedback(query: CallbackQuery) -> None:
        if not permitted(query) or not query.data:
            await query.answer()
            return
        fb = decode_feedback(query.data)
        if fb is None:
            await query.answer("не понял кнопку")
            return
        try:
            await api.feedback(
                query.from_user.id,
                fb.post_id,
                fb.signal,
                context=fb.context,
                digest_id=fb.digest_id,
                trace_id=fb.trace_id,
            )
        except httpx.HTTPError as exc:
            await query.answer(f"не записалось: {repr(exc)[:60]}", show_alert=True)
            return
        await query.answer(
            {"like": "👍 учтено", "dislike": "👎 учтено", "save": "💾 сохранено"}[fb.signal]
        )

    @router.callback_query(F.data.startswith("why:"))
    async def on_why(query: CallbackQuery) -> None:
        if not permitted(query) or not query.data:
            await query.answer()
            return
        ids = decode_why(query.data)
        answer = await api.why(*ids) if ids else None
        if answer is None:
            await query.answer("нет объяснения", show_alert=True)
            return
        await query.answer(answer["explanation"][:200], show_alert=True)

    @router.callback_query(F.data == "noop")
    async def on_noop(query: CallbackQuery) -> None:
        await query.answer()

    return router


async def digest_scheduler(bot: Bot, api: ApiClient, *, interval_s: float = 60.0) -> None:
    """Every minute: for each subscribed chat whose hour has come and who has no issue from
    today yet, build and send one. Restart-safe because the check is against stored issues."""
    while True:
        try:
            now = datetime.now(UTC)
            for row in await api.scheduled_users():
                if int(row["digest_hour"]) != now.hour:
                    continue
                latest = await api.latest_digest(int(row["user_id"]))
                if latest and str(latest.get("created_at", ""))[:10] == now.strftime("%Y-%m-%d"):
                    continue
                body = await api.digest(int(row["user_id"]))
                await bot.send_message(
                    int(row["user_id"]),
                    render_digest(body),
                    reply_markup=digest_keyboard(int(body["digest_id"] or 0), body["items"])
                    if body.get("digest_id")
                    else None,
                    disable_web_page_preview=True,
                )
                log.info(
                    "scheduled_digest_sent", user_id=row["user_id"], digest_id=body.get("digest_id")
                )
        except Exception as exc:  # the loop must survive API hiccups
            log.warning("scheduler_error", error=repr(exc)[:200])
        await asyncio.sleep(interval_s)


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise SystemExit("BOT_TOKEN is not set")
    session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else None
    bot = Bot(
        settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    api = ApiClient(settings.api_url)
    dp = Dispatcher()
    dp.include_router(make_router(api, settings.allowed_user_ids))
    scheduler = asyncio.create_task(digest_scheduler(bot, api))
    log.info("bot_started", api=settings.api_url, proxy=bool(settings.bot_proxy))
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await api.aclose()
        await bot.session.close()


def main() -> None:
    from tgdigest.config import get_settings
    from tgdigest.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_bot(settings))
