import os
import random
import threading
from datetime import datetime

import requests
from flask import Flask, jsonify

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
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

API_URL = "https://v3.football.api-sports.io"

web_app = Flask(__name__)

# Simple demo user data
users = {}


# =========================================================
# API-FOOTBALL REQUEST
# =========================================================

def football_api(endpoint, params=None):
    if not API_FOOTBALL_KEY:
        return None

    try:
        headers = {
            "x-apisports-key": API_FOOTBALL_KEY
        }

        response = requests.get(
            f"{API_URL}/{endpoint}",
            headers=headers,
            params=params or {},
            timeout=15
        )

        if response.status_code != 200:
            print(
                "API ERROR:",
                response.status_code,
                response.text
            )
            return None

        return response.json()

    except Exception as error:
        print("API REQUEST ERROR:", error)
        return None


# =========================================================
# DATE
# =========================================================

def today_date():
    return datetime.now().strftime("%Y-%m-%d")


# =========================================================
# USER
# =========================================================

def get_user(user_id, first_name="User"):

    if user_id not in users:
        users[user_id] = {
            "name": first_name,
            "balance": 0.00,
            "bets": [],
        }

    return users[user_id]


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                callback_data="best_bet"
            ),
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                callback_data="football"
            ),
        ],

        [
            InlineKeyboardButton(
                "📊 PREDICTION",
                callback_data="prediction"
            ),
            InlineKeyboardButton(
                "📋 BET TABLE",
                callback_data="bet_table"
            ),
        ],

        [
            InlineKeyboardButton(
                "💰 BALANCE",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "👤 PROFILE",
                callback_data="profile"
            ),
        ],

        [
            InlineKeyboardButton(
                "📜 HISTORY",
                callback_data="history"
            ),
            InlineKeyboardButton(
                "🏆 WINNERS",
                callback_data="winners"
            ),
        ],

        [
            InlineKeyboardButton(
                "ℹ️ HOW TO PLAY",
                callback_data="how_to_play"
            ),
            InlineKeyboardButton(
                "📞 SUPPORT",
                callback_data="support"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# FOOTBALL MENU
# =========================================================

def football_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "📅 TODAY'S MATCHES",
                callback_data="football_matches"
            )
        ],

        [
            InlineKeyboardButton(
                "🎯 BEST PREDICTIONS",
                callback_data="prediction"
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
                "⬅️ BACK",
                callback_data="back_main"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# BACK BUTTON
# =========================================================

def back_button():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="back_main"
            )
        ]
    ])


