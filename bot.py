"""Telegram bot that collects student union complaints."""

from __future__ import annotations

import logging
import os
import threading
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

from data_manager import load_secrets, save_complaint

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("unionstudent.bot")

# Public Telegram handle (safe to share). Token stays in secrets.toml, never in git.
BOT_USERNAME = "unionstudent_complaints_bot"
BOT_URL = "https://t.me/unionstudent_complaints_bot"

NAME, STUDENT_ID, DEPARTMENT, CATEGORY, URGENCY, COMPLAINT, CONFIRM = range(7)

DEPARTMENTS = [
    "Computer Science",
    "Information Systems",
    "Electrical Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "Business / Economics",
    "Medicine / Health",
    "Law",
    "Education",
    "Other",
]

CATEGORIES = [
    "Academic",
    "Cafeteria",
    "Dormitory",
    "Library",
    "Finance / Registrar",
    "Campus Security",
    "Transport",
    "Other",
]

URGENCY_LEVELS = ["Low", "Medium", "High", "Emergency"]


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


def _keyboard(options: list[str], columns: int = 2) -> ReplyKeyboardMarkup:
    rows = [options[i : i + columns] for i in range(0, len(options), columns)]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    greet = user.first_name if user else "student"
    await update.message.reply_text(
        f"ሰላም {greet}!\n"
        "Welcome to the Student Union complaint bot.\n\n"
        "የተማሪዎች ህብረት ቅሬታ መቀበያ ነው። "
        "Please send your full name.\n\n"
        "Use /cancel to stop.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def collect_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = update.message.text.strip()
    await update.message.reply_text("Enter your student ID (e.g. UGR/1234/15).")
    return STUDENT_ID


async def collect_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["student_id"] = update.message.text.strip()
    await update.message.reply_text(
        "Select your department / የትምህርት ክፍልዎን ይምረጡ።",
        reply_markup=_keyboard(DEPARTMENTS),
    )
    return DEPARTMENT


async def collect_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["department"] = update.message.text.strip()
    await update.message.reply_text(
        "What is the complaint about? / ቅሬታው ስለ ምንድን ነው?",
        reply_markup=_keyboard(CATEGORIES),
    )
    return CATEGORY


async def collect_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["category"] = update.message.text.strip()
    await update.message.reply_text(
        "How urgent is this? / አስቸኳይነቱ ምን ያህል ነው?",
        reply_markup=_keyboard(URGENCY_LEVELS, columns=2),
    )
    return URGENCY


async def collect_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["urgency"] = update.message.text.strip()
    await update.message.reply_text(
        "Write the complaint in detail.\n"
        "ቅሬታዎን በዝርዝር ይጻፉ።",
        reply_markup=ReplyKeyboardRemove(),
    )
    return COMPLAINT


async def collect_complaint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["complaint"] = update.message.text.strip()
    data = context.user_data
    summary = (
        "Please confirm this complaint:\n\n"
        f"Name: {data['full_name']}\n"
        f"Student ID: {data['student_id']}\n"
        f"Department: {data['department']}\n"
        f"Category: {data['category']}\n"
        f"Urgency: {data['urgency']}\n"
        f"Complaint: {data['complaint']}\n\n"
        "Send Yes to submit or No to cancel."
    )
    await update.message.reply_text(
        summary,
        reply_markup=_keyboard(["Yes", "No"], columns=2),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer not in {"yes", "y", "አዎ"}:
        context.user_data.clear()
        await update.message.reply_text(
            "Cancelled. Send /start to file another complaint.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    user = update.effective_user
    try:
        saved = save_complaint(
            telegram_id=str(user.id if user else ""),
            telegram_username=(user.username or "") if user else "",
            full_name=context.user_data["full_name"],
            student_id=context.user_data["student_id"],
            department=context.user_data["department"],
            category=context.user_data["category"],
            urgency=context.user_data["urgency"],
            complaint=context.user_data["complaint"],
        )
    except Exception:
        logger.exception("Failed to save complaint")
        await update.message.reply_text(
            "Sorry, the complaint could not be saved. Please try again later.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "Thank you. Your complaint was received.\n"
        f"Ticket: {saved['id']}\n"
        "The Student Union dashboard will show it in real time.\n\n"
        "ቅሬታዎ ተቀብሏል። /start በመጫን ሌላ ማስገባት ይችላሉ።",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Send /start whenever you want to report a complaint.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_name)],
            STUDENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_student_id)],
            DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_department)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_category)],
            URGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_urgency)],
            COMPLAINT: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_complaint)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conversation)
    application.add_handler(CommandHandler("cancel", cancel))
    return application


def start_bot_background() -> str:
    """Start polling in a daemon thread so Streamlit Cloud can also show the dashboard."""
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
            _bot_status = "starting"
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
        st.write("ዋናው ፋይል `app.py` መሆን አለበት።")
        status, error = bot_status()
        st.info(f"Bot status: {status}")
        if error:
            st.warning(error)
        st.link_button("Open Telegram bot", BOT_URL)
        st.stop()
    main()
