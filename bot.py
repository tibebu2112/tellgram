"""BDU Student Union Telegram Bot — 8-step complaint flow.

Step 1  → Campus selection
Step 2  → Department (typed or keyboard)
Step 3  → Identity preference (reveal / anonymous)
Step 4  → Problem category
Step 5  → Location details (building / floor / room)
Step 6  → Problem description (free text)
Step 7  → Media attachment (photo / video / skip)
Step 8  → Confirmation summary → ticket issued

Runs as a standalone worker process (Procfile: worker: python bot.py)
and also works as a background thread inside Streamlit as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from telegram import (
        BotCommand,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        MenuButtonCommands,
        ReplyKeyboardMarkup,
        ReplyKeyboardRemove,
        Update,
    )
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        ConversationHandler,
        MessageHandler,
        filters,
    )
except ImportError as _telegram_import_error:
    raise ImportError(
        f"python-telegram-bot is not installed or has wrong version: {_telegram_import_error}\n"
        "Add  python-telegram-bot==21.6  to requirements.txt and redeploy."
    ) from _telegram_import_error

from constants import (
    CAMPUSES,
    CATEGORIES,
    DEPARTMENTS,
    IDENTITY_ANON,
    IDENTITY_NAMED,
    NEW_COMPLAINT,
    SKIP,
    STATUS_AM,
)
from data_manager import EVIDENCE_DIR, load_secrets, read_complaints, save_complaint

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("unionstudent.bot")

BOT_USERNAME = "unionstudent_complaints_bot"
BOT_URL      = f"https://t.me/{BOT_USERNAME}"

# ── Conversation states ───────────────────────────────────────────────────────
(
    S_CAMPUS,
    S_DEPARTMENT,
    S_IDENTITY,
    S_NAMED_INFO,
    S_CATEGORY,
    S_BUILDING,
    S_FLOOR,
    S_ROOM,
    S_DESCRIPTION,
    S_EVIDENCE,
    S_CONFIRM,
) = range(11)

# Callback-data prefixes for inline keyboards
CB_CAMPUS   = "campus:"
CB_DEPT     = "dept:"
CB_IDENTITY = "id:"
CB_CATEGORY = "cat:"
CB_CONFIRM  = "confirm:"

# ── Global status (used by Streamlit dashboard) ────────────────────────────────
_bot_thread:   threading.Thread | None = None
_bot_status    = "stopped"
_bot_error     = ""
_restart_count = 0
_keepalive_on  = False


# ── Helpers ────────────────────────────────────────────────────────────────────

_RESTART_WORDS = {
    "start",
    "/start",
    "new",
    "/new",
    "help",
    "/help",
    "hi",
    "hello",
    "ሰላም",
    "ቅሬታ",
}


def _running_under_streamlit() -> bool:
    return bool(
        os.getenv("STREAMLIT_SERVER_PORT")
        or os.getenv("STREAMLIT_RUNTIME")
        or os.getenv("STREAMLIT_SERVER_HEADLESS")
    )


def token_from_anywhere() -> str:
    env = os.getenv("TELEGRAM_BOT_TOKEN")
    if env:
        return env.strip()
    file_secrets = load_secrets()
    if file_secrets.get("TELEGRAM_BOT_TOKEN"):
        return str(file_secrets["TELEGRAM_BOT_TOKEN"]).strip()
    try:
        import streamlit as st
        v = st.secrets.get("TELEGRAM_BOT_TOKEN")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return ""


def _app_url_from_anywhere() -> str:
    env = os.getenv("STREAMLIT_APP_URL")
    if env:
        return env.strip()
    file_secrets = load_secrets()
    if file_secrets.get("STREAMLIT_APP_URL"):
        return str(file_secrets["STREAMLIT_APP_URL"]).strip()
    try:
        import streamlit as st
        v = st.secrets.get("STREAMLIT_APP_URL")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return ""


def bot_status() -> tuple[str, str]:
    return _bot_status, _bot_error


def validate_telegram_token(token: str) -> tuple[bool, str]:
    """Check the token with Telegram getMe. Never logs the token itself."""
    if not token or ":" not in token:
        return False, "TELEGRAM_BOT_TOKEN is missing or not a bot token (must look like 123456:AA...)."
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getMe",
        headers={"User-Agent": "BDU-UnionStudentBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        username = str((data.get("result") or {}).get("username") or "")
        if data.get("ok") and username:
            return True, username
        return False, "Telegram getMe did not return a bot username."
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, (
                "TELEGRAM_BOT_TOKEN is invalid or revoked (Telegram 401 Unauthorized). "
                "The bot cannot receive any messages until you put a fresh token from @BotFather "
                "into Streamlit Cloud → Settings → Secrets, then reboot the app. "
                "Do not commit the token to GitHub."
            )
        return False, f"Telegram getMe failed (HTTP {exc.code})."
    except Exception as exc:
        return False, f"Could not reach api.telegram.org: {exc}"


def _token() -> str:
    t = token_from_anywhere()
    if not t:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .streamlit/secrets.toml or Streamlit Cloud Secrets.")
    return t


def _inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build an InlineKeyboardMarkup from rows of (label, callback_data) tuples."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


def _reply_kb(options: list[str], columns: int = 1) -> ReplyKeyboardMarkup:
    rows = [options[i: i + columns] for i in range(0, len(options), columns)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def _is_skip(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in {SKIP.lower(), "skip", "⏭ skip photo/video"}


def _campus_keyboard() -> InlineKeyboardMarkup:
    rows = [[( c, f"{CB_CAMPUS}{c}")] for c in CAMPUSES]
    return _inline(rows)


def _department_keyboard() -> InlineKeyboardMarkup:
    # Two columns for departments
    depts = DEPARTMENTS
    rows = []
    for i in range(0, len(depts), 2):
        pair = depts[i: i + 2]
        rows.append([(d, f"{CB_DEPT}{d}") for d in pair])
    return _inline(rows)


def _category_keyboard() -> InlineKeyboardMarkup:
    rows = [[(c, f"{CB_CATEGORY}{c}")] for c in CATEGORIES]
    return _inline(rows)


def _identity_keyboard() -> InlineKeyboardMarkup:
    return _inline([
        [(IDENTITY_NAMED, f"{CB_IDENTITY}named")],
        [(IDENTITY_ANON,  f"{CB_IDENTITY}anon")],
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return _inline([
        [("✅ Submit Complaint", f"{CB_CONFIRM}yes")],
        [("✏️ Start Over",       f"{CB_CONFIRM}no")],
    ])


def _wants_restart(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in _RESTART_WORDS


def _safe_name(value: str | None) -> str:
    """Strip Markdown special chars so a student's name cannot crash replies."""
    raw = (value or "Student").strip() or "Student"
    for ch in ("_", "*", "`", "[", "]", "(", ")"):
        raw = raw.replace(ch, " ")
    return raw


