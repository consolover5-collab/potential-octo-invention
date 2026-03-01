"""aiogram Telegram bot — control panel UI for tg-parsing."""

import json
import logging
import io

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.models import Config
from db.database import Database

logger = logging.getLogger(__name__)
router = Router()

# Runtime references (set in ControlBot.setup)
_bot_instance: "ControlBot | None" = None


def _cfg() -> Config:
    return _bot_instance.config


def _db() -> Database:
    return _bot_instance.db


async def _send_qr_image(message: Message, link: str) -> bool:
    try:
        import qrcode

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        await message.answer_photo(
            BufferedInputFile(buf.getvalue(), filename="userbot-login-qr.png"),
            caption="📷 QR для авторизации userbot (действует ~2 минуты).",
        )
        return True
    except Exception as e:
        logger.warning("Failed to build/send QR image: %s", e)
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(link)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            ascii_qr = "\n".join(
                "".join("██" if cell else "  " for cell in row)
                for row in matrix
            )
            await message.answer(
                "📷 PNG-QR не отправился, отправляю текстовый QR:\n"
                f"<pre>{ascii_qr}</pre>",
                parse_mode="HTML",
            )
            return True
        except Exception as e2:
            logger.warning("Failed to send ASCII QR fallback: %s", e2)
            return False


