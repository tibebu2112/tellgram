"""Telegram bot — runs as a standalone worker process (Procfile worker:)
or as a background thread inside Streamlit Cloud.

ROOT CAUSE OF SILENT BOT:
  Streamlit Cloud sleeps the entire app after ~15 min with no browser
  traffic.  When it sleeps every thread inside it (including this bot)
  dies.  The fix is to run this file as a *separate* worker process via
  the Procfile line:
      worker: python bot.py
  That process is never put to sleep by Streamlit's idle timer.

FIXES IN THIS VERSION:
  1. Standalone process    — Procfile runs this as a worker, always alive
  2. Infinite restart loop — auto-restarts on any crash with back-off
  3. post_init             — removes Web App menu button (fixes Play Store error)
  4. allow_reentry=True    — students never get stuck mid-flow
  5. Generous timeouts     — handles slow mobile / African internet
  6. drop_pending_updates  — clears backlog of unread messages on startup
  7. HTTP keep-alive ping  — pings Streamlit app URL every 4 min so the
                             dashboard stays warm too
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from telegram import (
    MenuButtonDefault,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
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
BOT_URL = f"https://t.me/{BOT_USERNAME}"

# Conversation states
(
    CAMPUS,
    CATEGORY,
    DESCRIPTION,
    EVIDENCE,
    LOCATION,
    IDENTITY,
    NAME,
    PHONE,
    DEPARTMENT,
) = range(9)

# ── Global status (used by Streamlit dashboard to show bot health) ─────────────
_bot_thread: threading.Thread | None = None
_bot_status = "stopped"
_bot_error = ""
_restart_count = 0


# ── Token helpers ──────────────────────────────────────────────────────────────

def _running_under_streamlit() -> bool:
    return bool(
        os.getenv("STREAMLIT_SERVER_PORT")
        or os.getenv("STREAMLIT_RUNTIME")
        or os.getenv("STREAMLIT_SERVER_HEADLESS")
    )


def token_from_anywhere() -> str:
    """Return the bot token from env → secrets.toml → st.secrets (in that order)."""
    env = os.getenv("TELEGRAM_BOT_TOKEN")
    if env:
        return env.strip()
    file_secrets = load_secrets()
    if file_secrets.get("TELEGRAM_BOT_TOKEN"):
        return str(file_secrets["TELEGRAM_BOT_TOKEN"]).strip()
    try:
        import streamlit as st
        value = st.secrets.get("TELEGRAM_BOT_TOKEN")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return ""


def _app_url_from_anywhere() -> str:
    """Return the Streamlit app URL for keep-alive pings, or empty string."""
    env = os.getenv("STREAMLIT_APP_URL")
    if env:
        return env.strip()
    file_secrets = load_secrets()
    if file_secrets.get("STREAMLIT_APP_URL"):
        return str(file_secrets["STREAMLIT_APP_URL"]).strip()
    try:
        import streamlit as st
        value = st.secrets.get("STREAMLIT_APP_URL")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return ""


def bot_status() -> tuple[str, str]:
    return _bot_status, _bot_error


def _token() -> str:
    token = token_from_anywhere()
    if not token:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN in .streamlit/secrets.toml or Streamlit Cloud Secrets."
        )
    return token


# ── Keyboard helper ────────────────────────────────────────────────────────────

def _keyboard(options: list[str], columns: int = 1) -> ReplyKeyboardMarkup:
    rows = [options[i: i + columns] for i in range(0, len(options), columns)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def _is_skip(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in {SKIP.lower(), "skip", "ዝለል", "⏭ ዝለል"}


# ── Conversation handlers ──────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user = update.effective_user
    greet = user.first_name if user else "ተማሪ"
    await update.message.reply_text(
        f"ሰላም {greet}! 👋\n\n"
        "🎓 የባህር ዳር ዩኒቨርሲቲ\n"
        "የተማሪዎች ህብረት ቅሬታ ቦት ✅\n"
        "24/7 ክፍት ነው\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 ደረጃ 1 ከ 4\n"
        "ካምፓስዎን ይምረጡ 👇\n"
        "━━━━━━━━━━━━━━━\n\n"
        "/status #BDU-XXXX — ቅሬታ ሁኔታ\n"
        "/cancel — ለመውጣት",
        reply_markup=_keyboard(CAMPUSES),
    )
    return CAMPUS


async def collect_campus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice not in CAMPUSES:
        await update.message.reply_text(
            "❗ ከዝርዝሩ ካምፓስ ይምረጡ 👇",
            reply_markup=_keyboard(CAMPUSES),
        )
        return CAMPUS
    context.user_data["campus"] = choice
    await update.message.reply_text(
        f"✅ ካምፓስ: {choice}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 ደረጃ 2 ከ 4\n"
        "የችግሩን ዘርፍ ይምረጡ 👇\n"
        "━━━━━━━━━━━━━━━",
        reply_markup=_keyboard(CATEGORIES),
    )
    return CATEGORY


async def collect_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice not in CATEGORIES:
        await update.message.reply_text(
            "❗ ከዝርዝሩ ዘርፍ ይምረጡ 👇",
            reply_markup=_keyboard(CATEGORIES),
        )
        return CATEGORY
    context.user_data["category"] = choice
    await update.message.reply_text(
        f"✅ ዘርፍ: {choice}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 ደረጃ 3 ከ 4\n"
        "የችግሩን መግለጫ ይጻፉ\n"
        "(ቢያንስ 5 ፊደላት)\n"
        "━━━━━━━━━━━━━━━",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESCRIPTION


async def collect_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if len(text) < 5:
        await update.message.reply_text(
            "❗ ትንሽ ዝርዝር ይጻፉ (ቢያንስ 5 ፊደላት)።"
        )
        return DESCRIPTION
    context.user_data["description"] = text
    await update.message.reply_text(
        "✅ መግለጫ ተቀምጧል!\n\n"
        "📎 አማራጭ: ፎቶ ወይም ቪዲዮ ማስረጃ ይላኩ\n"
        "ከሌለዎት 👇 ዝለል ይጫኑ",
        reply_markup=_keyboard([SKIP]),
    )
    return EVIDENCE


async def _store_file(update: Update, suffix: str, media) -> tuple[str, str]:
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        telegram_file = await media.get_file()
        filename = f"{update.effective_user.id}_{int(time.time())}{suffix}"
        path = EVIDENCE_DIR / filename
        await telegram_file.download_to_drive(path)
        return str(path), media.file_id
    except Exception:
        logger.exception("Could not download evidence file")
        return "", getattr(media, "file_id", "")


async def collect_evidence_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    path, file_id = await _store_file(update, ".jpg", photo)
    context.user_data["evidence_path"] = path
    context.user_data["evidence_file_id"] = file_id
    await update.message.reply_text("✅ ፎቶ ማስረጃ ተቀምጧል!")
    return await _ask_location(update)


async def collect_evidence_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    path, file_id = await _store_file(update, ".mp4", video)
    context.user_data["evidence_path"] = path
    context.user_data["evidence_file_id"] = file_id
    await update.message.reply_text("✅ ቪዲዮ ማስረጃ ተቀምጧል!")
    return await _ask_location(update)


async def collect_evidence_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_skip(update.message.text):
        await update.message.reply_text(
            "ፎቶ/ቪዲዮ ይላኩ ወይም 👇 ዝለል ይጫኑ",
            reply_markup=_keyboard([SKIP]),
        )
        return EVIDENCE
    context.user_data["evidence_path"] = ""
    context.user_data["evidence_file_id"] = ""
    return await _ask_location(update)


async def _ask_location(update: Update) -> int:
    await update.message.reply_text(
        "📍 አማራጭ: የህንጻ/ዶርም ቁጥር ይጻፉ\n"
        "(ለምሳሌ: Dorm 4 / Block B)\n"
        "ከሌለዎት 👇 ዝለል ይጫኑ",
        reply_markup=_keyboard([SKIP]),
    )
    return LOCATION


async def collect_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    context.user_data["location"] = "" if _is_skip(text) else text
    await update.message.reply_text(
        "━━━━━━━━━━━━━━━\n"
        "📌 ደረጃ 4 ከ 4\n"
        "ማንነትዎ እንዴት ይያዝ?\n"
        "ሳይፈሩ ቅሬታ ማቅረብ ይችላሉ 👇\n"
        "━━━━━━━━━━━━━━━",
        reply_markup=_keyboard([IDENTITY_NAMED, IDENTITY_ANON]),
    )
    return IDENTITY


async def collect_identity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice == IDENTITY_ANON:
        context.user_data["is_anonymous"] = True
        return await _finish(update, context)
    if choice != IDENTITY_NAMED:
        await update.message.reply_text(
            "❗ አንዱን ይምረጡ 👇",
            reply_markup=_keyboard([IDENTITY_NAMED, IDENTITY_ANON]),
        )
        return IDENTITY
    context.user_data["is_anonymous"] = False
    await update.message.reply_text(
        "ሙሉ ስምዎን ይጻፉ:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def collect_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = (update.message.text or "").strip()
    await update.message.reply_text("📞 ስልክ ቁጥርዎን ይጻፉ:")
    return PHONE


async def collect_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["phone"] = (update.message.text or "").strip()
    await update.message.reply_text(
        "🎓 የትምህርት ክፍልዎን ይምረጡ 👇",
        reply_markup=_keyboard(DEPARTMENTS, columns=2),
    )
    return DEPARTMENT


async def collect_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["department"] = (update.message.text or "").strip()
    return await _finish(update, context)


async def _finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    data = context.user_data
    try:
        saved = save_complaint(
            telegram_id=str(user.id if user else ""),
            telegram_username=(user.username or "") if user else "",
            campus=data.get("campus", ""),
            category=data.get("category", ""),
            description=data.get("description", ""),
            location=data.get("location", ""),
            evidence_path=data.get("evidence_path", ""),
            evidence_file_id=data.get("evidence_file_id", ""),
            is_anonymous=bool(data.get("is_anonymous")),
            full_name=data.get("full_name", ""),
            phone=data.get("phone", ""),
            department=data.get("department", ""),
        )
    except Exception:
        logger.exception("Failed to save complaint")
        await update.message.reply_text(
            "⚠️ ይቅርታ፣ ቅሬታው አልተቀመጠም።\n"
            "ቆይተው /start ብለው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    ticket = saved["id"]
    context.user_data.clear()
    await update.message.reply_text(
        "🎉 ቅሬታዎ ተመዝግቧል!\n\n"
        f"🎫 Ticket: {ticket}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 ይህን ቁጥር ያስቀምጡ!\n"
        "ሁኔታ ለማወቅ ይጻፉ:\n"
        f"/status {ticket}\n"
        "━━━━━━━━━━━━━━━\n\n"
        "የተማሪዎች ህብረት ምላሽ ይሰጣል ✊",
        reply_markup=_keyboard([NEW_COMPLAINT]),
    )
    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check status: /status #BDU-2026-112"""
    args = context.args or []
    ticket_id = " ".join(args).strip()
    if not ticket_id:
        await update.message.reply_text(
            "📊 ቅሬታዎ ሁኔታ ለማወቅ ይጻፉ:\n\n"
            "/status #BDU-2026-112\n\n"
            "(ቲኬት ቁጥርዎን ተክቶ)"
        )
        return
    try:
        df = read_complaints()
        match = df[df["id"].astype(str).str.strip() == ticket_id.strip()]
    except Exception:
        logger.exception("Error reading complaints for status check")
        await update.message.reply_text("⚠️ ዳታ ማንበብ አልተቻለም። ቆይተው ይሞክሩ።")
        return
    if match.empty:
        await update.message.reply_text(
            f"❌ Ticket '{ticket_id}' አልተገኘም።\n\n"
            "ቁጥሩን አረጋግጡ።\n"
            "ለምሳሌ: /status #BDU-2026-112"
        )
        return
    r = match.iloc[0]
    sv = str(r["status"])
    sl = STATUS_AM.get(sv, sv)
    pct = {"Open": 5, "In Progress": 50, "Resolved": 100}.get(sv, 5)
    bar = "🟩" * (pct // 10) + "⬜" * (10 - pct // 10)
    resolution = (
        str(r.get("resolution_note", ""))
        if "resolution_note" in r.index else ""
    )
    msg = (
        f"━━━━━━━━━━━━━━━\n"
        f"🎫 {r['id']}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 {r['campus']}\n"
        f"🗂 {r['category']}\n"
        f"📝 {str(r['description'])[:80]}"
        f"{'…' if len(str(r['description'])) > 80 else ''}\n"
        f"📅 {r['created_at']}\n\n"
        f"ሁኔታ: {sl}\n"
        f"ሂደት: {bar} {pct}%"
    )
    if resolution.strip():
        msg += f"\n\n💬 ከህብረት:\n{resolution}"
    else:
        msg += "\n\n⏳ ህብረቱ ገና ምላሽ አልሰጠም።"
    await update.message.reply_text(msg, reply_markup=_keyboard([NEW_COMPLAINT]))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ ተሰርዟል።\n"
        "ቅሬታ ሲኖርዎት /start ወይም /new ይጫኑ 👋",
        reply_markup=_keyboard([NEW_COMPLAINT]),
    )
    return ConversationHandler.END


async def always_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Catch-all — any unexpected message restarts the flow cleanly."""
    text = (update.message.text or "").strip()
    if text in CAMPUSES:
        return await collect_campus(update, context)
    return await start(update, context)


async def post_init(application: Application) -> None:
    """Remove Web App menu button — fixes Play Store 'Not available' error."""
    try:
        await application.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        logger.info("Menu button reset to default chat mode")
    except Exception as e:
        logger.warning("Could not reset menu button: %s", e)


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
            CommandHandler("start", start),
            CommandHandler("new", start),
            CommandHandler("complaint", start),
            CommandHandler("help", start),
            MessageHandler(filters.Regex(f"^{NEW_COMPLAINT}$"), start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, always_open),
        ],
        states={
            CAMPUS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_campus)],
            CATEGORY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_category)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_description)],
            EVIDENCE: [
                MessageHandler(filters.PHOTO, collect_evidence_photo),
                MessageHandler(filters.VIDEO, collect_evidence_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_evidence_text),
            ],
            LOCATION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_location)],
            IDENTITY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_identity)],
            NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_name)],
            PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_phone)],
            DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_department)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("new", start),
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    return app


# ── Keep-alive ping (used when running as standalone process) ─────────────────

def _start_keepalive_ping() -> None:
    """Ping the Streamlit app URL every 4 minutes to keep the dashboard warm."""
    url = _app_url_from_anywhere()
    if not url:
        logger.info("STREAMLIT_APP_URL not set — skip keep-alive ping")
        return

    def _ping_loop() -> None:
        time.sleep(60)  # wait for bot to fully start first
        logger.info("Keep-alive pinger started for %s", url)
        while True:
            time.sleep(240)  # 4 minutes
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "BDU-BotKeepAlive/1.0"}
                )
                urllib.request.urlopen(req, timeout=15)
                logger.debug("Keep-alive ping OK")
            except Exception as exc:
                logger.debug("Keep-alive ping failed (non-fatal): %s", exc)

    threading.Thread(target=_ping_loop, daemon=True, name="keepalive").start()


# ── Standalone entry-point (Procfile: worker: python bot.py) ──────────────────

def main() -> None:
    """Run the bot as a standalone polling process — never returns normally."""
    os.chdir(Path(__file__).resolve().parent)
    token = _token()

    _start_keepalive_ping()

    restart_count = 0
    while True:                             # INFINITE RESTART LOOP
        try:
            application = build_application(token)
            logger.info(
                "Bot starting (restart #%d): %s", restart_count, BOT_URL
            )
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,  # discard backlog on startup
                stop_signals=None,
            )
            logger.info("Bot stopped cleanly.")
            break                           # only a clean exit breaks the loop
        except Exception as exc:
            restart_count += 1
            wait = min(120, 5 * restart_count)   # back-off: 5s, 10s, … max 120s
            logger.error(
                "Bot crashed (#%d), restarting in %ds: %s",
                restart_count, wait, exc,
            )
            time.sleep(wait)


# ── Background-thread entry (called by app.py on Streamlit Cloud) ─────────────

def start_bot_background() -> str:
    """Start the bot in a background thread (fallback when Procfile worker
    is not available, e.g. free-tier Streamlit Community Cloud).
    Returns 'started', 'running', or 'missing_token'.
    """
    global _bot_thread, _bot_status, _bot_error, _restart_count

    if _bot_thread is not None and _bot_thread.is_alive():
        return _bot_status

    token = token_from_anywhere()
    if not token:
        _bot_status = "missing_token"
        _bot_error = "TELEGRAM_BOT_TOKEN is not set in Streamlit Secrets."
        return _bot_status

    os.chdir(Path(__file__).resolve().parent)
    _bot_status = "starting"
    _bot_error = ""

    def _run() -> None:
        global _bot_status, _bot_error, _restart_count
        while True:
            try:
                application = build_application(token)
                _bot_status = "running"
                logger.info(
                    "Bot polling (bg thread restart #%d): %s",
                    _restart_count, BOT_URL,
                )
                application.run_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    stop_signals=None,
                )
                _bot_status = "stopped"
                break
            except Exception as exc:
                _restart_count += 1
                _bot_error = str(exc)
                wait = min(120, 5 * _restart_count)
                _bot_status = f"restarting #{_restart_count}"
                logger.error(
                    "Bot crashed (bg thread #%d), retry in %ds: %s",
                    _restart_count, wait, exc,
                )
                time.sleep(wait)

    _bot_thread = threading.Thread(target=_run, name="telegram-bot", daemon=True)
    _bot_thread.start()
    return "started"


# ── Script entry ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _running_under_streamlit():
        # Someone accidentally ran `streamlit run bot.py` instead of app.py
        import streamlit as st
        st.set_page_config(page_title="Union Student Bot", page_icon="🎓")
        start_bot_background()
        st.error("❌ Wrong entry point!")
        st.write(
            "Streamlit Cloud → App settings → **Main file path** → `app.py` → Redeploy."
        )
        s, e = bot_status()
        st.info(f"Bot status: {s}")
        if e:
            st.warning(e)
        st.link_button("Open Telegram bot", BOT_URL)
        st.stop()
    # Normal case: `python bot.py` from Procfile worker
    main()
