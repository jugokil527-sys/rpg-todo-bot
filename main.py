"""
main.py — Bot entry point: init, middleware, APScheduler jobs, polling.
"""

import os
import asyncio
import logging
from datetime import date

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import TOKEN, ADMIN_ID
from database import Database
from handlers import router
from utils import (
    escape_md, render_hp_bar, render_xp_bar,
    get_penalty_image_path,
)

# ── Logging ────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Whitelist Middleware ───────────────────────────────

class WhitelistMiddleware(BaseMiddleware):
    def __init__(self, db: Database, admin_id: int):
        self.db = db
        self.admin_id = admin_id

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)
        if user.id == self.admin_id:
            return await handler(event, data)
        if await self.db.is_whitelisted(user.id):
            return await handler(event, data)
        return None

# ── Scheduled Jobs ─────────────────────────────────────

async def morning_alarm_job(bot: Bot, db: Database):
    """07:00-12:00 every 30 min — nudge users with empty task list."""
    try:
        user_ids = await db.get_all_user_ids()
        today = date.today().isoformat()
        for uid in user_ids:
            try:
                tasks = await db.get_tasks_by_date(uid, today)
                if not tasks:
                    await bot.send_message(
                        uid,
                        "☀️ *Доброе утро\\!*\n\n"
                        "📋 Твой список задач на сегодня *пуст*\\.\n"
                        "Пора спланировать день\\! Жми «📋 Задачи» 👇",
                    )
            except Exception:
                logger.exception("Morning alarm error user=%s", uid)
    except Exception:
        logger.exception("Morning alarm job failed")


async def evening_summary_job(bot: Bot, db: Database):
    """21:00 — calculate penalties, update streaks, send summary."""
    try:
        user_ids = await db.get_all_user_ids()
        today = date.today().isoformat()

        for uid in user_ids:
            try:
                await _process_evening(bot, db, uid, today)
            except Exception:
                logger.exception("Evening summary error user=%s", uid)
    except Exception:
        logger.exception("Evening summary job failed")


async def _process_evening(bot: Bot, db: Database, uid: int, today: str):
    tasks = await db.get_tasks_by_date(uid, today)
    if not tasks:
        return

    user = await db.get_user(uid)
    if not user:
        return

    total = len(tasks)
    done = sum(1 for t in tasks if t["completed"])
    failed = [t for t in tasks if not t["completed"] and not t["penalized"]]

    # ── Penalties ──
    total_hp_loss = 0
    total_pts_loss = 0
    penalty_table = {
        "focus":     (20, 5),
        "important": (10, 3),
        "wish":      (0,  2),
    }
    shield = bool(user["shield_active"])

    for t in failed:
        hp_pen, pts_pen = penalty_table.get(t["task_type"], (0, 0))
        if shield and hp_pen > 0:
            hp_pen = 0
            shield = False          # shield is one-use
        total_hp_loss += hp_pen
        total_pts_loss += pts_pen

    new_hp = max(0, user["hp"] - total_hp_loss)
    new_pts = max(0, user["points"] - total_pts_loss)
    shield_now = 0 if user["shield_active"] and total_hp_loss > 0 else user["shield_active"]

    # ── Streak ──
    if done == total:
        new_streak = user["pepper_streak"] + 1
        new_pepper = 1 if new_streak >= 3 else user["pepper_mode"]
    else:
        new_streak = 0
        new_pepper = 0 if not user["pepper_mode"] else user["pepper_mode"]
        # Only reset pepper if it was earned via streak (not potion)
        # Simplification: reset pepper on broken streak
        new_pepper = 0

    await db.update_user(
        uid,
        hp=new_hp,
        points=new_pts,
        shield_active=shield_now,
        pepper_mode=new_pepper,
        pepper_streak=new_streak,
        last_perfect_date=today if done == total else user["last_perfect_date"],
    )
    await db.mark_tasks_penalized(uid, today)

    # ── Build summary message ──
    lines = ["🌙 *Итоги дня*\n"]
    lines.append(f"✅ *Выполнено:* `{done}/{total}`\n")

    if failed:
        lines.append("❌ *Невыполненные:*")
        for t in failed:
            em = {"focus": "🎯", "important": "⚡", "wish": "💫"}.get(t["task_type"], "📌")
            lines.append(f"  • {em} {escape_md(t['title'])}")
        lines.append("")

    if total_hp_loss or total_pts_loss:
        lines.append("💔 *Штрафы:*")
        if total_hp_loss:
            lines.append(f"  ❤️ *\\-{total_hp_loss} HP*")
        if total_pts_loss:
            lines.append(f"  💰 *\\-{total_pts_loss} очков*")
        lines.append("")

    lines.append(f"❤️ *HP:* `{new_hp}/100`")
    lines.append(render_hp_bar(new_hp))

    if new_streak >= 3:
        lines.append(f"\n🌶️ *Режим Перца\\! Стрик {new_streak} дней — x1\\.5\\!*")
    elif done == total:
        lines.append(f"\n🔥 *Стрик:* `{new_streak}` \\(нужно 3 для 🌶️\\)")

    text = "\n".join(lines)

    # Send with penalty image if there were losses
    if total_hp_loss or total_pts_loss:
        img = get_penalty_image_path()
        if os.path.exists(img):
            from aiogram.types import FSInputFile
            await bot.send_photo(uid, photo=FSInputFile(img), caption=text)
        else:
            await bot.send_message(uid, text)
    else:
        await bot.send_message(uid, text)

    logger.info(
        "Evening summary user=%s done=%d/%d hp_loss=%d pts_loss=%d streak=%d",
        uid, done, total, total_hp_loss, total_pts_loss, new_streak,
    )