async def _reply_text(update: Update, text: str, **kwargs) -> None:
    """Reply from either a typed message or an inline-button tap."""
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.reply_text(text, **kwargs)
    except Exception:
        kwargs.pop("parse_mode", None)
        await msg.reply_text(text)


async def _edit_text(update: Update, text: str, **kwargs) -> None:
    query = update.callback_query
    if not query:
        await _reply_text(update, text, **kwargs)
        return
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        kwargs.pop("parse_mode", None)
        try:
            await query.edit_message_text(text)
        except Exception:
            await _reply_text(update, text)


async def tap_buttons_or_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """If the student types 'start' while buttons are showing, restart instead of going silent."""
    text = (update.message.text if update.message else "") or ""
    if _wants_restart(text):
        return await start(update, context)
    await _reply_text(
        update,
        "Please tap a button above ✅\nOr send /start to begin a new complaint.",
    )
    return None


def _progress(step: int, total: int = 8) -> str:
    filled = "🟦" * step + "⬜" * (total - step)
    return f"{filled}  Step {step}/{total}"


def _summary(d: dict) -> str:
    """Build a human-readable summary of everything collected so far."""
    anon    = d.get("is_anonymous", False)
    name    = "Anonymous 🕵" if anon else (d.get("full_name") or "—")
    sid     = "—" if anon else (d.get("student_id") or "—")
    phone   = "—" if anon else (d.get("phone") or "—")
    loc_parts = [d.get("building",""), d.get("floor",""), d.get("room","")]
    location  = " / ".join(p for p in loc_parts if p) or "—"
    has_media = bool(d.get("evidence_path") or d.get("evidence_file_id"))
    return (
        "📋 *Complaint Summary*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🏫 *Campus:*      {d.get('campus','—')}\n"
        f"🎓 *Department:*  {d.get('department','—')}\n"
        f"👤 *Identity:*    {name}\n"
        f"🪪 *Student ID:*  {sid}\n"
        f"📞 *Phone:*       {phone}\n"
        f"🗂 *Category:*   {d.get('category','—')}\n"
        f"📍 *Location:*   {location}\n"
        f"📸 *Media:*       {'Attached ✅' if has_media else 'None'}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *Description:*\n{d.get('description','—')}"
    )


