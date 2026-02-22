"""
handlers.py — All Aiogram 3.x handlers: commands, FSM, inline keyboards, callbacks.
"""

import os
import logging
from datetime import date, datetime, timedelta

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_ID
from database import Database
from utils import escape_md, render_hp_bar, render_xp_bar, get_profile_image_path, parse_time

logger = logging.getLogger(__name__)
router = Router()

# Set by main.py at startup — reliable alternative to Aiogram DI
_scheduler = None
_bot_ref = None

# ═══════════════════ FSM States ═══════════════════

class TaskForm(StatesGroup):
    name = State()
    task_type = State()
    reminder_time = State()

class RewardForm(StatesGroup):
    name = State()
    cost = State()

class AddUserForm(StatesGroup):
    user_id = State()

class CategoryForm(StatesGroup):
    name = State()

class IdeaForm(StatesGroup):
    title = State()

# ═══════════════════ Keyboards ═══════════════════

TASK_EMOJIS = {"focus": "🎯", "important": "⚡", "wish": "💫"}
TASK_NAMES = {"focus": "Focus", "important": "Important", "wish": "Wish"}

# Texts of main reply‐keyboard buttons (used to detect menu presses during FSM)
MENU_BUTTONS = {"📋 Задачи", "🧙 Профиль", "🛒 Магазин", "🎁 Награды", "💡 Идеи", "👥 Юзеры"}


async def _cancel_if_menu(message: Message, state: FSMContext) -> bool:
    """If the user pressed a main-menu button while inside FSM, cancel the state."""
    if message.text in MENU_BUTTONS:
        await state.clear()
        await message.answer("❌ *Действие отменено\\.*\nНажмите кнопку ещё раз\\.")
        return True
    return False

def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📋 Задачи"), KeyboardButton(text="🧙 Профиль")],
        [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="🎁 Награды")],
        [KeyboardButton(text="💡 Идеи")],
    ]
    if user_id == ADMIN_ID:
        rows.append([KeyboardButton(text="👥 Юзеры")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def task_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Focus (+50 XP, +20 Очков)", callback_data="ttype:focus")],
        [InlineKeyboardButton(text="⚡ Important (+20 XP, +10 Очков)", callback_data="ttype:important")],
        [InlineKeyboardButton(text="💫 Wish (+5 XP, +2 Очка, +5 HP)", callback_data="ttype:wish")],
    ])


def tasks_kb(tasks: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for t in tasks:
        emoji = TASK_EMOJIS.get(t["task_type"], "📌")
        if t["completed"]:
            rows.append([InlineKeyboardButton(
                text=f"✅ {emoji} {t['title']}", callback_data=f"tinfo:{t['id']}"
            )])
        else:
            rows.append([
                InlineKeyboardButton(text=f"⬜ {emoji} {t['title']}", callback_data=f"tinfo:{t['id']}"),
                InlineKeyboardButton(text="✅", callback_data=f"tdone:{t['id']}"),
                InlineKeyboardButton(text="🗑", callback_data=f"tdel:{t['id']}"),
            ])
    rows.append([InlineKeyboardButton(text="➕ Добавить задачу", callback_data="tadd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ Щит — 50 очков", callback_data="buy:shield")],
        [InlineKeyboardButton(text="🌶️ Зелье Перца — 100 очков", callback_data="buy:pepper")],
    ])


def rewards_kb(rewards: list[dict], can_claim: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for r in rewards:
        row = [InlineKeyboardButton(
            text=f"🎁 {r['title']} ({r['cost']} очк.)", callback_data=f"rinfo:{r['id']}"
        )]
        if can_claim:
            row.append(InlineKeyboardButton(text="🎁 Забрать", callback_data=f"rclaim:{r['id']}"))
        row.append(InlineKeyboardButton(text="🗑", callback_data=f"rdel:{r['id']}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="➕ Добавить награду", callback_data="radd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminder_buttons(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"remdone:{task_id}"),
        InlineKeyboardButton(text="🔕 Ок", callback_data=f"remok:{task_id}"),
    ]])


def users_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data="ulist")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="uadd")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data="urem")],
    ])

# ═══════════════════ /cancel — exit any FSM state ═══════════════════

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    if cur is None:
        await message.answer("🤷 *Нечего отменять\\.*")
    else:
        await state.clear()
        await message.answer("❌ *Действие отменено\\.*", reply_markup=main_kb(message.from_user.id))


