import os
import random
import threading

from flask import Flask

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

web_app = Flask(__name__)


# =========================================================
# FLASK WEB SERVER
# =========================================================

@web_app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">
        <title>Best Bet</title>
    </head>

    <body style="
        background:#10182f;
        color:white;
        font-family:Arial;
        text-align:center;
        padding:40px;
    ">

        <h1>🎯 BEST BET</h1>

        <p>🤖 Telegram Bot is running.</p>

        <p>⚡ Keno Fast Demo is ready.</p>

    </body>
    </html>
    """


@web_app.route("/health")
def health():
    return "OK"


def run_web():
    port = int(os.environ.get("PORT", 10000))

    web_app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                callback_data="best_bet"
            )
        ],

        [
            InlineKeyboardButton(
                "⚡ KENO FAST",
                callback_data="keno_fast"
            ),
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                callback_data="football"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 DEPOSIT",
                callback_data="deposit"
            ),
            InlineKeyboardButton(
                "💳 BALANCE",
                callback_data="balance"
            )
        ],

        [
            InlineKeyboardButton(
                "💸 WITHDRAW",
                callback_data="withdraw"
            ),
            InlineKeyboardButton(
                "📜 MY HISTORY",
                callback_data="history"
            )
        ],

        [
            InlineKeyboardButton(
                "🏆 WINNERS",
                callback_data="winners"
            ),
            InlineKeyboardButton(
                "ℹ️ HOW TO PLAY",
                callback_data="how_to_play"
            )
        ],

        [
            InlineKeyboardButton(
                "📞 SUPPORT",
                callback_data="support"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# KENO / NUMBER GAME MENU
# =========================================================

def keno_menu():

    keyboard = []

    for start in range(1, 81, 10):

        row = []

        for number in range(start, start + 10):

            row.append(
                InlineKeyboardButton(
                    str(number),
                    callback_data=f"keno_number_{number}"
                )
            )

        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(
            "🎲 RANDOM DRAW",
            callback_data="keno_draw"
        )
    ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ BACK",
            callback_data="back_main"
        )
    ])

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# FOOTBALL MENU
# =========================================================

def football_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "📅 MATCHES",
                callback_data="football_matches"
            )
        ],

        [
            InlineKeyboardButton(
                "🔴 LIVE",
                callback_data="football_live"
            )
        ],

        [
            InlineKeyboardButton(
                "🏆 LEAGUES",
                callback_data="football_leagues"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 STANDINGS",
                callback_data="football_standings"
            )
        ],

        [
            InlineKeyboardButton(
                "🔎 TEAMS",
                callback_data="football_teams"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="back_main"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    text = (
        f"👋 Baga nagaan dhuftan, "
        f"{user.first_name}!\n\n"
        "🎯 *BEST BET*\n\n"
        "Menu armaan gadii keessaa "
        "filannoo kee godhi."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    # =====================================================
    # BEST BET
    # =====================================================

    if query.data == "best_bet":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO FAST
    # =====================================================

    elif query.data == "keno_fast":

        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "Lakkoofsa 1 hanga 80 keessaa "
            "filadhu.\n\n"
            "🧪 Demo number game qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO NUMBER
    # =====================================================

    elif query.data.startswith("keno_number_"):

        number = query.data.replace(
            "keno_number_",
            ""
        )

        await query.edit_message_text(
            f"🔢 Lakkoofsa filatame: *{number}*\n\n"
            "Lakkoofsa biraa filachuu "
            "ykn RANDOM DRAW gochuu dandeessa.\n\n"
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO RANDOM DRAW
    # =====================================================

    elif query.data == "keno_draw":

        result = random.sample(
            range(1, 81),
            10
        )

        result.sort()

        result_text = ", ".join(
            str(number)
            for number in result
        )

        await query.edit_message_text(
            "🎲 *RANDOM DRAW*\n\n"
            f"🔢 Result:\n\n"
            f"*{result_text}*\n\n"
            "🧪 Demo number game qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL
    # =====================================================

    elif query.data == "football":

        await query.edit_message_text(
            " ",
            reply_markup=football_menu()
        )


    # =====================================================
    # FOOTBALL MATCHES
    # =====================================================

    elif query.data == "football_matches":

        await query.edit_message_text(
            "📅 *MATCHES*\n\n"
            "Taphoota football asitti "
            "ilaalla.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL LIVE
    # =====================================================

    elif query.data == "football_live":

        await query.edit_message_text(
            "🔴 *LIVE*\n\n"
            "Live football data asitti "
            "mul'ata.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL LEAGUES
    # =====================================================

    elif query.data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "⚽ Premier League\n"
            "⚽ Champions League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL STANDINGS
    # =====================================================

    elif query.data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "Gabatee sadarkaa league "
            "asitti ilaalla.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL TEAMS
    # =====================================================

    elif query.data == "football_teams":

        await query.edit_message_text(
            "🔎 *TEAMS*\n\n"
            "Gareewwan football asitti "
            "ilaalla.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BACK MAIN
    # =====================================================

    elif query.data == "back_main":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # DEPOSIT
    # =====================================================

    elif query.data == "deposit":

        await query.edit_message_text(
            "💰 *DEPOSIT*\n\n"
            "Deposit system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            "Balance system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WITHDRAW
    # =====================================================

    elif query.data == "withdraw":

        await query.edit_message_text(
            "💸 *WITHDRAW*\n\n"
            "Withdrawal system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        await query.edit_message_text(
            "📜 *MY HISTORY*\n\n"
            "Demo history as keessatti "
            "mul'ata.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WINNERS
    # =====================================================

    elif query.data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo results as keessatti "
            "mul'atu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif query.data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ ⚡ KENO FAST filadhu.\n"
            "2️⃣ Lakkoofsa 1–80 keessaa filadhu.\n"
            "3️⃣ 🎲 RANDOM DRAW cuqaasi.\n"
            "4️⃣ Result ilaali.\n\n"
            "🧪 Demo qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # SUPPORT
    # =====================================================

    elif query.data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Yoo gargaarsa barbaadde, "
            "admin/support qunnami.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable hin jiru."
        )


    # Flask server jalqabi
    threading.Thread(
        target=run_web,
        daemon=True
    ).start()


    # Telegram application
    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )


    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    # Inline buttons
    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )


    print(
        "🌐 Web server started..."
    )

    print(
        "🤖 BEST BET BOT started..."
    )


    # Telegram polling
    app.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