# ── Step 1 — /start ────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user  = update.effective_user
    greet = _safe_name(user.first_name if user else "Student")
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
    await _reply_text(
        update,
        f"ሰላም {greet}! 👋\n\n"
        "🎓 *Bahir Dar University*\n"
        "*Student Union Complaint Bot* ✅\n"
        "Available 24/7 — your voice matters.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(1)}\n"
        "*Select your Campus:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_campus_keyboard(),
    )
    return S_CAMPUS


# ── Step 1 callback — campus chosen ───────────────────────────────────────────

async def cb_campus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    campus = query.data.removeprefix(CB_CAMPUS)
    context.user_data["campus"] = campus
    await _edit_text(
        update,
        f"✅ Campus: *{campus}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(2)}\n"
        "*Select or type your Department:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_department_keyboard(),
    )
    return S_DEPARTMENT


# ── Step 2 — department (inline button or typed text) ─────────────────────────

async def cb_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    dept = query.data.removeprefix(CB_DEPT)
    context.user_data["department"] = dept
    await _edit_text(
        update,
        f"✅ Department: *{dept}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(3)}\n"
        "*How should your identity be handled?*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_identity_keyboard(),
    )
    return S_IDENTITY


async def text_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Accept a free-typed department name."""
    dept = (update.message.text or "").strip()
    if _wants_restart(dept):
        return await start(update, context)
    if len(dept) < 2:
        await update.message.reply_text(
            "Please type your department name (at least 2 characters) "
            "or choose one from the list above.",
            reply_markup=_department_keyboard(),
        )
        return S_DEPARTMENT
    context.user_data["department"] = dept
    await update.message.reply_text(
        f"✅ Department: *{_safe_name(dept)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(3)}\n"
        "*How should your identity be handled?*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_identity_keyboard(),
    )
    return S_IDENTITY


# ── Step 3 — identity ─────────────────────────────────────────────────────────

async def cb_identity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()
    choice = query.data.removeprefix(CB_IDENTITY)

    if choice == "anon":
        context.user_data["is_anonymous"] = True
        await _edit_text(
            update,
            "🕵 Identity kept *Anonymous*.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"{_progress(4)}\n"
            "*Select the problem category:*\n"
            "━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_category_keyboard(),
        )
        return S_CATEGORY

    # Reveal identity
    context.user_data["is_anonymous"] = False
    await _edit_text(
        update,
        "👤 Identity will be *revealed*.\n\n"
        "Please enter your *Full Name*:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_NAMED_INFO


async def collect_named_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect full name, then student ID / phone in one message or separately."""
    text = (update.message.text or "").strip()
    if _wants_restart(text):
        return await start(update, context)
    if "full_name" not in context.user_data:
        # First message = full name
        if len(text) < 2:
            await update.message.reply_text("Please enter your full name (at least 2 characters):")
            return S_NAMED_INFO
        context.user_data["full_name"] = text
        await update.message.reply_text(
            f"✅ Name: *{_safe_name(text)}*\n\n"
            "Now enter your *Student ID* (e.g. BDU1234567):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return S_NAMED_INFO

    if "student_id" not in context.user_data:
        # Second message = student ID
        context.user_data["student_id"] = text
        await update.message.reply_text(
            f"✅ Student ID: *{_safe_name(text)}*\n\n"
            "Enter your *Phone Number* (e.g. 0912345678):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return S_NAMED_INFO

    # Third message = phone → move to category
    context.user_data["phone"] = text
    await update.message.reply_text(
        f"✅ Phone: *{_safe_name(text)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(4)}\n"
        "*Select the problem category:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_category_keyboard(),
    )
    return S_CATEGORY


# ── Step 4 — category ─────────────────────────────────────────────────────────

async def cb_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cat   = query.data.removeprefix(CB_CATEGORY)
    context.user_data["category"] = cat
    await _edit_text(
        update,
        f"✅ Category: *{cat}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(5)}\n"
        "*Location Details*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Enter the *Building Name or Block Number*\n"
        "(e.g. Block A, Admin Building, Dorm 5)\n\n"
        "Type `skip` to leave blank.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_BUILDING


# ── Step 5 — location (building → floor → room) ───────────────────────────────

async def collect_building(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _wants_restart(text):
        return await start(update, context)
    context.user_data["building"] = "" if text.lower() == "skip" else text
    await update.message.reply_text(
        "Enter the *Floor Number*\n(e.g. Ground, 1st, 2nd)\n\nType `skip` to leave blank.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_FLOOR


async def collect_floor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _wants_restart(text):
        return await start(update, context)
    context.user_data["floor"] = "" if text.lower() == "skip" else text
    await update.message.reply_text(
        "Enter the *Room / Dorm Number* or specific location\n"
        "(e.g. Room 204, Dorm 3B, Library Hall)\n\nType `skip` to leave blank.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_ROOM


async def collect_room(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _wants_restart(text):
        return await start(update, context)
    context.user_data["room"] = "" if text.lower() == "skip" else text
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(6)}\n"
        "*Describe the Problem*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Write a detailed description of the issue.\n"
        "The more detail you provide, the faster it gets resolved. 📝",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return S_DESCRIPTION


# ── Step 6 — description ──────────────────────────────────────────────────────

async def collect_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _wants_restart(text):
        return await start(update, context)
    if len(text) < 10:
        await update.message.reply_text(
            "❗ Please write a more detailed description (at least 10 characters)."
        )
        return S_DESCRIPTION
    context.user_data["description"] = text
    await update.message.reply_text(
        "✅ Description saved!\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(7)}\n"
        "*Attach Photo or Video (Optional)*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📸 Send a photo or short video showing the issue.\n"
        "If you don't have one, tap the button below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_reply_kb([SKIP]),
    )
    return S_EVIDENCE


# ── Step 7 — evidence ─────────────────────────────────────────────────────────

async def _store_file(update: Update, suffix: str, media) -> tuple[str, str]:
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        tg_file  = await media.get_file()
        filename = f"{update.effective_user.id}_{int(time.time())}{suffix}"
        path     = EVIDENCE_DIR / filename
        await tg_file.download_to_drive(path)
        return str(path), media.file_id
    except Exception:
        logger.exception("Could not download evidence file")
        return "", getattr(media, "file_id", "")


async def collect_evidence_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo           = update.message.photo[-1]
    path, file_id   = await _store_file(update, ".jpg", photo)
    context.user_data["evidence_path"]    = path
    context.user_data["evidence_file_id"] = file_id
    await update.message.reply_text(
        "✅ Photo attached!",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await _show_summary(update, context)


async def collect_evidence_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video           = update.message.video
    path, file_id   = await _store_file(update, ".mp4", video)
    context.user_data["evidence_path"]    = path
    context.user_data["evidence_file_id"] = file_id
    await update.message.reply_text(
        "✅ Video attached!",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await _show_summary(update, context)


async def collect_evidence_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _wants_restart(update.message.text if update.message else ""):
        return await start(update, context)
    if not _is_skip(update.message.text):
        await update.message.reply_text(
            "Please send a photo/video or tap *Skip Photo/Video* 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_reply_kb([SKIP]),
        )
        return S_EVIDENCE
    context.user_data["evidence_path"]    = ""
    context.user_data["evidence_file_id"] = ""
    return await _show_summary(update, context)


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 8 — show full summary and ask for confirmation."""
    d   = context.user_data
    msg = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_progress(8)}\n"
        "*Review & Confirm*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        + _summary(d)
        + "\n\n━━━━━━━━━━━━━━━━━━━\n"
        "Is everything correct? Tap *Submit* to register your complaint."
    )
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirm_keyboard(),
    )
    return S_CONFIRM


# ── Step 8 — confirmation ─────────────────────────────────────────────────────

async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()
    choice = query.data.removeprefix(CB_CONFIRM)

    if choice == "no":
        # Start over
        await query.edit_message_text("↩️ Restarting... send /start to begin again.")
        context.user_data.clear()
        return ConversationHandler.END

    # Submit
    user = update.effective_user
    d    = context.user_data
    try:
        saved = save_complaint(
            telegram_id       = str(user.id if user else ""),
            telegram_username = (user.username or "") if user else "",
            campus            = d.get("campus", ""),
            department        = d.get("department", ""),
            category          = d.get("category", ""),
            description       = d.get("description", ""),
            building          = d.get("building", ""),
            floor             = d.get("floor", ""),
            room              = d.get("room", ""),
            evidence_path     = d.get("evidence_path", ""),
            evidence_file_id  = d.get("evidence_file_id", ""),
            is_anonymous      = bool(d.get("is_anonymous")),
            full_name         = d.get("full_name", ""),
            student_id        = d.get("student_id", ""),
            phone             = d.get("phone", ""),
        )
    except Exception:
        logger.exception("Failed to save complaint")
        await query.edit_message_text(
            "⚠️ Sorry, your complaint could not be saved.\n"
            "Please try again with /start."
        )
        context.user_data.clear()
        return ConversationHandler.END

    ticket = saved["id"]
    context.user_data.clear()

    await query.edit_message_text(
        "🎉 *Complaint Registered Successfully!*\n\n"
        f"🎫 *Ticket ID:* `{ticket}`\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📌 *Save this Ticket ID!*\n"
        "To check your complaint status, send:\n"
        f"`/status {ticket}`\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "The Student Union will review your complaint and respond. ✊\n"
        "You will be notified here when the status changes.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline([[("➕ Submit Another Complaint", f"{CB_CONFIRM}new")]]),
    )
    return ConversationHandler.END


async def cb_new_complaint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """'Submit Another Complaint' inline button after ticket issued."""
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    return await start(update, context)


# ── /status command ───────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args      = context.args or []
    ticket_id = " ".join(args).strip()
    if not ticket_id:
        await update.message.reply_text(
            "📊 To check your complaint status send:\n\n"
            "`/status #BDU-2026-112`\n\n"
            "(replace with your ticket number)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        df    = read_complaints()
        match = df[df["id"].astype(str).str.strip() == ticket_id.strip()]
    except Exception:
        logger.exception("Error reading complaints")
        await update.message.reply_text("⚠️ Could not read data. Please try later.")
        return
    if match.empty:
        await update.message.reply_text(
            f"❌ Ticket `{ticket_id}` not found.\n\n"
            "Please double-check the number.\n"
            "Example: `/status #BDU-2026-112`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    r          = match.iloc[0]
    sv         = str(r["status"])
    sl         = STATUS_AM.get(sv, sv)
    pct        = {"Pending": 5, "Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)
    bar        = "🟩" * (pct // 10) + "⬜" * (10 - pct // 10)
    resolution = str(r.get("resolution_note", "")) if "resolution_note" in r.index else ""
    loc_parts  = [str(r.get(f, "")) for f in ("building", "floor", "room")]
    location   = " / ".join(p for p in loc_parts if p) or "—"

    msg = (
        f"🎫 *{r['id']}*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🏫 Campus:     {r['campus']}\n"
        f"🎓 Department: {r.get('department','—')}\n"
        f"🗂 Category:  {r['category']}\n"
        f"📍 Location:  {location}\n"
        f"📅 Submitted: {r['created_at']}\n\n"
        f"📊 Status:    {sl}\n"
        f"🔢 Progress:  {bar} {pct}%"
    )
    if resolution.strip():
        msg += f"\n\n💬 *Union Response:*\n{resolution}"
    else:
        msg += "\n\n⏳ The Union has not responded yet. Please check back later."

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline([[("➕ New Complaint", f"{CB_CONFIRM}new_outside")]]),
    )


# ── /cancel command ───────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cancelled.\n"
        "Whenever you have a complaint, send /start 👋",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── Catch-all fallback ────────────────────────────────────────────────────────

async def always_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Any unexpected text outside a conversation restarts the flow."""
    return await start(update, context)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_text(update, "✅ Bot is awake and listening. Send /start to file a complaint.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Handler error: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Send /start to try again."
            )
        except Exception:
            pass


# ── post_init — clean up menu button ─────────────────────────────────────────

async def post_init(application: Application) -> None:
    bot = application.bot
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        logger.warning("Could not delete webhook: %s", e)
    try:
        # Commands menu — never a Mini App / Web App (those are blocked on Play Store Telegram).
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await bot.set_my_commands([
            BotCommand("start",  "Start a new complaint"),
            BotCommand("status", "Check ticket status"),
            BotCommand("cancel", "Cancel current complaint"),
            BotCommand("help",   "How to use this bot"),
            BotCommand("ping",   "Check that the bot is awake"),
        ])
        logger.info("Menu button set to commands (not Mini App)")
    except Exception as e:
        logger.warning("Could not reset menu button: %s", e)


# ── Build application ─────────────────────────────────────────────────────────

def build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",      start),
            CommandHandler("new",        start),
            CommandHandler("complaint",  start),
            CommandHandler("help",       start),
            MessageHandler(~filters.COMMAND, always_open),
        ],
        states={
            S_CAMPUS: [
                CallbackQueryHandler(cb_campus, pattern=f"^{CB_CAMPUS}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tap_buttons_or_restart),
            ],
            S_DEPARTMENT: [
                CallbackQueryHandler(cb_department, pattern=f"^{CB_DEPT}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, text_department),
            ],
            S_IDENTITY: [
                CallbackQueryHandler(cb_identity, pattern=f"^{CB_IDENTITY}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tap_buttons_or_restart),
            ],
            S_NAMED_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_named_info),
            ],
            S_CATEGORY: [
                CallbackQueryHandler(cb_category, pattern=f"^{CB_CATEGORY}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tap_buttons_or_restart),
            ],
            S_BUILDING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_building),
            ],
            S_FLOOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_floor),
            ],
            S_ROOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_room),
            ],
            S_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_description),
            ],
            S_EVIDENCE: [
                MessageHandler(filters.PHOTO,                        collect_evidence_photo),
                MessageHandler(filters.VIDEO,                        collect_evidence_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND,     collect_evidence_skip),
            ],
            S_CONFIRM: [
                CallbackQueryHandler(cb_confirm,       pattern=f"^{CB_CONFIRM}(yes|no)$"),
                CallbackQueryHandler(cb_new_complaint, pattern=f"^{CB_CONFIRM}new$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tap_buttons_or_restart),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start",  start),
            CommandHandler("new",    start),
            CommandHandler("help",   start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, tap_buttons_or_restart),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ping",   cmd_ping))
    app.add_handler(CommandHandler("help",   start))
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(
        CallbackQueryHandler(cb_new_complaint, pattern=f"^{CB_CONFIRM}new_outside$")
    )
    app.add_error_handler(on_error)
    return app


# ── Notify student (called from Streamlit dashboard) ─────────────────────────

async def _send_notification(token: str, chat_id: str, message: str) -> None:
    app = Application.builder().token(token).build()
    async with app:
        await app.bot.send_message(
            chat_id    = int(chat_id),
            text       = message,
            parse_mode = ParseMode.MARKDOWN,
        )


def notify_student(chat_id: str, message: str) -> bool:
    """Synchronously send a Telegram message to a student.
    Called from the Streamlit dashboard when an admin updates a ticket.
    Returns True on success, False on failure.
    """
    token = token_from_anywhere()
    if not token or not chat_id:
        return False
    try:
        asyncio.run(_send_notification(token, chat_id, message))
        return True
    except Exception as exc:
        logger.error("notify_student failed: %s", exc)
        return False


# ── Keep-alive ping ───────────────────────────────────────────────────────────

def _start_keepalive_ping() -> None:
    global _keepalive_on
    if _keepalive_on:
        return
    url = _app_url_from_anywhere()
    if not url:
        logger.warning("STREAMLIT_APP_URL is not set — bot may sleep on Streamlit Cloud.")
        return
    _keepalive_on = True

    ping_url = url.rstrip("/") + "/?keepalive=1"

    def _loop() -> None:
        time.sleep(20)
        while True:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(ping_url, headers={"User-Agent": "BDU-BotKeepAlive/1.0"}),
                    timeout=20,
                )
                logger.info("Keep-alive ping ok")
            except Exception as exc:
                logger.warning("Keep-alive ping failed: %s", exc)
            time.sleep(90)

    threading.Thread(target=_loop, daemon=True, name="keepalive").start()


# ── Standalone main (Procfile: worker: python bot.py) ─────────────────────────

def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    token  = _token()
    ok, info = validate_telegram_token(token)
    if not ok:
        logger.error(info)
        raise SystemExit(info)
    logger.info("Telegram token ok — @%s", info)
    count  = 0
    _start_keepalive_ping()
    while True:
        try:
            logger.info("Bot starting (restart #%d): %s", count, BOT_URL)
            build_application(token).run_polling(
                allowed_updates      = Update.ALL_TYPES,
                drop_pending_updates = False,
                poll_interval        = 0.5,
                timeout              = 30,
                stop_signals         = None,
            )
            break  # clean exit
        except Exception as exc:
            count += 1
            wait   = min(120, 5 * count)
            logger.error("Bot crashed (#%d), retry in %ds: %s", count, wait, exc)
            time.sleep(wait)


# ── Background-thread entry (Streamlit fallback) ──────────────────────────────

def start_bot_background() -> str:
    global _bot_thread, _bot_status, _bot_error, _restart_count

    if _bot_thread is not None and _bot_thread.is_alive():
        return _bot_status

    token = token_from_anywhere()
    if not token:
        _bot_status = "missing_token"
        _bot_error  = "TELEGRAM_BOT_TOKEN is not set."
        return _bot_status

    ok, info = validate_telegram_token(token)
    if not ok:
        _bot_status = "bad_token"
        _bot_error  = info
        logger.error(info)
        return _bot_status
    logger.info("Telegram token ok — @%s", info)

    os.chdir(Path(__file__).resolve().parent)
    _start_keepalive_ping()
    _bot_status = "starting"
    _bot_error  = ""

    def _run() -> None:
        global _bot_status, _bot_error, _restart_count
        while True:
            try:
                _bot_status = "running"
                logger.info("Bot polling (bg thread #%d)", _restart_count)
                build_application(token).run_polling(
                    allowed_updates      = Update.ALL_TYPES,
                    drop_pending_updates = False,
                    poll_interval        = 0.5,
                    timeout              = 30,
                    stop_signals         = None,
                )
                _bot_status = "stopped"
                break
            except Exception as exc:
                _restart_count += 1
                _bot_error  = str(exc)
                wait        = min(120, 5 * _restart_count)
                _bot_status = f"restarting #{_restart_count}"
                logger.error("Bot crashed (bg #%d), retry in %ds: %s", _restart_count, wait, exc)
                time.sleep(wait)

    _bot_thread = threading.Thread(target=_run, name="telegram-bot", daemon=True)
    _bot_thread.start()
    return "started"


# ── Script entry ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _running_under_streamlit():
        import streamlit as st
        st.set_page_config(page_title="Union Student Bot", page_icon="🎓")
        start_bot_background()
        st.error("❌ Wrong entry point. Main file path must be app.py.")
        s, e = bot_status()
        st.info(f"Bot status: {s}")
        if e:
            st.warning(e)
        st.link_button("Open Telegram bot", BOT_URL)
        st.stop()
    main()