@router.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("❌ *Действие отменено\\.*", reply_markup=main_kb(cb.from_user.id))
    await cb.answer()


@router.message(Command("testrem"))
async def cmd_test_reminder(message: Message, db: Database, bot: Bot):
    """Admin-only: schedule a test reminder in 30 seconds."""
    if message.from_user.id != ADMIN_ID:
        return
    if not _scheduler:
        await message.answer("❌ *Scheduler не инициализирован\\!*")
        return
    try:
        from apscheduler.triggers.date import DateTrigger
        run_time = datetime.now() + timedelta(seconds=30)
        _scheduler.add_job(
            _send_test_reminder,
            DateTrigger(run_date=run_time),
            args=[bot, message.from_user.id],
            id="test_reminder",
            replace_existing=True,
        )
        await message.answer(f"✅ *Тестовое напоминание запланировано\\!*\nПридёт через 30 секунд\\.")
        logger.info("Test reminder scheduled for %s", run_time)
    except Exception as e:
        logger.exception("Test reminder failed")
        await message.answer(f"❌ *Ошибка:* `{escape_md(str(e))}`")

# ═══════════════════ /start ═══════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, db: Database):
    await db.create_user(message.from_user.id, message.from_user.username or "")
    text = (
        "🎮 *Добро пожаловать в RPG Планировщик\\!*\n\n"
        "Ты — герой своей жизни\\. Выполняй задачи, "
        "зарабатывай *XP* и *очки*, прокачивай уровень\\!\n\n"
        "Используй кнопки ниже 👇"
    )
    await message.answer(text, reply_markup=main_kb(message.from_user.id))

# ═══════════════════ Profile ═══════════════════

@router.message(F.text == "🧙 Профиль")
async def show_profile(message: Message, db: Database):
    user = await _ensure_user(message.from_user.id, message.from_user.username, db)
    lvl = user["level"]
    xp, xp_need = user["xp"], lvl * 100
    hp, pts = user["hp"], user["points"]
    streak = user["pepper_streak"]
    items = []
    if user["shield_active"]:
        items.append("🛡️ Щит")
    if user["pepper_mode"]:
        items.append("🌶️ Перец")
    items_txt = ", ".join(items) if items else "нет"

    text = (
        f"🧙 *Профиль героя*\n\n"
        f"📊 *Уровень:* `{lvl}`\n"
        f"⚔️ *XP:* `{xp}/{xp_need}`\n"
        f"{render_xp_bar(xp, xp_need)}\n"
        f"❤️ *HP:* `{hp}/100`\n"
        f"{render_hp_bar(hp)}\n"
        f"💰 *Очки:* `{pts}`\n"
        f"🔥 *Стрик:* `{streak}` дн\\.\n"
        f"🎒 *Предметы:* {escape_md(items_txt)}"
    )
    if user["pepper_mode"]:
        text += "\n\n🌶️ *Режим Перца активен\\! x1\\.5 награды*"

    img = get_profile_image_path(hp, bool(user["pepper_mode"]))
    if os.path.exists(img):
        await message.answer_photo(photo=FSInputFile(img), caption=text)
    else:
        await message.answer(text)

# ═══════════════════ Tasks — list ═══════════════════

@router.message(F.text == "📋 Задачи")
async def show_tasks(message: Message, db: Database):
    today = date.today().isoformat()
    tasks = await db.get_tasks_by_date(message.from_user.id, today)

    if not tasks:
        text = "📋 *Задачи на сегодня*\n\n_Список пуст\\. Добавьте задачу\\!_"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="tadd")]
        ])
    else:
        done = sum(1 for t in tasks if t["completed"])
        lines = ["📋 *Задачи на сегодня*\n"]
        for i, t in enumerate(tasks, 1):
            em = TASK_EMOJIS.get(t["task_type"], "📌")
            tn = TASK_NAMES.get(t["task_type"], "")
            title_esc = escape_md(t["title"])
            if t["completed"]:
                lines.append(f"{i}\\. ✅ ~{em} *{tn}*: {title_esc}~")
            else:
                lines.append(f"{i}\\. ⬜ {em} *{tn}*: {title_esc}")
            if t["reminder_time"]:
                lines.append(f"   ⏰ {escape_md(t['reminder_time'])}")
        lines.append(f"\n▫️ *Выполнено:* `{done}/{len(tasks)}`")
        text = "\n".join(lines)
        kb = tasks_kb(tasks)

    await message.answer(text, reply_markup=kb)

