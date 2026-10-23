"""BDU Student Union Telegram Bot.

HOW TO RUN (the ONLY correct way):
    python bot.py

This file is a standalone bot process. It must NOT be run inside Streamlit.
Deploy it on Railway / Render / any free Python host separately from the
Streamlit dashboard.

Set one environment variable:
    TELEGRAM_BOT_TOKEN = your token from @BotFather

Steps 1-8 of the complaint flow:
  1. Campus
  2. Department
  3. Identity (reveal / anonymous)
  4. Problem category
  5. Location (building / floor / room)
  6. Description
  7. Photo or video (optional)
  8. Summary → confirm → ticket issued
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

from constants import (
    CAMPUSES,
    CATEGORIES,
    DEPARTMENTS,
    IDENTITY_ANON,
    IDENTITY_NAMED,
    SKIP,
    STATUS_AM,
)
from data_manager import EVIDENCE_DIR, load_secrets, read_complaints, save_complaint

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("bdu.bot")

BOT_USERNAME = "unionstudent_complaints_bot"
BOT_URL      = f"https://t.me/{BOT_USERNAME}"

# ── Conversation states ────────────────────────────────────────────────────────
(
    S_CAMPUS, S_DEPARTMENT, S_IDENTITY, S_NAMED_INFO,
    S_CATEGORY, S_BUILDING, S_FLOOR, S_ROOM,
    S_DESCRIPTION, S_EVIDENCE, S_CONFIRM,
) = range(11)

# ── Callback prefixes ──────────────────────────────────────────────────────────
C_CAMPUS    = "cs:"
C_DEPT      = "dp:"
C_IDENTITY  = "id:"
C_CATEGORY  = "ct:"
C_CONFIRM   = "cf:"

# ── Keyboard builders ──────────────────────────────────────────────────────────

def _ik(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row]
        for row in rows
    ])

def _rk(opts: list[str], cols: int = 1) -> ReplyKeyboardMarkup:
    rows = [opts[i:i+cols] for i in range(0, len(opts), cols)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)

def _kb_campus()    -> InlineKeyboardMarkup:
    return _ik([[(c, f"{C_CAMPUS}{c}")] for c in CAMPUSES])

def _kb_department() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(DEPARTMENTS), 2):
        pair = DEPARTMENTS[i:i+2]
        rows.append([(d, f"{C_DEPT}{d}") for d in pair])
    return _ik(rows)

def _kb_identity()  -> InlineKeyboardMarkup:
    return _ik([
        [(IDENTITY_NAMED, f"{C_IDENTITY}named")],
        [(IDENTITY_ANON,  f"{C_IDENTITY}anon")],
    ])

def _kb_category()  -> InlineKeyboardMarkup:
    return _ik([[(c, f"{C_CATEGORY}{c}")] for c in CATEGORIES])

def _kb_confirm()   -> InlineKeyboardMarkup:
    return _ik([
        [("✅ Submit Complaint", f"{C_CONFIRM}yes")],
        [("✏️ Start Over",       f"{C_CONFIRM}no")],
    ])

# ── Helpers ────────────────────────────────────────────────────────────────────

def _bar(step: int, total: int = 8) -> str:
    return "🟦" * step + "⬜" * (total - step) + f"  Step {step}/{total}"

def _safe(v: str | None) -> str:
    s = (v or "").strip()
    for c in ("_", "*", "`", "[", "]"):
        s = s.replace(c, " ")
    return s or "—"

def _is_restart(text: str | None) -> bool:
    # Only slash-commands restart — never plain words like "new", "hi", "hello"
    # which students may type as their name, building, or description
    return (text or "").strip().lower() in {"start", "/start", "/new", "/help"}

def _is_skip(text: str | None) -> bool:
    t = (text or "").strip().lower()
    return t in {"skip", "⏭ skip photo/video", "⏭ skip"}

def _summary(d: dict) -> str:
    anon  = d.get("is_anonymous", False)
    loc   = " / ".join(p for p in [d.get("building",""), d.get("floor",""), d.get("room","")] if p) or "—"
    media = "Attached ✅" if (d.get("evidence_path") or d.get("evidence_file_id")) else "None"
    return (
        "📋 *Complaint Summary*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🏫 *Campus:*      {_safe(d.get('campus'))}\n"
        f"🎓 *Department:*  {_safe(d.get('department'))}\n"
        f"👤 *Identity:*    {'Anonymous 🕵' if anon else _safe(d.get('full_name'))}\n"
        f"🪪 *Student ID:*  {'—' if anon else _safe(d.get('student_id'))}\n"
        f"📞 *Phone:*       {'—' if anon else _safe(d.get('phone'))}\n"
        f"🗂 *Category:*   {_safe(d.get('category'))}\n"
        f"📍 *Location:*   {loc}\n"
        f"📸 *Media:*       {media}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *Description:*\n{_safe(d.get('description'))}"
    )

async def _reply(update: Update, text: str, **kw) -> None:
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.reply_text(text, **kw)
    except Exception:
        kw.pop("parse_mode", None)
        kw.pop("reply_markup", None)
        await msg.reply_text(text)

async def _edit(update: Update, text: str, **kw) -> None:
    q = update.callback_query
    if not q:
        await _reply(update, text, **kw)
        return
    try:
        await q.edit_message_text(text, **kw)
    except Exception:
        kw.pop("parse_mode", None)
        try:
            await q.edit_message_text(text)
        except Exception:
            await _reply(update, text)

# ── Step 1 — /start ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
    name = _safe(update.effective_user.first_name if update.effective_user else "Student")
    await _reply(
        update,
        f"ሰላም {name}! 👋\n\n"
        "🎓 *Bahir Dar University*\n"
        "*Student Union Complaint Bot* ✅\n"
        "Available 24/7 — your voice matters.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(1)}\n"
        "*Select your Campus:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_campus(),
    )
    return S_CAMPUS

# ── Step 1 → Campus ────────────────────────────────────────────────────────────

async def cb_campus(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    campus = q.data[len(C_CAMPUS):]
    ctx.user_data["campus"] = campus
    await _edit(update,
        f"✅ Campus: *{_safe(campus)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(2)}\n"
        "*Select or type your Department:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_department(),
    )
    return S_DEPARTMENT

# ── Step 2 → Department ────────────────────────────────────────────────────────

async def cb_dept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    dept = q.data[len(C_DEPT):]
    ctx.user_data["department"] = dept
    await _edit(update,
        f"✅ Department: *{_safe(dept)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(3)}\n"
        "*How should your identity be handled?*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_identity(),
    )
    return S_IDENTITY

async def text_dept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text):
        return await cmd_start(update, ctx)
    if len(text) < 2:
        await _reply(update, "Please type your department (min 2 chars) or pick from the list above.",
                     reply_markup=_kb_department())
        return S_DEPARTMENT
    ctx.user_data["department"] = text
    await _reply(update,
        f"✅ Department: *{_safe(text)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(3)}\n"
        "*How should your identity be handled?*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_identity(),
    )
    return S_IDENTITY

# ── Step 3 → Identity ─────────────────────────────────────────────────────────

async def cb_identity(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    choice = q.data[len(C_IDENTITY):]
    if choice == "anon":
        ctx.user_data["is_anonymous"] = True
        await _edit(update,
            "🕵 Identity kept *Anonymous*.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"{_bar(4)}\n"
            "*Select the problem category:*\n"
            "━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_kb_category(),
        )
        return S_CATEGORY
    ctx.user_data["is_anonymous"] = False
    await _edit(update,
        "👤 Identity will be *revealed*.\n\n"
        "Please enter your *Full Name*:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_NAMED_INFO

async def collect_named(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text):
        return await cmd_start(update, ctx)
    d = ctx.user_data
    if "full_name" not in d:
        if len(text) < 2:
            await _reply(update, "Please enter your full name (at least 2 characters):")
            return S_NAMED_INFO
        d["full_name"] = text
        await _reply(update,
            f"✅ Name: *{_safe(text)}*\n\nEnter your *Student ID* (e.g. BDU1234567):",
            parse_mode=ParseMode.MARKDOWN)
        return S_NAMED_INFO
    if "student_id" not in d:
        d["student_id"] = text
        await _reply(update,
            f"✅ Student ID: *{_safe(text)}*\n\nEnter your *Phone Number* (e.g. 0912345678):",
            parse_mode=ParseMode.MARKDOWN)
        return S_NAMED_INFO
    d["phone"] = text
    await _reply(update,
        f"✅ Phone: *{_safe(text)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(4)}\n"
        "*Select the problem category:*\n"
        "━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_category(),
    )
    return S_CATEGORY

# ── Step 4 → Category ─────────────────────────────────────────────────────────

async def cb_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    cat = q.data[len(C_CATEGORY):]
    ctx.user_data["category"] = cat
    await _edit(update,
        f"✅ Category: *{_safe(cat)}*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(5)}\n"
        "*Location Details*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Enter *Building Name or Block Number*\n"
        "(e.g. Block A, Dorm 5)  — or type `skip`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return S_BUILDING

# ── Step 5 → Location ─────────────────────────────────────────────────────────

async def collect_building(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text): return await cmd_start(update, ctx)
    ctx.user_data["building"] = "" if text.lower() == "skip" else text
    await _reply(update,
        "Enter *Floor Number* (e.g. Ground, 1st, 2nd)  — or type `skip`",
        parse_mode=ParseMode.MARKDOWN)
    return S_FLOOR

async def collect_floor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text): return await cmd_start(update, ctx)
    ctx.user_data["floor"] = "" if text.lower() == "skip" else text
    await _reply(update,
        "Enter *Room / Dorm Number*  — or type `skip`",
        parse_mode=ParseMode.MARKDOWN)
    return S_ROOM

async def collect_room(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text): return await cmd_start(update, ctx)
    ctx.user_data["room"] = "" if text.lower() == "skip" else text
    await _reply(update,
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(6)}\n"
        "*Describe the Problem*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Write a detailed description. The more detail the faster it gets resolved. 📝",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return S_DESCRIPTION

# ── Step 6 → Description ──────────────────────────────────────────────────────

async def collect_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text): return await cmd_start(update, ctx)
    if len(text) < 10:
        await _reply(update, "❗ Please write more detail (at least 10 characters).")
        return S_DESCRIPTION
    ctx.user_data["description"] = text
    await _reply(update,
        "✅ Description saved!\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(7)}\n"
        "*Attach Photo or Video (Optional)*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📸 Send a photo or video, or tap Skip 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_rk([SKIP]),
    )
    return S_EVIDENCE

# ── Step 7 → Evidence ─────────────────────────────────────────────────────────

async def _store(update: Update, suffix: str, media) -> tuple[str, str]:
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        f = await media.get_file()
        fn = f"{update.effective_user.id}_{int(time.time())}{suffix}"
        p  = EVIDENCE_DIR / fn
        await f.download_to_drive(p)
        return str(p), media.file_id
    except Exception:
        log.exception("Evidence download failed")
        return "", getattr(media, "file_id", "")

async def ev_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    p, fid = await _store(update, ".jpg", update.message.photo[-1])
    ctx.user_data.update(evidence_path=p, evidence_file_id=fid)
    await _reply(update, "✅ Photo attached!", reply_markup=ReplyKeyboardRemove())
    return await _show_summary(update, ctx)

async def ev_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    p, fid = await _store(update, ".mp4", update.message.video)
    ctx.user_data.update(evidence_path=p, evidence_file_id=fid)
    await _reply(update, "✅ Video attached!", reply_markup=ReplyKeyboardRemove())
    return await _show_summary(update, ctx)

async def ev_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if _is_restart(text):
        return await cmd_start(update, ctx)
    if not _is_skip(text):
        await _reply(update,
            "Please send a photo/video or tap *Skip Photo/Video* 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_rk([SKIP]))
        return S_EVIDENCE
    ctx.user_data.update(evidence_path="", evidence_file_id="")
    return await _show_summary(update, ctx)

# ── Step 8 → Summary ──────────────────────────────────────────────────────────

async def _show_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await _reply(update,
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_bar(8)}\n"
        "*Review & Confirm*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        + _summary(ctx.user_data)
        + "\n\n━━━━━━━━━━━━━━━━━━━\n"
        "Everything correct? Tap *Submit* to register.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb_confirm(),
    )
    return S_CONFIRM

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    choice = q.data[len(C_CONFIRM):]

    if choice == "no":
        await q.edit_message_text("↩️ Cancelled. Send /start to begin again.")
        ctx.user_data.clear()
        return ConversationHandler.END

    user = update.effective_user
    d    = ctx.user_data
    try:
        saved = save_complaint(
            telegram_id       = str(user.id) if user else "",
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
        log.exception("save_complaint failed")
        await q.edit_message_text(
            "⚠️ Could not save your complaint. Please try /start again.")
        ctx.user_data.clear()
        return ConversationHandler.END

    ticket = saved["id"]
    ctx.user_data.clear()

    # ── Notify admin via Telegram ─────────────────────────────────────────────
    admin_id = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if admin_id:
        anon = bool(d.get("is_anonymous"))
        loc  = " / ".join(p for p in [
            saved.get("building",""), saved.get("floor",""), saved.get("room","")
        ] if p) or "—"
        admin_msg = (
            f"🆕 *New Complaint — {ticket}*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🏫 Campus:     {saved.get('campus','—')}\n"
            f"🎓 Department: {saved.get('department','—')}\n"
            f"🗂 Category:  {saved.get('category','—')}\n"
            f"📍 Location:  {loc}\n"
            f"👤 Identity:  {'Anonymous 🕵' if anon else saved.get('full_name','—')}\n"
            f"📅 Time:      {saved.get('created_at','—')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"📝 *Description:*\n{saved.get('description','—')}"
        )
        try:
            await ctx.bot.send_message(
                chat_id    = int(admin_id),
                text       = admin_msg,
                parse_mode = ParseMode.MARKDOWN,
            )
        except Exception as e:
            log.warning("Admin notify failed: %s", e)

    await q.edit_message_text(
        "🎉 *Complaint Registered!*\n\n"
        f"🎫 *Ticket ID:* `{ticket}`\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📌 Save this Ticket ID!\n"
        f"Check status: `/status {ticket}`\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "The Student Union will review and respond. ✊\n"
        "You will be notified here when the status changes.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_ik([[("➕ New Complaint", f"{C_CONFIRM}new")]]),
    )
    return ConversationHandler.END

async def cb_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    return await cmd_start(update, ctx)

# ── /status ────────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ticket_id = " ".join(ctx.args or []).strip()
    if not ticket_id:
        await _reply(update,
            "Send: `/status #BDU-2026-112`\n(replace with your ticket number)",
            parse_mode=ParseMode.MARKDOWN)
        return
    try:
        df    = read_complaints()
        match = df[df["id"].astype(str).str.strip() == ticket_id.strip()]
    except Exception:
        await _reply(update, "⚠️ Could not read data. Please try later.")
        return
    if match.empty:
        await _reply(update,
            f"❌ Ticket `{ticket_id}` not found.\nExample: `/status #BDU-2026-112`",
            parse_mode=ParseMode.MARKDOWN)
        return
    r   = match.iloc[0]
    sv  = str(r["status"])
    sl  = STATUS_AM.get(sv, sv)
    pct = {"Pending": 5, "Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)
    bar = "🟩" * (pct // 10) + "⬜" * (10 - pct // 10)
    loc = " / ".join(str(r.get(f,"")) for f in ("building","floor","room") if r.get(f,""))
    res = str(r.get("resolution_note","")) if "resolution_note" in r.index else ""
    msg = (
        f"🎫 *{r['id']}*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🏫 {r['campus']}\n"
        f"🎓 {r.get('department','—')}\n"
        f"🗂 {r['category']}\n"
        f"📍 {loc or '—'}\n"
        f"📅 {r['created_at']}\n\n"
        f"📊 Status: {sl}\n"
        f"🔢 {bar} {pct}%"
    )
    if res.strip():
        msg += f"\n\n💬 *Union Response:*\n{res}"
    else:
        msg += "\n\n⏳ No response yet. Check back later."
    await _reply(update, msg, parse_mode=ParseMode.MARKDOWN)

# ── /cancel, /ping ─────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await _reply(update, "❌ Cancelled. Send /start to begin.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "✅ Bot is alive. Send /start to file a complaint.")

async def _tap_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Shown when student types text while an inline keyboard is waiting for a tap."""
    await _reply(update, "👆 Please tap one of the buttons above to continue.\n"
                         "Or send /cancel to start over.")
    return None  # stay in the same state

