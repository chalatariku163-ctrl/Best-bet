import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")


# =========================================================
# START MENU
# =========================================================

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🎯 BEST BET", callback_data="best_bet"),
        ],
        [
            InlineKeyboardButton("💰 DEPOSIT", callback_data="deposit"),
            InlineKeyboardButton("💳 BALANCE", callback_data="balance"),
        ],
        [
            InlineKeyboardButton("🎲 PLACE BET", callback_data="place_bet"),
            InlineKeyboardButton("💸 WITHDRAW", callback_data="withdraw"),
        ],
        [
            InlineKeyboardButton("📜 MY HISTORY", callback_data="history"),
            InlineKeyboardButton("🏆 WINNERS", callback_data="winners"),
        ],
        [
            InlineKeyboardButton("ℹ️ HOW TO PLAY", callback_data="how_to_play"),
        ],
        [
            InlineKeyboardButton("📞 SUPPORT", callback_data="support"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    text = (
        f"👋 Baga nagaan dhuftan, {user.first_name}!\n\n"
        "🎯 **BEST BET**\n\n"
        "Taphachuu fi odeeffannoo account kee ilaaluuf "
        "menu armaan gadii keessaa filadhu."
    )

    await update.message.reply_text(
        text,
