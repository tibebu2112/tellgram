"""Telegram bot that collects student union complaints."""

from __future__ import annotations

import logging
import os
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


def _token() -> str:
    secrets = load_secrets()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or secrets.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN in the environment or in .streamlit/secrets.toml"
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


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    application = Application.builder().token(_token()).build()
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
    logger.info("Bot is polling: %s", BOT_URL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