async def fallback_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Called when a message arrives outside any active conversation state.
    Tells the student to use /start — never silently restarts mid-flow.
    """
    await _reply(
        update,
        "👋 Send /start to begin a new complaint.\n"
        "If you were in the middle of a complaint, the server may have restarted — "
        "please send /start to begin again.",
    )
    return ConversationHandler.END

# ── post_init ──────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook cleared, pending updates dropped")
    except Exception as e:
        log.warning("delete_webhook: %s", e)
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await app.bot.set_my_commands([
            BotCommand("start",  "Start a new complaint"),
            BotCommand("status", "Check ticket status"),
            BotCommand("ping",   "Check bot is alive"),
            BotCommand("cancel", "Cancel and restart"),
        ])
    except Exception as e:
        log.warning("set_my_commands: %s", e)

# ── notify_student (called from Streamlit dashboard) ──────────────────────────

async def _async_notify(token: str, chat_id: str, message: str) -> None:
    app = Application.builder().token(token).build()
    async with app:
        await app.bot.send_message(
            chat_id=int(chat_id), text=message, parse_mode=ParseMode.MARKDOWN)

def notify_student(chat_id: str, message: str) -> bool:
    token = _get_token()
    if not token or not chat_id:
        return False
    try:
        asyncio.run(_async_notify(token, chat_id, message))
        return True
    except Exception as exc:
        log.error("notify_student: %s", exc)
        return False

# ── Token helpers ──────────────────────────────────────────────────────────────

def _get_token() -> str:
    t = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if t:
        return t
    try:
        sec = load_secrets()
        t   = str(sec.get("TELEGRAM_BOT_TOKEN", "")).strip()
        if t:
            return t
    except Exception:
        pass
    try:
        import streamlit as st
        t = str(st.secrets.get("TELEGRAM_BOT_TOKEN", "")).strip()
        if t:
            return t
    except Exception:
        pass
    return ""

# used by app.py
token_from_anywhere = _get_token

# ── Background bot state (shared across Streamlit reruns) ─────────────────────
_bot_thread: threading.Thread | None = None
_bot_running: bool = False
_bot_error:   str  = ""

# Lock-file used as a cross-rerun / cross-process signal on Streamlit Cloud
_BOT_LOCK = Path("/tmp/bot.running")
_BOT_ERR  = Path("/tmp/bot.error")


def _mark_running() -> None:
    try:
        _BOT_LOCK.write_text("1")
        _BOT_ERR.unlink(missing_ok=True)
    except Exception:
        pass


def _mark_error(msg: str) -> None:
    try:
        _BOT_LOCK.unlink(missing_ok=True)
        _BOT_ERR.write_text(msg[:500])
    except Exception:
        pass


def _read_lock() -> tuple[bool, str]:
    """Returns (is_running, error_message)."""
    running = _BOT_LOCK.exists()
    err = ""
    try:
        if _BOT_ERR.exists():
            err = _BOT_ERR.read_text()
    except Exception:
        pass
    return running, err


def _run_polling_loop(token: str) -> None:
    """Run the PTB polling loop forever inside a daemon thread."""
    global _bot_running, _bot_error
    count = 0
    while True:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("closed")
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())

            _bot_running = True
            _bot_error   = ""
            _mark_running()
            build_app(token).run_polling(
                allowed_updates      = Update.ALL_TYPES,
                drop_pending_updates = True,
                poll_interval        = 1.0,
                timeout              = 30,
                stop_signals         = None,
            )
            break  # clean exit (shouldn't happen normally)
        except Exception as exc:
            _bot_running = False
            _bot_error   = str(exc)
            _mark_error(_bot_error)
            count += 1
            wait = min(120, 10 * count)
            log.error("Bot crash #%d — retry in %ds: %s", count, wait, exc)
            time.sleep(wait)


# Track when the thread was started so we can give it a grace period
_bot_started_at: float = 0.0


def start_bot_background() -> str:
    """Called by app.py on every Streamlit rerun.

    Spawns exactly one daemon thread per process.
    Returns: 'running', 'starting', 'missing_token', or 'error'.
    """
    global _bot_thread, _bot_running, _bot_error, _bot_started_at

    token = _get_token()
    if not token:
        _bot_error = "TELEGRAM_BOT_TOKEN not set"
        return "missing_token"

    # Thread already alive — nothing to do
    if _bot_thread is not None and _bot_thread.is_alive():
        if _bot_running:
            return "running"
        # Give the thread a 15-second grace period before calling it stuck
        if time.time() - _bot_started_at < 15:
            return "starting"
        return "running"   # assume running after grace period

    # Spawn fresh thread
    _bot_running     = False
    _bot_error       = ""
    _bot_started_at  = time.time()
    _bot_thread = threading.Thread(
        target   = _run_polling_loop,
        args     = (token,),
        daemon   = True,
        name     = "telegram-bot",
    )
    _bot_thread.start()
    log.info("Bot thread started (daemon)")
    return "starting"


def bot_status() -> tuple[str, str]:
    """Return (status_label, error_message) for the Streamlit header."""
    global _bot_thread, _bot_running, _bot_error

    token = _get_token()
    if not token:
        return "missing_token", "TELEGRAM_BOT_TOKEN not set in secrets."

    # Check lock-file first (survives across Streamlit reruns)
    lock_running, lock_err = _read_lock()
    if lock_err and ("401" in lock_err or "Unauthorized" in lock_err):
        return "bad_token", lock_err

    if _bot_thread is None:
        # No thread in this process — check lock file
        if lock_running:
            return "running ✅", ""
        return "not started", ""

    if not _bot_thread.is_alive():
        if lock_err:
            return "stopped", lock_err
        return "stopped", _bot_error or "Thread exited unexpectedly."

    # Thread is alive
    if _bot_running or lock_running:
        return "running ✅", ""

    # Thread alive, not yet confirmed — give 15-second grace period
    if time.time() - _bot_started_at < 15:
        return "starting…", ""

    # After grace period, assume it's running (polling loop sets flag async)
    return "running ✅", ""

# ── Build application ──────────────────────────────────────────────────────────

def build_app(token: str) -> Application:
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
            CommandHandler("start",     cmd_start),
            CommandHandler("new",       cmd_start),
            CommandHandler("complaint", cmd_start),
            CommandHandler("help",      cmd_start),
            # NOTE: No MessageHandler here — a plain text message should NOT
            # restart the conversation mid-flow after a server restart.
            # Students must type /start to begin.
        ],
        states={
            S_CAMPUS: [
                CallbackQueryHandler(cb_campus, pattern=f"^{C_CAMPUS}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _tap_prompt),
            ],
            S_DEPARTMENT: [
                CallbackQueryHandler(cb_dept, pattern=f"^{C_DEPT}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, text_dept),
            ],
            S_IDENTITY: [
                CallbackQueryHandler(cb_identity, pattern=f"^{C_IDENTITY}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _tap_prompt),
            ],
            S_NAMED_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_named),
            ],
            S_CATEGORY: [
                CallbackQueryHandler(cb_category, pattern=f"^{C_CATEGORY}"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _tap_prompt),
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
                MessageHandler(filters.PHOTO, ev_photo),
                MessageHandler(filters.VIDEO, ev_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ev_skip),
            ],
            S_CONFIRM: [
                CallbackQueryHandler(cb_confirm, pattern=f"^{C_CONFIRM}(yes|no)$"),
                CallbackQueryHandler(cb_new,     pattern=f"^{C_CONFIRM}new$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _tap_prompt),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
            CommandHandler("new",    cmd_start),
            # Catch any unmatched text DURING a conversation and prompt restart
            MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_restart),
        ],
        allow_reentry=True,
        # Persist conversation state key by chat_id so a Streamlit rerun
        # does not lose mid-flow context (PTB keeps state in memory per process)
        name="bdu_complaint",
        persistent=False,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ping",   cmd_ping))
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CallbackQueryHandler(cb_new, pattern=f"^{C_CONFIRM}new$"))
    return app

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    os.chdir(Path(__file__).resolve().parent)
    token = _get_token()
    if not token:
        sys.exit("ERROR: TELEGRAM_BOT_TOKEN is not set.")

    # ── Health server (keeps Render free tier alive) ──────────────────────────
    port = int(os.getenv("PORT", "8080"))

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"BDU Bot alive")
        def log_message(self, *a): pass

    srv = HTTPServer(("0.0.0.0", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="health").start()
    log.info("Health server on port %d", port)

    # ── Self-ping every 4 min so Render never spins down ─────────────────────
    service_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if service_url:
        def _ping():
            time.sleep(60)
            while True:
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            service_url,
                            headers={"User-Agent": "BDU-SelfPing/1.0"},
                        ),
                        timeout=15,
                    )
                    log.info("Self-ping ok")
                except Exception as e:
                    log.warning("Self-ping failed: %s", e)
                time.sleep(240)
        threading.Thread(target=_ping, daemon=True, name="self-ping").start()
        log.info("Self-ping → %s", service_url)

    # ── Bot polling loop (Python 3.12 event loop fix) ────────────────────────
    log.info("Starting bot — %s", BOT_URL)
    count = 0
    while True:
        try:
            # Python 3.12 no longer auto-creates an event loop — create one explicitly
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("closed")
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())

            build_app(token).run_polling(
                allowed_updates      = Update.ALL_TYPES,
                drop_pending_updates = True,
                poll_interval        = 1.0,
                timeout              = 30,
                stop_signals         = None,
            )
            break
        except Exception as exc:
            count += 1
            wait = min(60, 5 * count)
            log.error("Crash #%d — retry in %ds: %s", count, wait, exc)
            time.sleep(wait)

if __name__ == "__main__":
    main()