# ═══════════════════ Tasks — add (FSM) ═══════════════════

@router.callback_query(F.data == "tadd")
async def task_add_start(cb: CallbackQuery, state: FSMContext):
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer("✏️ *Введите название задачи:*", reply_markup=cancel_kb)
    await state.set_state(TaskForm.name)
    await cb.answer()


@router.message(TaskForm.name)
async def task_add_name(message: Message, state: FSMContext):
    if await _cancel_if_menu(message, state):
        return
    await state.update_data(name=message.text)
    await message.answer("🎯 *Выберите тип задачи:*", reply_markup=task_type_kb())
    await state.set_state(TaskForm.task_type)


@router.callback_query(TaskForm.task_type, F.data.startswith("ttype:"))
async def task_add_type(cb: CallbackQuery, state: FSMContext):
    await state.update_data(task_type=cb.data.split(":")[1])
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_rem"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer(
        "⏰ *Введите время напоминания* \\(16:00 или 16\\.00\\)\nили нажмите пропустить:",
        reply_markup=skip_kb,
    )
    await state.set_state(TaskForm.reminder_time)
    await cb.answer()


@router.callback_query(TaskForm.reminder_time, F.data == "skip_rem")
async def task_skip_reminder(cb: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    today = date.today().isoformat()
    await db.add_task(cb.from_user.id, data["name"], data["task_type"], None, today)
    await state.clear()
    em = TASK_EMOJIS.get(data["task_type"], "📌")
    tn = TASK_NAMES.get(data["task_type"], "")
    await cb.message.answer(
        f"✅ *Задача добавлена\\!*\n\n{em} *{tn}*: {escape_md(data['name'])}"
    )
    await cb.answer()
    logger.info("Task added: %s (%s) user=%s", data["name"], data["task_type"], cb.from_user.id)


@router.message(TaskForm.reminder_time)
async def task_add_reminder(message: Message, state: FSMContext, db: Database, bot: Bot):
    if await _cancel_if_menu(message, state):
        return
    parsed = parse_time(message.text)
    if parsed is None:
        await message.answer("❌ *Неверный формат\\!* Пример: `14:30` или `14\\.30`")
        return

    hour, minute = parsed
    data = await state.get_data()
    today = date.today().isoformat()
    rem_str = f"{hour:02d}:{minute:02d}"
    task_id = await db.add_task(
        message.from_user.id, data["name"], data["task_type"], rem_str, today
    )
    await state.clear()

    # schedule one-time reminder via APScheduler
    if _scheduler:
        try:
            from apscheduler.triggers.date import DateTrigger
            import pytz

            tz = pytz.timezone("Europe/Moscow")
            now = datetime.now(tz)
            run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Only schedule if the time hasn't passed yet today
            if run_time > now:
                _scheduler.add_job(
                    _send_reminder,
                    DateTrigger(run_date=run_time),
                    args=[_bot_ref or bot, db, message.from_user.id, task_id],
                    id=f"rem_{task_id}",
                    replace_existing=True,
                    misfire_grace_time=300,
                )
                logger.info("Reminder scheduled: task=%s at %s", task_id, run_time)
            else:
                logger.info("Reminder time already passed for task=%s (%s), skipping", task_id, rem_str)
        except Exception as e:
            logger.exception("Failed to schedule reminder for task=%s: %s", task_id, e)
    else:
        logger.warning("Scheduler not available (_scheduler is None)! Reminder for task=%s will NOT fire.", task_id)

    em = TASK_EMOJIS.get(data["task_type"], "📌")
    tn = TASK_NAMES.get(data["task_type"], "")
    await message.answer(
        f"✅ *Задача добавлена\\!*\n\n"
        f"{em} *{tn}*: {escape_md(data['name'])}\n"
        f"⏰ Напоминание: `{rem_str}`"
    )
    logger.info("Task+reminder: %s at %s user=%s", data["name"], rem_str, message.from_user.id)

# ═══════════════════ Tasks — complete / delete ═══════════════════

@router.callback_query(F.data.startswith("tdone:"))
async def task_done(cb: CallbackQuery, db: Database):
    task_id = int(cb.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task or task["completed"]:
        await cb.answer("Задача уже выполнена или не найдена!")
        return

    user = await db.get_user(cb.from_user.id)
    xp_g, pts_g, hp_g = _calc_rewards(task["task_type"], user["pepper_mode"])

    new_xp = user["xp"] + xp_g
    new_lvl = user["level"]
    while new_xp >= new_lvl * 100:
        new_xp -= new_lvl * 100
        new_lvl += 1

    new_hp = min(100, user["hp"] + hp_g)
    new_pts = user["points"] + pts_g

    await db.complete_task(task_id)
    await db.update_user(cb.from_user.id, xp=new_xp, level=new_lvl, hp=new_hp, points=new_pts)

    pepper_tag = " 🌶️ *x1\\.5*" if user["pepper_mode"] else ""
    text = (
        f"✅ *Задача выполнена\\!*{pepper_tag}\n\n"
        f"📝 {escape_md(task['title'])}\n"
        f"⚔️ *\\+{xp_g} XP*  💰 *\\+{pts_g} очков*"
    )
    if hp_g:
        text += f"  ❤️ *\\+{hp_g} HP*"
    if new_lvl > user["level"]:
        text += f"\n\n🎉 *LEVEL UP\\! Уровень {new_lvl}\\!*"

    await cb.message.answer(text)
    await cb.answer("✅ Выполнено!")
    logger.info("Task done id=%s user=%s +%dXP +%dpts", task_id, cb.from_user.id, xp_g, pts_g)


@router.callback_query(F.data.startswith("tdel:"))
async def task_delete(cb: CallbackQuery, db: Database):
    task_id = int(cb.data.split(":")[1])
    task = await db.get_task(task_id)
    if task:
        await db.delete_task(task_id)
        await cb.message.answer(f"🗑 Задача удалена: {escape_md(task['title'])}")
        logger.info("Task deleted id=%s user=%s", task_id, cb.from_user.id)
    await cb.answer()

# ═══════════════════ Shop ═══════════════════

@router.message(F.text == "🛒 Магазин")
async def show_shop(message: Message, db: Database):
    user = await _ensure_user(message.from_user.id, message.from_user.username, db)
    text = (
        f"🛒 *Магазин*\n\n"
        f"💰 *Баланс:* `{user['points']}` очков\n\n"
        f"🛡️ *Щит* — *50* очков\n"
        f"_Защищает от потери HP при провале \\(одноразовый\\)_\n\n"
        f"🌶️ *Зелье Перца* — *100* очков\n"
        f"_Активирует режим x1\\.5 наград_"
    )
    await message.answer(text, reply_markup=shop_kb())


@router.callback_query(F.data.startswith("buy:"))
async def shop_buy(cb: CallbackQuery, db: Database):
    item = cb.data.split(":")[1]
    user = await db.get_user(cb.from_user.id)
    prices = {"shield": 50, "pepper": 100}
    price = prices.get(item, 0)

    if user["points"] < price:
        await cb.answer(
            f"❌ Недостаточно очков! Нужно {price}, у вас {user['points']}", show_alert=True
        )
        return

    new_pts = user["points"] - price
    if item == "shield":
        await db.update_user(cb.from_user.id, points=new_pts, shield_active=1)
        await cb.message.answer(
            f"🛡️ *Щит активирован\\!*\n💰 *\\-{price}* очков \\(осталось: *{new_pts}*\\)"
        )
        logger.info("Shield bought user=%s", cb.from_user.id)
    elif item == "pepper":
        await db.update_user(cb.from_user.id, points=new_pts, pepper_mode=1)
        await cb.message.answer(
            f"🌶️ *Зелье Перца выпито\\!*\n💰 *\\-{price}* очков \\(осталось: *{new_pts}*\\)\n"
            f"_Режим x1\\.5 наград активирован\\!_"
        )
        logger.info("Pepper potion bought user=%s", cb.from_user.id)
    await cb.answer("✅ Куплено!")

# ═══════════════════ Rewards ═══════════════════

@router.message(F.text == "🎁 Награды")
async def show_rewards(message: Message, db: Database):
    rlist = await db.get_rewards(message.from_user.id)
    today = date.today()
    is_sun = today.weekday() == 6
    rate = await db.get_week_completion_rate(message.from_user.id)
    can_claim = is_sun and rate > 80

    if not is_sun:
        status = "_Забрать награды можно только в воскресенье_"
    elif rate <= 80:
        status = f"_Выполнение за неделю: {escape_md(f'{rate:.0f}')}% \\(нужно \\> 80%\\)_"
    else:
        status = f"✅ _Можно забирать\\! Выполнение: {escape_md(f'{rate:.0f}')}%_"

    text = f"🎁 *Награды*\n\n{status}"
    if not rlist:
        text += "\n\n_Список пуст\\. Добавьте награду\\!_"
    await message.answer(text, reply_markup=rewards_kb(rlist, can_claim))


@router.callback_query(F.data == "radd")
async def reward_add_start(cb: CallbackQuery, state: FSMContext):
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer("🎁 *Введите название награды:*", reply_markup=cancel_kb)
    await state.set_state(RewardForm.name)
    await cb.answer()


@router.message(RewardForm.name)
async def reward_add_name(msg: Message, state: FSMContext):
    if await _cancel_if_menu(msg, state):
        return
    await state.update_data(name=msg.text)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await msg.answer("💰 *Введите стоимость в очках:*", reply_markup=cancel_kb)
    await state.set_state(RewardForm.cost)


@router.message(RewardForm.cost)
async def reward_add_cost(msg: Message, state: FSMContext, db: Database):
    if await _cancel_if_menu(msg, state):
        return
    try:
        cost = int(msg.text)
        if cost <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ *Введите положительное число\\!*")
        return
    data = await state.get_data()
    await db.add_reward(msg.from_user.id, data["name"], cost)
    await state.clear()
    await msg.answer(f"✅ *Награда добавлена\\!*\n🎁 {escape_md(data['name'])} — *{cost}* очков")
    logger.info("Reward added: %s (%d pts) user=%s", data["name"], cost, msg.from_user.id)


@router.callback_query(F.data.startswith("rclaim:"))
async def reward_claim(cb: CallbackQuery, db: Database):
    rid = int(cb.data.split(":")[1])
    reward = await db.get_reward(rid)
    if not reward:
        await cb.answer("Награда не найдена!")
        return
    user = await db.get_user(cb.from_user.id)
    today = date.today()
    is_sun = today.weekday() == 6
    rate = await db.get_week_completion_rate(cb.from_user.id)
    if not is_sun or rate <= 80:
        await cb.answer("❌ Условия не выполнены!", show_alert=True)
        return
    if user["points"] < reward["cost"]:
        await cb.answer(f"❌ Недостаточно очков! Нужно {reward['cost']}", show_alert=True)
        return
    new_pts = user["points"] - reward["cost"]
    await db.update_user(cb.from_user.id, points=new_pts)
    await db.claim_reward(rid)
    await cb.message.answer(
        f"🎉 *Награда получена\\!*\n🎁 {escape_md(reward['title'])}\n"
        f"💰 *\\-{reward['cost']}* очков"
    )
    await cb.answer("🎉 Получено!")
    logger.info("Reward claimed: %s user=%s", reward["title"], cb.from_user.id)


@router.callback_query(F.data.startswith("rdel:"))
async def reward_del(cb: CallbackQuery, db: Database):
    rid = int(cb.data.split(":")[1])
    await db.delete_reward(rid)
    await cb.message.answer("🗑 Награда удалена")
    await cb.answer()

# ═══════════════════ Reminder callbacks ═══════════════════

@router.callback_query(F.data.startswith("remdone:"))
async def reminder_done(cb: CallbackQuery, db: Database):
    task_id = int(cb.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task or task["completed"]:
        await cb.answer("Уже выполнено!")
        return
    user = await db.get_user(cb.from_user.id)
    xp_g, pts_g, hp_g = _calc_rewards(task["task_type"], user["pepper_mode"])
    new_xp = user["xp"] + xp_g
    new_lvl = user["level"]
    while new_xp >= new_lvl * 100:
        new_xp -= new_lvl * 100
        new_lvl += 1
    new_hp = min(100, user["hp"] + hp_g)
    new_pts = user["points"] + pts_g
    await db.complete_task(task_id)
    await db.update_user(cb.from_user.id, xp=new_xp, level=new_lvl, hp=new_hp, points=new_pts)
    await cb.message.answer(
        f"✅ *Выполнено\\!* \\+*{xp_g}* XP, \\+*{pts_g}* очков"
    )
    await cb.answer("✅")
    logger.info("Reminder done task=%s user=%s", task_id, cb.from_user.id)


@router.callback_query(F.data.startswith("remok:"))
async def reminder_ok(cb: CallbackQuery):
    await cb.answer("🔕 Ок")
    await cb.message.delete()

# ═══════════════════ Users (Admin) ═══════════════════

@router.message(F.text == "👥 Юзеры")
async def show_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👥 *Управление юзерами*", reply_markup=users_kb())


@router.callback_query(F.data == "ulist")
async def users_list(cb: CallbackQuery, db: Database):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("❌ Нет доступа!")
        return
    wl = await db.get_whitelist()
    if not wl:
        text = "👥 *Whitelist*\n\n_Пусто_"
    else:
        lines = ["👥 *Whitelist*\n"]
        for u in wl:
            lines.append(f"• `{u['user_id']}`")
        text = "\n".join(lines)
    await cb.message.answer(text)
    await cb.answer()


@router.callback_query(F.data == "uadd")
async def users_add_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("❌ Нет доступа!")
        return
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer("👤 *Введите ID пользователя:*", reply_markup=cancel_kb)
    await state.set_state(AddUserForm.user_id)
    await cb.answer()


@router.message(AddUserForm.user_id)
async def users_add_id(msg: Message, state: FSMContext, db: Database):
    if msg.from_user.id != ADMIN_ID:
        await state.clear()
        return
    if await _cancel_if_menu(msg, state):
        return
    try:
        uid = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ *Введите числовой ID\\!*")
        return
    await db.add_to_whitelist(uid)
    await db.create_user(uid)
    await state.clear()
    await msg.answer(f"✅ Пользователь `{uid}` добавлен")
    logger.info("Whitelist add: %s by admin", uid)


@router.callback_query(F.data == "urem")
async def users_rem_start(cb: CallbackQuery, db: Database):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("❌ Нет доступа!")
        return
    wl = await db.get_whitelist()
    if not wl:
        await cb.message.answer("_Whitelist пуст_")
        await cb.answer()
        return
    rows = []
    for u in wl:
        rows.append([InlineKeyboardButton(
            text=f"❌ {u['user_id']}", callback_data=f"udel:{u['user_id']}"
        )])
    await cb.message.answer("Выберите юзера для удаления:",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("udel:"))
async def users_del(cb: CallbackQuery, db: Database):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("❌")
        return
    uid = int(cb.data.split(":")[1])
    await db.remove_from_whitelist(uid)
    await cb.message.answer(f"✅ Пользователь `{uid}` удалён")
    await cb.answer()
    logger.info("Whitelist remove: %s by admin", uid)

# ═══════════════════ Helpers ═══════════════════

async def _ensure_user(user_id: int, username: str, db: Database) -> dict:
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username or "")
        user = await db.get_user(user_id)
    return user


def _calc_rewards(task_type: str, pepper: int) -> tuple[int, int, int]:
    """Return (xp, points, hp_heal) for completing a task."""
    table = {
        "focus":     (50, 20, 0),
        "important": (20, 10, 0),
        "wish":      (5,  2,  5),
    }
    xp, pts, hp = table.get(task_type, (0, 0, 0))
    mult = 1.5 if pepper else 1.0
    return int(xp * mult), int(pts * mult), hp


async def _send_reminder(bot: Bot, db: Database, user_id: int, task_id: int):
    """Called by APScheduler to send a task reminder."""
    logger.info("_send_reminder FIRED for task=%s user=%s", task_id, user_id)
    try:
        task = await db.get_task(task_id)
        if not task or task["completed"]:
            logger.info("Reminder skipped (completed/missing) task=%s", task_id)
            return
        em = TASK_EMOJIS.get(task["task_type"], "📌")
        tn = TASK_NAMES.get(task["task_type"], "")
        text = (
            f"⏰ *Напоминание\\!*\n\n"
            f"{em} *{tn}*: {escape_md(task['title'])}"
        )
        await bot.send_message(user_id, text, reply_markup=reminder_buttons(task_id))
        logger.info("Reminder SENT for task=%s user=%s", task_id, user_id)
    except Exception:
        logger.exception("Reminder error task=%s user=%s", task_id, user_id)


async def _send_test_reminder(bot: Bot, user_id: int):
    """Send a test reminder to verify scheduler works."""
    logger.info("Test reminder FIRED for user=%s", user_id)
    try:
        await bot.send_message(user_id, "✅ *Тестовое напоминание\\!*\nЕсли ты это видишь \\\u2014 шедулер работает 🎉")
        logger.info("Test reminder SENT to user=%s", user_id)
    except Exception:
        logger.exception("Test reminder error user=%s", user_id)

# ═══════════════════ Ideas ═══════════════════

STATUS_EMOJI = {"new": "🔵", "wip": "🟡", "done": "🟢"}
STATUS_LABEL = {"new": "Новая", "wip": "В работе", "done": "Готово"}
STATUS_CYCLE = {"new": "wip", "wip": "done", "done": "new"}


@router.message(F.text == "💡 Идеи")
async def show_ideas_menu(message: Message, db: Database):
    cats = await db.get_categories(message.from_user.id)
    rows: list[list[InlineKeyboardButton]] = []
    for c in cats:
        cnt = await db.count_ideas_in_category(c["id"])
        rows.append([InlineKeyboardButton(
            text=f"{c['emoji']} {c['name']} ({cnt})",
            callback_data=f"icat:{c['id']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="icatadd")])
    text = "💡 *Идеи*\n\n_Выбери категорию или создай новую:_"
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ── Category: add / delete ──

@router.callback_query(F.data == "icatadd")
async def cat_add_start(cb: CallbackQuery, state: FSMContext):
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer("📂 *Введите название категории:*", reply_markup=cancel_kb)
    await state.set_state(CategoryForm.name)
    await cb.answer()


@router.message(CategoryForm.name)
async def cat_add_name(msg: Message, state: FSMContext, db: Database):
    if await _cancel_if_menu(msg, state):
        return
    name = msg.text.strip()
    if not name:
        await msg.answer("❌ *Название не может быть пустым\\!*")
        return
    await db.add_category(msg.from_user.id, name)
    await state.clear()
    await msg.answer(f"✅ Категория *{escape_md(name)}* создана\\!")
    logger.info("Category added: %s user=%s", name, msg.from_user.id)


@router.callback_query(F.data.startswith("icatdel:"))
async def cat_delete(cb: CallbackQuery, db: Database):
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if cat:
        await db.delete_category(cat_id)
        await cb.message.answer(f"🗑 Категория *{escape_md(cat['name'])}* удалена")
        logger.info("Category deleted: %s user=%s", cat["name"], cb.from_user.id)
    await cb.answer()


# ── Ideas inside a category ──

@router.callback_query(F.data.startswith("icat:"))
async def show_category_ideas(cb: CallbackQuery, db: Database):
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if not cat:
        await cb.answer("Категория не найдена!")
        return
    ideas = await db.get_ideas_by_category(cat_id)
    rows: list[list[InlineKeyboardButton]] = []
    if ideas:
        for idea in ideas:
            se = STATUS_EMOJI.get(idea["status"], "🔵")
            rows.append([
                InlineKeyboardButton(
                    text=f"{se} {idea['title']}",
                    callback_data=f"istatus:{idea['id']}",
                ),
                InlineKeyboardButton(text="🗑", callback_data=f"idel:{idea['id']}:{cat_id}"),
            ])
    rows.append([InlineKeyboardButton(text="➕ Добавить идею", callback_data=f"iadd:{cat_id}")])
    rows.append([
        InlineKeyboardButton(text="🗑 Удалить категорию", callback_data=f"icatdel:{cat_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="iback"),
    ])
    status_legend = "🔵 Новая  🟡 В работе  🟢 Готово"
    header = f"{cat['emoji']} *{escape_md(cat['name'])}*\n\n"
    if ideas:
        header += f"_{escape_md(status_legend)}_\n_Нажми на идею чтобы сменить статус_"
    else:
        header += "_Пусто\\. Добавь первую идею\\!_"
    await cb.message.answer(header, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data == "iback")
async def ideas_back(cb: CallbackQuery, db: Database):
    cats = await db.get_categories(cb.from_user.id)
    rows: list[list[InlineKeyboardButton]] = []
    for c in cats:
        cnt = await db.count_ideas_in_category(c["id"])
        rows.append([InlineKeyboardButton(
            text=f"{c['emoji']} {c['name']} ({cnt})",
            callback_data=f"icat:{c['id']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="icatadd")])
    await cb.message.answer(
        "💡 *Идеи*\n\n_Выбери категорию:_",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


# ── Idea: add ──

@router.callback_query(F.data.startswith("iadd:"))
async def idea_add_start(cb: CallbackQuery, state: FSMContext):
    cat_id = int(cb.data.split(":")[1])
    await state.update_data(cat_id=cat_id)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await cb.message.answer("✏️ *Введите тему идеи:*", reply_markup=cancel_kb)
    await state.set_state(IdeaForm.title)
    await cb.answer()


@router.message(IdeaForm.title)
async def idea_add_title(msg: Message, state: FSMContext, db: Database):
    if await _cancel_if_menu(msg, state):
        return
    data = await state.get_data()
    cat_id = data["cat_id"]
    title = msg.text.strip()
    if not title:
        await msg.answer("❌ *Тема не может быть пустой\\!*")
        return
    await db.add_idea(msg.from_user.id, cat_id, title)
    await state.clear()
    await msg.answer(f"✅ *Идея сохранена\\!*\n🔵 {escape_md(title)}")
    logger.info("Idea added: %s cat=%s user=%s", title, cat_id, msg.from_user.id)


# ── Idea: cycle status ──

@router.callback_query(F.data.startswith("istatus:"))
async def idea_cycle_status(cb: CallbackQuery, db: Database):
    idea_id = int(cb.data.split(":")[1])
    idea = await db.get_idea(idea_id)
    if not idea:
        await cb.answer("Идея не найдена!")
        return
    new_status = STATUS_CYCLE.get(idea["status"], "new")
    await db.update_idea_status(idea_id, new_status)
    se = STATUS_EMOJI[new_status]
    sl = STATUS_LABEL[new_status]
    await cb.answer(f"{se} {sl}")
    # Refresh the category view
    cat = await db.get_category(idea["category_id"])
    ideas = await db.get_ideas_by_category(idea["category_id"])
    rows: list[list[InlineKeyboardButton]] = []
    for i in ideas:
        s = STATUS_EMOJI.get(i["status"], "🔵")
        # reflect updated status
        st = new_status if i["id"] == idea_id else i["status"]
        s = STATUS_EMOJI.get(st, "🔵")
        rows.append([
            InlineKeyboardButton(text=f"{s} {i['title']}", callback_data=f"istatus:{i['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"idel:{i['id']}:{idea['category_id']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить идею", callback_data=f"iadd:{idea['category_id']}")])
    rows.append([
        InlineKeyboardButton(text="🗑 Удалить категорию", callback_data=f"icatdel:{idea['category_id']}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="iback"),
    ])
    header = f"{cat['emoji']} *{escape_md(cat['name'])}*\n\n_🔵 Новая  🟡 В работе  🟢 Готово_"
    try:
        await cb.message.edit_text(header, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass


# ── Idea: delete ──

@router.callback_query(F.data.startswith("idel:"))
async def idea_delete(cb: CallbackQuery, db: Database):
    parts = cb.data.split(":")
    idea_id = int(parts[1])
    cat_id = int(parts[2])
    idea = await db.get_idea(idea_id)
    if idea:
        await db.delete_idea(idea_id)
        await cb.answer("🗑 Удалено")
        logger.info("Idea deleted: %s user=%s", idea["title"], cb.from_user.id)
    else:
        await cb.answer("Не найдено")
    # refresh
    cat = await db.get_category(cat_id)
    if not cat:
        return
    ideas = await db.get_ideas_by_category(cat_id)
    rows: list[list[InlineKeyboardButton]] = []
    for i in ideas:
        s = STATUS_EMOJI.get(i["status"], "🔵")
        rows.append([
            InlineKeyboardButton(text=f"{s} {i['title']}", callback_data=f"istatus:{i['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"idel:{i['id']}:{cat_id}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить идею", callback_data=f"iadd:{cat_id}")])
    rows.append([
        InlineKeyboardButton(text="🗑 Удалить категорию", callback_data=f"icatdel:{cat_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="iback"),
    ])
    header = f"{cat['emoji']} *{escape_md(cat['name'])}*"
    if not ideas:
        header += "\n\n_Пусто\\. Добавь первую идею\\!_"
    try:
        await cb.message.edit_text(header, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass
