"""Telegram bot: campus → category → evidence → anonymity."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
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
)
from data_manager import EVIDENCE_DIR, load_secrets, save_complaint

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("unionstudent.bot")

BOT_USERNAME = "unionstudent_complaints_bot"
BOT_URL = "https://t.me/unionstudent_complaints_bot"

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

_bot_thread: threading.Thread | None = None
_bot_status = "stopped"
_bot_error = ""


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

        value = st.secrets.get("TELEGRAM_BOT_TOKEN")
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


def _keyboard(options: list[str], columns: int = 1) -> ReplyKeyboardMarkup:
    rows = [options[i : i + columns] for i in range(0, len(options), columns)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def _is_skip(text: str | None) -> bool:
    if not text:
        return False
    value = text.strip().lower()
    return value in {SKIP.lower(), "skip", "ዝለል", "⏭ ዝለል"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    user = update.effective_user
    greet = user.first_name if user else ""
    await update.message.reply_text(
        f"ሰላም {greet}!\n"
        "የባህር ዳር ዩኒቨርሲቲ የተማሪዎች ህብረት ቅሬታ መቀበያ ነው።\n"
        "ቅሬታ በማንኛውም ሰዓት (24/7) ማስገባት ይችላሉ።\n\n"
        "ደረጃ 1፡ ካምፓስዎን ይምረጡ።\n"
        "/new ሌላ ቅሬታ · /cancel ለማቋረጥ",
        reply_markup=_keyboard(CAMPUSES),
    )
    return CAMPUS


async def collect_campus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice not in CAMPUSES:
        await update.message.reply_text(
            "እባክዎ ከዝርዝሩ ካምፓስ ይምረጡ።",
            reply_markup=_keyboard(CAMPUSES),
        )
        return CAMPUS
    context.user_data["campus"] = choice
    await update.message.reply_text(
        "ደረጃ 2፡ የችግሩን ዘርፍ ይምረጡ።",
        reply_markup=_keyboard(CATEGORIES),
    )
    return CATEGORY


async def collect_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = (update.message.text or "").strip()
    if choice not in CATEGORIES:
        await update.message.reply_text(
            "እባክዎ ከዝርዝሩ ዘርፍ ይምረጡ።",
            reply_markup=_keyboard(CATEGORIES),
        )
        return CATEGORY
    context.user_data["category"] = choice
    await update.message.reply_text(
        "ደረጃ 3፡ የችግሩን አጭር መግለጫ ይጻፉ።",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESCRIPTION


async def collect_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if len(text) < 5:
        await update.message.reply_text("እባክዎ ትንሽ ዝርዝር መግለጫ ይጻፉ።")
        return DESCRIPTION
    context.user_data["description"] = text
    await update.message.reply_text(
        "አማራጭ፡ የፎቶ ወይም የቪዲዮ ማስረጃ ይላኩ። ከሌለዎት ዝለል ይጫኑ።",
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
    return await _ask_location(update)


async def collect_evidence_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    path, file_id = await _store_file(update, ".mp4", video)
    context.user_data["evidence_path"] = path
    context.user_data["evidence_file_id"] = file_id
    return await _ask_location(update)


async def collect_evidence_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_skip(update.message.text):
        await update.message.reply_text(
            "ፎቶ/ቪዲዮ ይላኩ ወይም ዝለል ይጫኑ።",
            reply_markup=_keyboard([SKIP]),
        )
        return EVIDENCE
    context.user_data["evidence_path"] = ""
    context.user_data["evidence_file_id"] = ""
    return await _ask_location(update)


async def _ask_location(update: Update) -> int:
    await update.message.reply_text(
        "አማራጭ፡ የህንጻ/ዶርም ቁጥር ይጻፉ (ለምሳሌ Dorm 4 / Block B)። ከሌለዎት ዝለል ይጫኑ።",
        reply_markup=_keyboard([SKIP]),
    )
    return LOCATION


async def collect_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    context.user_data["location"] = "" if _is_skip(text) else text
    await update.message.reply_text(
        "ደረጃ 4፡ ማንነትዎ እንዴት ይያዝ?\n"
        "ተማሪዎች ሳይፈሩ ቅሬታ ማቅረብ ይችላሉ።",
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
            "እባክዎ አንዱን ይምረጡ።",
            reply_markup=_keyboard([IDENTITY_NAMED, IDENTITY_ANON]),
        )
        return IDENTITY
    context.user_data["is_anonymous"] = False
    await update.message.reply_text("ሙሉ ስምዎን ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return NAME


async def collect_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = (update.message.text or "").strip()
    await update.message.reply_text("ስልክ ቁጥርዎን ይጻፉ።")
    return PHONE


async def collect_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["phone"] = (update.message.text or "").strip()
    await update.message.reply_text(
        "የትምህርት ክፍልዎን ይምረጡ።",
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
            "ይቅርታ፣ ቅሬታው አልተቀመጠም። እባክዎ ቆይተው እንደገና ይሞክሩ።",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    ticket = saved["id"]
    context.user_data.clear()
    await update.message.reply_text(
        f"ቅሬታዎ በቁጥር {ticket} ተመዝግቧል! የተማሪዎች ህብረት follow-up ያደርግበታል።\n\n"
        "ሌላ ቅሬታ ካለዎት አሁኑኑ ያስገቡ።",
        reply_markup=_keyboard([NEW_COMPLAINT]),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "ተሰርዟል። ቅሬታ ሲኖርዎት በማንኛውም ጊዜ /start ወይም /new ይጫኑ።",
        reply_markup=_keyboard([NEW_COMPLAINT]),
    )
    return ConversationHandler.END


async def always_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Any message outside a flow starts a new complaint so students can report 24/7."""
    text = (update.message.text or "").strip()
    if text in CAMPUSES:
        return await collect_campus(update, context)
    return await start(update, context)


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("new", start),
            CommandHandler("complaint", start),
            CommandHandler("help", start),
            MessageHandler(filters.Regex(f"^{NEW_COMPLAINT}$"), start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, always_open),
        ],
        states={
            CAMPUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_campus)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_category)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_description)],
            EVIDENCE: [
                MessageHandler(filters.PHOTO, collect_evidence_photo),
                MessageHandler(filters.VIDEO, collect_evidence_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_evidence_text),
            ],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_location)],
            IDENTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_identity)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_phone)],
            DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_department)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("new", start),
        ],
        allow_reentry=False,
    )
    application.add_handler(conversation)
    application.add_handler(CommandHandler("cancel", cancel))
    return application


def start_bot_background() -> str:
    global _bot_thread, _bot_status, _bot_error
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
        global _bot_status, _bot_error
        try:
            application = build_application(token)
            _bot_status = "running"
            logger.info("Bot is polling: %s", BOT_URL)
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                stop_signals=None,
            )
            _bot_status = "stopped"
        except Exception as exc:
            logger.exception("Telegram bot crashed")
            _bot_error = str(exc)
            _bot_status = "error"

    _bot_thread = threading.Thread(target=_run, name="telegram-bot", daemon=True)
    _bot_thread.start()
    return "started"


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    application = build_application(_token())
    logger.info("Bot is polling: %s", BOT_URL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    if _running_under_streamlit():
        import streamlit as st

        st.set_page_config(page_title="Union Student Bot", page_icon="🎓")
        start_bot_background()
        st.error("Main file path must be app.py, not bot.py.")
        st.write("In Streamlit Cloud: App settings → Main file path → `app.py` → Redeploy.")
        status, error = bot_status()
        st.info(f"Bot status: {status}")
        if error:
            st.warning(error)
        st.link_button("Open Telegram bot", BOT_URL)
        st.stop()
    main()