# =========================================================
# FLASK HOME
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
        <title>BEST BET</title>
    </head>

    <body style="
        background:#10182f;
        color:white;
        font-family:Arial;
        text-align:center;
        padding:40px;
    ">

        <h1>🎯 BEST BET</h1>

        <p>⚽ Football Prediction System</p>

        <p>🤖 Telegram Bot is running.</p>

        <p>🟢 Server Online</p>

    </body>
    </html>
    """


# =========================================================
# HEALTH
# =========================================================

@web_app.route("/health")
def health():

    return "OK"


# =========================================================
# MATCHES API FOR HTML
# =========================================================

@web_app.route("/api/matches")
def api_matches():

    data = football_api(
        "fixtures",
        {
            "date": today_date()
        }
    )

    if not data:

        return jsonify({
            "success": False,
            "matches": []
        })

    matches = []

    for item in data.get("response", []):

        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        matches.append({

            "id": fixture.get("id"),

            "time": (
                fixture.get("date", "")
                .replace("T", " ")
                [:16]
            ),

            "home": (
                teams.get("home", {})
                .get("name", "Home")
            ),

            "away": (
                teams.get("away", {})
                .get("name", "Away")
            ),

            "league": league.get(
                "name",
                "Football"
            ),

            "country": league.get(
                "country",
                ""
            ),

            "odds": {
                "1": "-",
                "X": "-",
                "2": "-"
            },

            "markets": {
                "Over 2.5": "-",
                "BTTS": "-"
            },

            "label": "⚽ MATCH"
        })

    return jsonify({
        "success": True,
        "matches": matches
    })


# =========================================================
# BEST BET API
# =========================================================

@web_app.route("/api/best-bet")
def api_best_bet():

    data = football_api(
        "fixtures",
        {
            "date": today_date()
        }
    )

    if not data or not data.get("response"):

        return jsonify({
            "success": False,
            "match": None
        })

    item = data["response"][0]

    fixture = item.get("fixture", {})
    teams = item.get("teams", {})
    league = item.get("league", {})

    # NOTE:
    # This is a demo confidence calculation.
    # It is NOT a guarantee of a winning bet.

    confidence = random.randint(60, 82)

    match = {

        "id": fixture.get("id"),

        "home": (
            teams.get("home", {})
            .get("name", "Home")
        ),

        "away": (
            teams.get("away", {})
            .get("name", "Away")
        ),

        "league": league.get(
            "name",
            "Football"
        ),

        "time": (
            fixture.get("date", "")
            .replace("T", " ")
            [:16]
        ),

        "confidence": confidence
    }

    return jsonify({
        "success": True,
        "match": match
    })


# =========================================================
# WEB SERVER
# =========================================================

def run_web():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    web_app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    get_user(
        user.id,
        user.first_name
    )

    text = (
        f"👋 Baga nagaan dhuftan, "
        f"{user.first_name}!\n\n"

        "🎯 *BEST BET*\n\n"

        "⚽ Football matches\n"
        "📊 Prediction\n"
        "📋 Bet Table\n"
        "💰 Balance\n"
        "👤 Profile\n\n"

        "Menu armaan gadii keessaa "
        "filannoo kee godhi."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# =========================================================
# FOOTBALL MATCHES TEXT
# =========================================================

def get_matches_text():

    data = football_api(
        "fixtures",
        {
            "date": today_date()
        }
    )

    if not data:

        return (
            "❌ *FOOTBALL API*\n\n"
            "API data argachuu hin dandeenye.\n\n"
            "API key kee Render Environment "
            "Variables keessatti ilaali."
        )

    response = data.get(
        "response",
        []
    )

    if not response:

        return (
            "📅 *TODAY'S MATCHES*\n\n"
            "Har'a match hin argamne."
        )

    lines = [
        "📅 *TODAY'S MATCHES*\n"
    ]

    for item in response[:10]:

        teams = item.get(
            "teams",
            {}
        )

        league = item.get(
            "league",
            {}
        )

        home = (
            teams.get("home", {})
            .get("name", "Home")
        )

        away = (
            teams.get("away", {})
            .get("name", "Away")
        )

        league_name = league.get(
            "name",
            "Football"
        )

        lines.append(
            f"⚽ *{home}* vs *{away}*\n"
            f"🏆 {league_name}\n"
        )

    return "\n".join(lines)


# =========================================================
# PREDICTION
# =========================================================

def prediction_text():

    data = football_api(
        "fixtures",
        {
            "date": today_date()
        }
    )

    if not data or not data.get("response"):

        return (
            "📊 *PREDICTION*\n\n"
            "Prediction data hin argamne."
        )

    item = data["response"][0]

    teams = item.get(
        "teams",
        {}
    )

    league = item.get(
        "league",
        {}
    )

    home = (
        teams.get("home", {})
        .get("name", "Home")
    )

    away = (
        teams.get("away", {})
        .get("name", "Away")
    )

    confidence = random.randint(
        60,
        82
    )

    choices = [
        "1X",
        "X2",
        "OVER 1.5",
        "UNDER 3.5",
        "BTTS"
    ]

    prediction = random.choice(
        choices
    )

    return (
        "📊 *PREDICTION TABLE*\n\n"

        f"⚽ *{home}*\n"
        f"      VS\n"
        f"⚽ *{away}*\n\n"

        f"🏆 {league.get('name', 'Football')}\n\n"

        "━━━━━━━━━━━━━━\n"

        f"🎯 *Prediction:* `{prediction}`\n"
        f"📈 *Confidence:* `{confidence}%`\n"

        "━━━━━━━━━━━━━━\n\n"

        "⚠️ Prediction is analysis only.\n"
        "Winning is not guaranteed."
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

    user = query.from_user

    account = get_user(
        user.id,
        user.first_name
    )

    data = query.data


    # =====================================================
    # MAIN
    # =====================================================

    if data == "back_main":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BEST BET
    # =====================================================

    elif data == "best_bet":

        await query.edit_message_text(
            prediction_text(),
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # PREDICTION
    # =====================================================

    elif data == "prediction":

        await query.edit_message_text(
            prediction_text(),
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL
    # =====================================================

    elif data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Football service keessaa "
            "filannoo kee godhi.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # MATCHES
    # =====================================================

    elif data == "football_matches":

        text = get_matches_text()

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # LIVE
    # =====================================================

    elif data == "football_live":

        data_api = football_api(
            "fixtures",
            {
                "live": "all"
            }
        )

        if not data_api:

            text = (
                "🔴 *LIVE*\n\n"
                "Live data argachuu hin dandeenye."
            )

        else:

            live = data_api.get(
                "response",
                []
            )

            if not live:

                text = (
                    "🔴 *LIVE*\n\n"
                    "Amma live match hin jiru."
                )

            else:

                lines = [
                    "🔴 *LIVE MATCHES*\n"
                ]

                for item in live[:10]:

                    teams = item.get(
                        "teams",
                        {}
                    )

                    home = (
                        teams.get("home", {})
                        .get("name", "Home")
                    )

                    away = (
                        teams.get("away", {})
                        .get("name", "Away")
                    )

                    score = item.get(
                        "goals",
                        {}
                    )

                    home_score = score.get(
                        "home",
                        0
                    )

                    away_score = score.get(
                        "away",
                        0
                    )

                    lines.append(
                        f"🔴 {home} "
                        f"*{home_score} - {away_score}* "
                        f"{away}"
                    )

                text = "\n".join(
                    lines
                )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # LEAGUES
    # =====================================================

    elif data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "⚽ Premier League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga\n"
            "⚽ Champions League\n"
            "⚽ Ligue 1",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # STANDINGS
    # =====================================================

    elif data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "League filattee booda "
            "standing isaa fidna.\n\n"
            "⚽ Premier League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BET TABLE
    # =====================================================

    elif data == "bet_table":

        await query.edit_message_text(
            "📋 *BET TABLE*\n\n"

            "┌──────────────┐\n"
            "│ 🎯 BEST BET  │\n"
            "├──────────────┤\n"
            "│ 1X           │\n"
            "│ OVER 1.5     │\n"
            "│ UNDER 3.5    │\n"
            "│ BTTS         │\n"
            "└──────────────┘\n\n"

            "⚠️ Kun prediction table qofa.\n"
            "Bu'aan mirkanaa'aa miti.",

            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BALANCE
    # =====================================================

    elif data == "balance":

        balance = account["balance"]

        await query.edit_message_text(
            "💰 *MY BALANCE*\n\n"

            f"👤 {account['name']}\n\n"

            f"💵 Balance: *{balance:.2f} ETB*\n\n"

            "Deposit/withdraw system "
            "amma ijaaramaa jira.",

            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # PROFILE
    # =====================================================

    elif data == "profile":

        await query.edit_message_text(
            "👤 *MY PROFILE*\n\n"

            f"🧑 Name: *{account['name']}*\n"
            f"🆔 Telegram ID: `{user.id}`\n"
            f"💰 Balance: *{account['balance']:.2f} ETB*\n"
            f"📜 Bets: *{len(account['bets'])}*\n\n"

            "🎯 BEST BET account",

            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HISTORY
    # =====================================================

    elif data == "history":

        if not account["bets"]:

            text = (
                "📜 *MY HISTORY*\n\n"
                "Bet history amma duwwaa dha."
            )

        else:

            text = (
                "📜 *MY HISTORY*\n\n"
                "Bets kee:\n\n"
                + "\n".join(
                    account["bets"][-10:]
                )
            )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WINNERS
    # =====================================================

    elif data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "🥇 Demo Winner\n"
            "🥈 Demo Winner\n"
            "🥉 Demo Winner\n\n"
            "Real winner system database "
            "waliin booda ijaarra.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"

            "1️⃣ ⚽ Football seeni.\n"
            "2️⃣ 📅 Match filadhu.\n"
            "3️⃣ 📊 Prediction ilaali.\n"
            "4️⃣ 📋 Bet Table ilaali.\n"
            "5️⃣ 🎯 Best Bet ilaali.\n\n"

            "⚠️ Prediction jechuun "
            "mirkaneessa win miti.",

            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # SUPPORT
    # =====================================================

    elif data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Admin support qunnami.",
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

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

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

    print(
        "🌐 BEST BET web server started..."
    )

    print(
        "🤖 BEST BET Telegram bot started..."
    )

    app.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