# ── Restore reminders on startup ───────────────────────

async def restore_reminders(bot: Bot, db: Database, scheduler: AsyncIOScheduler):
    """Re-schedule today's pending reminders after bot restart."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from apscheduler.triggers.date import DateTrigger
    from handlers import _send_reminder

    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    today = now.date().isoformat()
    user_ids = await db.get_all_user_ids()
    count = 0

    for uid in user_ids:
        tasks = await db.get_tasks_by_date(uid, today)
        for t in tasks:
            if t["completed"] or not t["reminder_time"]:
                continue
            try:
                parts = t["reminder_time"].split(":")
                hour, minute = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                continue

            run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_time > now:
                scheduler.add_job(
                    _send_reminder,
                    DateTrigger(run_date=run_time),
                    args=[bot, db, uid, t["id"]],
                    id=f"rem_{t['id']}",
                    replace_existing=True,
                    misfire_grace_time=300,
                )
                count += 1

    logger.info("Restored %d reminder(s) for today", count)


# ── Main ───────────────────────────────────────────────

async def main():
    db = Database()
    await db.init()

    # Ensure admin is whitelisted
    await db.add_to_whitelist(ADMIN_ID)
    await db.create_user(ADMIN_ID, "admin")

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )

    dp = Dispatcher()

    # Inject dependencies
    dp["db"] = db
    dp["bot"] = bot

    # Middleware
    wl_mid = WhitelistMiddleware(db, ADMIN_ID)
    dp.message.middleware(wl_mid)
    dp.callback_query.middleware(wl_mid)

    # Router
    dp.include_router(router)

    # Scheduler (Europe/Moscow)
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Morning alarm: 07:00 – 12:00, every 30 min
    scheduler.add_job(
        morning_alarm_job,
        CronTrigger(hour="7-12", minute="0,30"),
        args=[bot, db],
        id="morning_alarm",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Evening summary: 21:00
    scheduler.add_job(
        evening_summary_job,
        CronTrigger(hour=21, minute=0),
        args=[bot, db],
        id="evening_summary",
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.start()

    # Store scheduler in dispatcher for handler access
    dp["scheduler"] = scheduler

    # Restore today's pending reminders (survive bot restarts)
    await restore_reminders(bot, db, scheduler)

    logger.info("Bot starting...")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