# ── Keyboards ──────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📡 Чаты", callback_data="chats"),
            InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="keywords"),
        ],
        [
            InlineKeyboardButton(text="💰 Макс. цена", callback_data="max_price"),
            InlineKeyboardButton(text="🧪 Тест", callback_data="test"),
        ],
        [
            InlineKeyboardButton(text="📋 Последние находки", callback_data="recent"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        ],
        [
            InlineKeyboardButton(text="🎯 Управление действиями", callback_data="actions_menu"),
        ],
        [
            InlineKeyboardButton(text="📜 Лог действий", callback_data="actions_log"),
        ],
        [
            InlineKeyboardButton(text="⏸ Пауза" if not (_bot_instance and _bot_instance.userbot and _bot_instance.userbot.paused) else "▶️ Запуск", callback_data="toggle_pause"),
        ],
        [
            InlineKeyboardButton(text="📊 Лимиты", callback_data="limits"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])


# ── /start ─────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    stats = await _db().get_stats()
    chats_count = len(_cfg().monitoring.chats)
    status = "Пауза" if (_bot_instance.userbot and _bot_instance.userbot.paused) else "Активен"

    text = (
        f"📊 Статус: {status} | Мониторинг: {chats_count} чат(ов)\n"
        f"🔍 Найдено: {stats['total_matches']} | ✉️ DM: {stats['total_dms']}"
    )
    await message.answer(text, reply_markup=main_menu_kb())


# ── Menu callback ──────────────────────────────────────────────────

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    stats = await _db().get_stats()
    chats_count = len(_cfg().monitoring.chats)
    status = "Пауза" if (_bot_instance.userbot and _bot_instance.userbot.paused) else "Активен"

    text = (
        f"📊 Статус: {status} | Мониторинг: {chats_count} чат(ов)\n"
        f"🔍 Найдено: {stats['total_matches']} | ✉️ DM: {stats['total_dms']}"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


# ── Chats ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "chats")
async def cb_chats(callback: CallbackQuery):
    chats = _cfg().monitoring.chats
    if chats:
        lines = [f"  {i+1}. {c}" for i, c in enumerate(chats)]
        text = "📡 Чаты для мониторинга:\n" + "\n".join(lines)
    else:
        text = "📡 Нет чатов для мониторинга."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="chat_add"),
            InlineKeyboardButton(text="➖ Удалить", callback_data="chat_del"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "chat_add")
async def cb_chat_add(callback: CallbackQuery):
    _bot_instance.awaiting[callback.from_user.id] = "chat_add"
    await callback.message.edit_text(
        "Введите @username или ID чата для добавления:",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "chat_del")
async def cb_chat_del(callback: CallbackQuery):
    chats = _cfg().monitoring.chats
    if not chats:
        await callback.answer("Список пуст", show_alert=True)
        return
    _bot_instance.awaiting[callback.from_user.id] = "chat_del"
    lines = [f"  {i+1}. {c}" for i, c in enumerate(chats)]
    await callback.message.edit_text(
        "Введите номер чата для удаления:\n" + "\n".join(lines),
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Keywords ───────────────────────────────────────────────────────

@router.callback_query(F.data == "keywords")
async def cb_keywords(callback: CallbackQuery):
    kws = _cfg().monitoring.keywords
    if kws:
        lines = [f"  {i+1}. {k}" for i, k in enumerate(kws)]
        text = "🔑 Ключевые слова:\n" + "\n".join(lines)
    else:
        text = "🔑 Нет ключевых слов."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add"),
            InlineKeyboardButton(text="➖ Удалить", callback_data="kw_del"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "kw_add")
async def cb_kw_add(callback: CallbackQuery):
    _bot_instance.awaiting[callback.from_user.id] = "kw_add"
    await callback.message.edit_text(
        "Введите ключевое слово для добавления:",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "kw_del")
async def cb_kw_del(callback: CallbackQuery):
    kws = _cfg().monitoring.keywords
    if not kws:
        await callback.answer("Список пуст", show_alert=True)
        return
    _bot_instance.awaiting[callback.from_user.id] = "kw_del"
    lines = [f"  {i+1}. {k}" for i, k in enumerate(kws)]
    await callback.message.edit_text(
        "Введите номер слова для удаления:\n" + "\n".join(lines),
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Max price ──────────────────────────────────────────────────────

@router.callback_query(F.data == "max_price")
async def cb_max_price(callback: CallbackQuery):
    price = _cfg().monitoring.max_price
    price_str = f"{price:,}".replace(",", " ") if price else "не задана"
    _bot_instance.awaiting[callback.from_user.id] = "max_price"
    await callback.message.edit_text(
        f"💰 Макс. цена: {price_str} ₽\nВведите новую максимальную цену:",
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Test ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "test")
async def cb_test(callback: CallbackQuery):
    _bot_instance.awaiting[callback.from_user.id] = "test"
    await callback.message.edit_text(
        "🧪 Отправьте текст или фото для тестирования pipeline:",
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Recent matches ─────────────────────────────────────────────────

@router.callback_query(F.data == "recent")
async def cb_recent(callback: CallbackQuery):
    rows = await _db().get_recent_matches(10)
    if not rows:
        text = "📋 Пока нет совпадений."
    else:
        lines = []
        for r in rows:
            dup = "🔄" if r["is_duplicate"] else "🔔"
            price_str = f"{r['price']:,}₽".replace(",", " ") if r.get("price") else "—"
            lines.append(
                f"{dup} {r['match_type']}({r.get('matched_value','')}) "
                f"| {price_str} | {r['created_at']}"
            )
        text = "📋 Последние находки:\n" + "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=back_kb())
    await callback.answer()


# ── Settings ───────────────────────────────────────────────────────

@router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    cfg = _cfg()
    vision_status = "вкл" if cfg.monitoring.use_vision else "выкл"
    notify = cfg.actions.notify_chat_id

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"👁 Vision: {vision_status}",
            callback_data="toggle_vision",
        )],
        [InlineKeyboardButton(text="📬 Кому уведомления", callback_data="set_notify")],
        [InlineKeyboardButton(text="🔐 Авторизовать userbot", callback_data="auth_userbot")],
        [InlineKeyboardButton(text="🔢 Ввести код вручную", callback_data="auth_userbot_code")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])
    await callback.message.edit_text(
        f"⚙️ Настройки\n👁 Vision: {vision_status}\n📬 Уведомления: {notify}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "toggle_vision")
async def cb_toggle_vision(callback: CallbackQuery):
    _cfg().monitoring.use_vision = not _cfg().monitoring.use_vision
    _save_config()
    status = "вкл" if _cfg().monitoring.use_vision else "выкл"
    await callback.answer(f"Vision: {status}", show_alert=True)
    await cb_settings(callback)


@router.callback_query(F.data == "set_notify")
async def cb_set_notify(callback: CallbackQuery):
    _bot_instance.awaiting[callback.from_user.id] = "set_notify"
    await callback.message.edit_text(
        "Введите chat_id для уведомлений (или 'me' для себя):",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "auth_userbot")
async def cb_auth_userbot(callback: CallbackQuery):
    if not _bot_instance.userbot:
        await callback.answer("Userbot недоступен", show_alert=True)
        return

    await callback.answer()

    try:
        link = await _bot_instance.userbot.create_qr_login_link()
    except Exception as e:
        logger.error("Failed to generate userbot auth link: %s", e)
        await callback.message.edit_text(
            "❌ Не удалось подготовить авторизацию userbot.",
            reply_markup=back_kb(),
        )
        return

    if not link:
        started = await _bot_instance.userbot.start()
        text = "✅ Userbot уже авторизован и запущен." if started else "✅ Userbot уже авторизован."
        await callback.message.edit_text(text, reply_markup=back_kb())
        return

    await callback.message.edit_text(
        "🔐 Подтвердите вход по QR или по ссылке ниже.\n"
        "QR/ссылка одноразовые и действуют ~2 минуты.",
        reply_markup=back_kb(),
    )
    sent_qr = await _send_qr_image(callback.message, link)
    if not sent_qr:
        await callback.message.answer("⚠️ Не удалось отправить QR-код изображением, использую ссылку.")
    await callback.message.answer(f"🔗 {link}")
    await callback.message.answer("⏳ Жду подтверждения авторизации...")

    result = await _bot_instance.userbot.wait_qr_login(timeout=180)
    if result == "ok":
        started = await _bot_instance.userbot.start()
        if started:
            await callback.message.answer("✅ Авторизация успешна, мониторинг запущен.")
        else:
            await callback.message.answer("✅ Авторизация успешна, но запуск userbot не удался.")
    elif result == "need_2fa":
        _bot_instance.awaiting[callback.from_user.id] = "auth_2fa"
        await callback.message.answer(
            "🔐 Для входа нужен 2FA-пароль.\n"
            "Введите его одним сообщением — бот удалит ваше сообщение после обработки."
        )
    elif result == "timeout":
        try:
            status = await _bot_instance.userbot.request_login_code()
        except Exception:
            status = "error"
        if status == "already_authorized":
            started = await _bot_instance.userbot.start()
            msg = "✅ Userbot уже авторизован и запущен." if started else "✅ Userbot уже авторизован."
            await callback.message.answer(msg)
        elif status == "sent":
            _bot_instance.awaiting[callback.from_user.id] = "auth_code"
            await callback.message.answer(
                "⌛ Ссылка не подтверждена вовремя. Переключаю на ввод кода вручную.\n"
                "Введите код из сообщения от аккаунта Telegram:"
            )
        else:
            await callback.message.answer("⌛ Время ожидания истекло. Нажмите «🔐 Авторизовать userbot» ещё раз.")
    else:
        await callback.message.answer("❌ Авторизация не завершена.")


@router.callback_query(F.data == "auth_userbot_code")
async def cb_auth_userbot_code(callback: CallbackQuery):
    if not _bot_instance.userbot:
        await callback.answer("Userbot недоступен", show_alert=True)
        return

    await callback.answer()
    try:
        status = await _bot_instance.userbot.request_login_code()
    except Exception as e:
        logger.error("Failed to request login code: %s", e)
        await callback.message.edit_text("❌ Не удалось запросить код авторизации.", reply_markup=back_kb())
        return

    if status == "already_authorized":
        started = await _bot_instance.userbot.start()
        text = "✅ Userbot уже авторизован и запущен." if started else "✅ Userbot уже авторизован."
        await callback.message.edit_text(text, reply_markup=back_kb())
        return

    _bot_instance.awaiting[callback.from_user.id] = "auth_code"
    await callback.message.edit_text(
        "🔢 Введите код из сообщения от аккаунта Telegram.\n"
        "Код приходит в официальном Telegram (обычно не SMS).\n"
        "Если после кода потребуется 2FA, бот попросит пароль.\n"
        "Ваше сообщение с кодом будет удалено после обработки.",
        reply_markup=back_kb(),
    )


# ── Pause / Resume ─────────────────────────────────────────────────

@router.callback_query(F.data == "toggle_pause")
async def cb_toggle_pause(callback: CallbackQuery):
    if _bot_instance.userbot:
        _bot_instance.userbot.paused = not _bot_instance.userbot.paused
        status = "⏸ Приостановлен" if _bot_instance.userbot.paused else "▶️ Запущен"
        await callback.answer(status, show_alert=True)
    await cb_menu(callback)


# ── Actions Menu ───────────────────────────────────────────────

@router.callback_query(F.data == "actions_menu")
async def cb_actions_menu(callback: CallbackQuery):
    cfg = _cfg()
    auto_dm_status = "вкл" if cfg.actions.auto_dm else "выкл"
    forward_status = "вкл" if cfg.actions.forward_to_main_bot else "выкл"
    dry_run_status = "вкл" if cfg.actions.dry_run else "выкл"

    text = (
        f"🎯 Управление действиями\n\n"
        f"✉️ Авто-DM: {auto_dm_status}\n"
        f"📤 Пересылка боту: {forward_status}\n"
        f"🔧 Режим пересылки: {cfg.actions.forward_mode.value}\n"
        f"🧪 Dry-run: {dry_run_status}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✉️ Авто-DM: {auto_dm_status}",
            callback_data="toggle_auto_dm"
        )],
        [InlineKeyboardButton(
            text=f"📤 Пересылка: {forward_status}",
            callback_data="toggle_forward"
        )],
        [InlineKeyboardButton(
            text=f"🔧 Режим: {cfg.actions.forward_mode.value}",
            callback_data="toggle_forward_mode"
        )],
        [InlineKeyboardButton(
            text=f"🧪 Dry-run: {dry_run_status}",
            callback_data="toggle_dry_run"
        )],
        [InlineKeyboardButton(
            text="📝 Редактировать шаблон DM",
            callback_data="edit_dm_template"
        )],
        [InlineKeyboardButton(
            text="🚫 Список opt-out",
            callback_data="opt_out_list"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "toggle_auto_dm")
async def cb_toggle_auto_dm(callback: CallbackQuery):
    _cfg().actions.auto_dm = not _cfg().actions.auto_dm
    _save_config()
    status = "вкл" if _cfg().actions.auto_dm else "выкл"
    await callback.answer(f"Авто-DM: {status}", show_alert=True)
    await cb_actions_menu(callback)


@router.callback_query(F.data == "toggle_forward")
async def cb_toggle_forward(callback: CallbackQuery):
    _cfg().actions.forward_to_main_bot = not _cfg().actions.forward_to_main_bot
    _save_config()
    status = "вкл" if _cfg().actions.forward_to_main_bot else "выкл"
    await callback.answer(f"Пересылка: {status}", show_alert=True)
    await cb_actions_menu(callback)


@router.callback_query(F.data == "toggle_forward_mode")
async def cb_toggle_forward_mode(callback: CallbackQuery):
    from bot.models import ForwardMode
    current = _cfg().actions.forward_mode
    if current == ForwardMode.FORWARD_RAW:
        _cfg().actions.forward_mode = ForwardMode.NOTIFY_WITH_META
    else:
        _cfg().actions.forward_mode = ForwardMode.FORWARD_RAW
    _save_config()
    await callback.answer(f"Режим: {_cfg().actions.forward_mode.value}", show_alert=True)
    await cb_actions_menu(callback)


@router.callback_query(F.data == "toggle_dry_run")
async def cb_toggle_dry_run(callback: CallbackQuery):
    _cfg().actions.dry_run = not _cfg().actions.dry_run
    _save_config()
    status = "вкл" if _cfg().actions.dry_run else "выкл"
    await callback.answer(f"Dry-run: {status}", show_alert=True)
    await cb_actions_menu(callback)


@router.callback_query(F.data == "edit_dm_template")
async def cb_edit_dm_template(callback: CallbackQuery):
    current = _cfg().actions.dm_template
    _bot_instance.awaiting[callback.from_user.id] = "edit_dm_template"
    await callback.message.edit_text(
        f"📝 Текущий шаблон DM:\n{current}\n\n"
        f"Поддерживаемые плейсхолдеры:\n"
        f"{{type}}, {{price}}, {{link}}, {{author}}, {{chat_title}}, {{message_snippet}}\n\n"
        f"Введите новый шаблон:",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "opt_out_list")
async def cb_opt_out_list(callback: CallbackQuery):
    opt_out = _cfg().rules.opt_out_list
    if opt_out:
        lines = [f"  {i+1}. {uid}" for i, uid in enumerate(opt_out)]
        text = "🚫 Список opt-out (не слать DM):\n" + "\n".join(lines)
    else:
        text = "🚫 Список opt-out пуст."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="opt_out_add"),
            InlineKeyboardButton(text="➖ Удалить", callback_data="opt_out_del"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="actions_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "opt_out_add")
async def cb_opt_out_add(callback: CallbackQuery):
    _bot_instance.awaiting[callback.from_user.id] = "opt_out_add"
    await callback.message.edit_text(
        "Введите user_id для добавления в opt-out:",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "opt_out_del")
async def cb_opt_out_del(callback: CallbackQuery):
    opt_out = _cfg().rules.opt_out_list
    if not opt_out:
        await callback.answer("Список пуст", show_alert=True)
        return
    _bot_instance.awaiting[callback.from_user.id] = "opt_out_del"
    lines = [f"  {i+1}. {uid}" for i, uid in enumerate(opt_out)]
    await callback.message.edit_text(
        "Введите номер для удаления:\n" + "\n".join(lines),
        reply_markup=back_kb(),
    )
    await callback.answer()


# ── Actions Log ────────────────────────────────────────────────

@router.callback_query(F.data == "actions_log")
async def cb_actions_log(callback: CallbackQuery):
    logs = await _db().get_actions_log(limit=20)
    if not logs:
        text = "📜 Лог действий пуст."
    else:
        lines = []
        for log in logs:
            icon = "✉️" if log["action_type"] == "dm" else "📤" if log["action_type"] == "forward" else "🔄"
            result_icon = "✅" if log["result"] == "success" else "❌"
            lines.append(
                f"{icon} {result_icon} {log['action_type']} | {log['timestamp'][:16]}"
            )
        text = "📜 Последние действия:\n" + "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=back_kb())
    await callback.answer()


# ── Help ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    text = (
        "❓ <b>Справка по боту</b>\n\n"
        "Бот мониторит Telegram-чаты на наличие объявлений, совпадающих с заданными "
        "ключевыми словами или изображениями, и автоматически отправляет DM продавцам.\n\n"
        "<b>Кнопки главного меню:</b>\n"
        "📡 <b>Чаты</b> — список чатов для мониторинга; добавить/удалить чат\n"
        "🔑 <b>Ключевые слова</b> — слова/фразы для поиска в тексте сообщений\n"
        "💰 <b>Макс. цена</b> — фильтр: игнорировать объявления дороже заданной суммы\n"
        "🧪 <b>Тест</b> — проверить, сработает ли pipeline на вашем тексте или фото\n"
        "📋 <b>Последние находки</b> — 10 последних совпадений с типом и ценой\n"
        "⚙️ <b>Настройки</b> — включить/выключить Vision (анализ фото через Groq), "
        "настроить канал уведомлений и пройти авторизацию userbot\n"
        "🔐 <b>Авторизовать userbot</b> — получить QR-код и ссылку входа\n"
        "🔢 <b>Ввести код вручную</b> — вариант для старых версий Telegram, где tg://login не открывается\n"
        "🔒 Сообщения с кодом/2FA для авторизации удаляются ботом после обработки\n"
        "🎯 <b>Управление действиями</b> — авто-DM, пересылка, dry-run режим, "
        "шаблон сообщения и список opt-out\n"
        "📜 <b>Лог действий</b> — последние 20 выполненных действий (DM / пересылка)\n"
        "⏸/▶️ <b>Пауза / Запуск</b> — приостановить или возобновить мониторинг\n\n"
        "<b>Плейсхолдеры шаблона DM:</b>\n"
        "<code>{type}</code>, <code>{price}</code>, <code>{link}</code>, "
        "<code>{author}</code>, <code>{chat_title}</code>, <code>{message_snippet}</code>"
    )
    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await callback.answer()


# ── Limits ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "limits")
async def cb_limits(callback: CallbackQuery):
    from bot.vision import _groq_rate_info

    dm = _bot_instance.dm_limiter
    vis = _bot_instance.vision_limiter

    dm_line = (
        f"✉️ DM: {dm.remaining}/{dm.max_tokens} осталось в час"
        if dm else "✉️ DM: недоступно"
    )
    vis_retry = f" (сброс через {vis.retry_after:.0f}с)" if vis and vis.retry_after > 0 else ""
    vis_line = (
        f"👁 Vision: {vis.remaining}/{vis.max_tokens} осталось в мин{vis_retry}"
        if vis else "👁 Vision: недоступно"
    )

    if _groq_rate_info:
        rem_req = _groq_rate_info.get("remaining_requests", "?")
        lim_req = _groq_rate_info.get("limit_requests", "?")
        rem_tok = _groq_rate_info.get("remaining_tokens", "?")
        lim_tok = _groq_rate_info.get("limit_tokens", "?")
        reset = _groq_rate_info.get("reset_requests", "?")
        groq_lines = (
            f"\n🌐 <b>Groq API</b> (данные из последнего ответа):\n"
            f"  Запросы: {rem_req}/{lim_req}\n"
            f"  Токены:  {rem_tok}/{lim_tok}\n"
            f"  Сброс:   {reset}"
        )
    else:
        groq_lines = "\n🌐 <b>Groq API</b>: нет данных (Vision ещё не вызывался)"

    text = f"📊 <b>Лимиты</b>\n\n{dm_line}\n{vis_line}{groq_lines}"
    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await callback.answer()


# ── Text input handler ─────────────────────────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_input(message: Message):
    action = _bot_instance.awaiting.pop(message.from_user.id, None)
    if not action:
        return

    text = message.text.strip()
    if action in {"auth_code", "auth_2fa"}:
        try:
            await message.delete()
        except Exception:
            pass

    if action == "chat_add":
        _cfg().monitoring.chats.append(text)
        _save_config()
        if _bot_instance.userbot:
            # Re-register chats requires restart; notify user
            await message.answer(f"✅ Чат {text} добавлен.\n⚠️ Перезапустите бота для применения.")
        else:
            await message.answer(f"✅ Чат {text} добавлен.")

    elif action == "chat_del":
        try:
            idx = int(text) - 1
            removed = _cfg().monitoring.chats.pop(idx)
            _save_config()
            await message.answer(f"✅ Чат {removed} удалён.\n⚠️ Перезапустите бота для применения.")
        except (ValueError, IndexError):
            await message.answer("❌ Неверный номер.")

    elif action == "kw_add":
        _cfg().monitoring.keywords.append(text)
        _save_config()
        if _bot_instance.userbot:
            _bot_instance.userbot.matcher.update(_cfg().monitoring.keywords)
        await message.answer(f"✅ Ключевое слово «{text}» добавлено.")

    elif action == "kw_del":
        try:
            idx = int(text) - 1
            removed = _cfg().monitoring.keywords.pop(idx)
            _save_config()
            if _bot_instance.userbot:
                _bot_instance.userbot.matcher.update(_cfg().monitoring.keywords)
            await message.answer(f"✅ Слово «{removed}» удалено.")
        except (ValueError, IndexError):
            await message.answer("❌ Неверный номер.")

    elif action == "max_price":
        try:
            new_price = int(text.replace(" ", "").replace("₽", "").replace("р", ""))
            _cfg().monitoring.max_price = new_price
            _save_config()
            await message.answer(f"✅ Макс. цена: {new_price:,} ₽".replace(",", " "))
        except ValueError:
            await message.answer("❌ Введите число.")

    elif action == "set_notify":
        if text.lower() == "me":
            _cfg().actions.notify_chat_id = "me"
        else:
            try:
                _cfg().actions.notify_chat_id = int(text)
            except ValueError:
                _cfg().actions.notify_chat_id = text
        _save_config()
        await message.answer(f"✅ Уведомления: {_cfg().actions.notify_chat_id}")

    elif action == "auth_code":
        if not _bot_instance.userbot:
            await message.answer("❌ Userbot недоступен.")
        else:
            result = await _bot_instance.userbot.sign_in_with_code(text)
            if result == "ok":
                started = await _bot_instance.userbot.start()
                if started:
                    await message.answer("✅ Авторизация успешна, мониторинг запущен.")
                else:
                    await message.answer("✅ Авторизация успешна, но userbot не запущен.")
            elif result == "need_2fa":
                _bot_instance.awaiting[message.from_user.id] = "auth_2fa"
                await message.answer("🔐 Введите пароль 2FA:")
                return
            elif result == "invalid_code":
                _bot_instance.awaiting[message.from_user.id] = "auth_code"
                await message.answer("❌ Неверный код. Попробуйте ещё раз:")
                return
            elif result == "expired_code":
                try:
                    await _bot_instance.userbot.request_login_code()
                    _bot_instance.awaiting[message.from_user.id] = "auth_code"
                    await message.answer("⌛ Код истёк. Отправил новый, введите его:")
                    return
                except Exception:
                    await message.answer("❌ Код истёк и не удалось запросить новый.")
            else:
                await message.answer("❌ Не удалось авторизоваться по коду.")

    elif action == "auth_2fa":
        if not _bot_instance.userbot:
            await message.answer("❌ Userbot недоступен.")
        else:
            result = await _bot_instance.userbot.sign_in_with_password(text)
            if result == "ok":
                started = await _bot_instance.userbot.start()
                if started:
                    await message.answer("✅ Авторизация 2FA успешна, мониторинг запущен.")
                else:
                    await message.answer("✅ Авторизация 2FA успешна, но userbot не запущен.")
            elif result == "invalid_2fa":
                _bot_instance.awaiting[message.from_user.id] = "auth_2fa"
                await message.answer("❌ Неверный пароль 2FA. Попробуйте снова:")
                return
            else:
                await message.answer("❌ Не удалось выполнить 2FA авторизацию.")

    elif action == "test":
        from bot.keywords import KeywordMatcher
        from bot.price import extract_price as ep
        matcher = KeywordMatcher(_cfg().monitoring.keywords)
        kw = matcher.match(text)
        price = ep(text)
        if kw:
            await message.answer(f"🧪 Результат:\n🏷 Keyword: {kw}\n💰 Цена: {price or '—'}")
        else:
            await message.answer("🧪 Результат: совпадений по тексту нет.")

    elif action == "edit_dm_template":
        _cfg().actions.dm_template = text
        _save_config()
        await message.answer(f"✅ Шаблон DM обновлён:\n{text}")

    elif action == "opt_out_add":
        try:
            user_id = int(text)
            if user_id not in _cfg().rules.opt_out_list:
                _cfg().rules.opt_out_list.append(user_id)
                _save_config()
                await message.answer(f"✅ User {user_id} добавлен в opt-out")
            else:
                await message.answer(f"⚠️ User {user_id} уже в списке")
        except ValueError:
            await message.answer("❌ Введите число (user_id)")

    elif action == "opt_out_del":
        try:
            idx = int(text) - 1
            removed = _cfg().rules.opt_out_list.pop(idx)
            _save_config()
            await message.answer(f"✅ User {removed} удалён из opt-out")
        except (ValueError, IndexError):
            await message.answer("❌ Неверный номер.")

    await message.answer("Главное меню:", reply_markup=main_menu_kb())


# ── Photo test handler ─────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo_input(message: Message):
    action = _bot_instance.awaiting.pop(message.from_user.id, None)
    if action != "test":
        return

    if not _cfg().monitoring.use_vision or not _cfg().vision.api_key:
        await message.answer("🧪 Vision выключен или нет API key.")
        return

    await message.answer("🧪 Анализирую фото...")
    photo = message.photo[-1]
    file = await message.bot.download(photo)
    image_bytes = file.read()

    from bot.vision import analyse_image, parse_vision_response
    reply = await analyse_image(image_bytes, _cfg().monitoring.vision_prompt, _cfg().vision)
    if reply:
        result = parse_vision_response(reply)
        if result:
            await message.answer(f"🧪 Vision результат:\n🏷 Тип: {result['type']}\n💰 Цена: {result.get('price', '—')}")
        else:
            await message.answer(f"🧪 Vision ответ: НЕТ (не совпадение)\nRaw: {reply[:200]}")
    else:
        await message.answer("🧪 Vision API не ответил.")

    await message.answer("Главное меню:", reply_markup=main_menu_kb())


# ── Config persistence ─────────────────────────────────────────────

def _save_config():
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(_cfg().model_dump(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save config: %s", e)


# ── ControlBot class ───────────────────────────────────────────────

class ControlBot:
    def __init__(self, config: Config, db: Database, dm_limiter=None, vision_limiter=None):
        self.config = config
        self.db = db
        self.userbot = None  # set externally after init
        self.awaiting: dict[int, str] = {}  # user_id -> action
        self.dm_limiter = dm_limiter
        self.vision_limiter = vision_limiter

        self.bot = Bot(token=config.telegram.bot_token)
        self.dp = Dispatcher()
        self.dp.include_router(router)

        global _bot_instance
        _bot_instance = self

    async def send_notification(self, text: str):
        """Send notification to the configured chat."""
        chat_id = self.config.actions.notify_chat_id
        if chat_id == "me":
            me = await self.bot.get_me()
            # For "me", send to the bot owner — we use the first user who /start'd
            # In practice, we send to saved_messages via userbot
            logger.info("Notification (me): %s", text[:80])
            return
        try:
            await self.bot.send_message(chat_id=int(chat_id), text=text)
        except Exception as e:
            logger.error("Failed to send notification: %s", e)

    async def start(self):
        logger.info("Control bot starting...")
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def stop(self):
        await self.dp.stop_polling()
        await self.bot.session.close()
        logger.info("Control bot stopped")
