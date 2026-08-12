import os
import random
import threading

from flask import Flask, jsonify, render_template

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


BOT_TOKEN = os.getenv("BOT_TOKEN")

web_app = Flask(__name__)


# =========================================================
# DEMO FOOTBALL DATA
# =========================================================

MATCHES = [
    {
        "league": "Premier League",
        "country": "England",
        "time": "19:30",
        "home": "Arsenal",
        "away": "Chelsea",
        "odds": {
            "1": "1.85",
            "X": "3.60",
            "2": "4.20"
        },
        "markets": {
            "Over 2.5": "1.90",
            "BTTS": "1.75"
        },
        "label": "🎯 BEST BET",
        "confidence": 82
    },
    {
        "league": "Premier League",
        "country": "England",
        "time": "21:00",
        "home": "Liverpool",
        "away": "Newcastle",
        "odds": {
            "1": "1.55",
            "X": "4.10",
            "2": "5.80"
        },
        "markets": {
            "Over 2.5": "1.68",
            "BTTS": "1.82"
        },
        "label": "⭐ VALUE",
        "confidence": 78
    },
    {
        "league": "La Liga",
        "country": "Spain",
        "time": "20:00",
        "home": "Barcelona",
        "away": "Sevilla",
        "odds": {
            "1": "1.42",
            "X": "4.80",
            "2": "7.20"
        },
        "markets": {
            "Over 2.5": "1.62",
            "BTTS": "1.78"
        },
        "label": "🎯 BEST BET",
        "confidence": 85
    }
]


# =========================================================
# FLASK
# =========================================================

@web_app.route("/")
def home():
    return render_template("index.html")


@web_app.route("/health")
def health():
    return "OK"


@web_app.route("/api/matches")
def matches_api():
    return jsonify({
        "success": True,
        "matches": MATCHES
    })


@web_app.route("/api/best-bet")
def best_bet_api():

    best = max(
        MATCHES,
        key=lambda match: match["confidence"]
    )

    return jsonify({
        "success": True,
        "match": best
    })


def run_web():
    port = int(os.environ.get("PORT", 10000))

    web_app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# TELEGRAM MENUS
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
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


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
        ]
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
# CALLBACK
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    # Telegram callback saffisaan acknowledge godhi
    try:
        await query.answer()
    except Exception as error:
        print(f"Callback answer warning: {error}")

    data = query.data

    # -----------------------------------------------------
    # BEST BET
    # -----------------------------------------------------

    if data == "best_bet":

        best = max(
            MATCHES,
            key=lambda match: match["confidence"]
        )

        text = (
            "🎯 *BEST BET*\n\n"
            f"⚽ {best['home']} vs {best['away']}\n"
            f"🏆 {best['league']}\n"
            f"🕐 {best['time']}\n\n"
            f"📊 Confidence: *{best['confidence']}%*\n\n"
            f"1️⃣ {best['odds']['1']}\n"
            f"❌ X {best['odds']['X']}\n"
            f"2️⃣ {best['odds']['2']}\n\n"
            "🧪 Demo data."
        )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # -----------------------------------------------------
    # KENO
    # -----------------------------------------------------

    elif data == "keno_fast":

        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "Lakkoofsa 1 hanga 80 keessaa filadhu.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif data.startswith("keno_number_"):

        number = data.replace(
            "keno_number_",
            ""
        )

        await query.edit_message_text(
            f"🔢 Lakkoofsa filatame: *{number}*\n\n"
            "RANDOM DRAW ykn lakkoofsa biraa filadhu.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif data == "keno_draw":

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
            f"🔢 Result:\n\n*{result_text}*\n\n"
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    # -----------------------------------------------------
    # FOOTBALL
    # -----------------------------------------------------

    elif data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Filannoo kee godhi.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football_matches":

        text = "📅 *TODAY'S MATCHES*\n\n"

        for match in MATCHES:

            text += (
                f"⚽ {match['home']} vs "
                f"{match['away']}\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']}\n\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football_live":

        await query.edit_message_text(
            "🔴 *LIVE*\n\n"
            "Live football API yeroo ammaa "
            "hin walqabsiifamne.\n\n"
            "🧪 Demo mode.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "⚽ Premier League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga\n"
            "⚽ Champions League",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "Standing API booda itti daballa.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football_teams":

        await query.edit_message_text(
            "🔎 *TEAMS*\n\n"
            "Team search API booda itti daballa.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    elif data == "back_main":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # -----------------------------------------------------
    # OTHER
    # -----------------------------------------------------

    elif data == "deposit":

        await query.edit_message_text(
            "💰 *DEPOSIT*\n\n"
            "Deposit system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "balance":

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            "Balance system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "withdraw":

        await query.edit_message_text(
            "💸 *WITHDRAW*\n\n"
            "Withdrawal system hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "history":

        await query.edit_message_text(
            "📜 *MY HISTORY*\n\n"
            "History system booda itti daballa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo winners booda itti daballa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ Football ykn Keno filadhu.\n"
            "2️⃣ Filannoo kee godhi.\n"
            "3️⃣ Result ilaali.\n\n"
            "🧪 Demo qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Admin/support qunnami.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        f"❌ Telegram error: {context.error}"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable hin jiru."
        )

    # Flask
    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    # Telegram
    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    print("🌐 Web server started...")
    print("🤖 BEST BET BOT started...")

    app.run_polling()


if __name__ == "__main__":
    main()
