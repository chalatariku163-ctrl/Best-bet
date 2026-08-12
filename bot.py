import os
import random
import threading

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

API_BASE_URL = "https://v3.football.api-sports.io"

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

        <p>🤖 Telegram Bot is running.</p>

        <p>⚽ Football API connected.</p>

        <p>⚡ Keno Fast Demo is ready.</p>

    </body>
    </html>
    """


@web_app.route("/health")
def health():
    return "OK"


# =========================================================
# WEB API - MATCHES
# =========================================================

@web_app.route("/api/matches")
def api_matches():

    if not API_FOOTBALL_KEY:
        return jsonify({
            "success": False,
            "error": "API_FOOTBALL_KEY is not configured.",
            "matches": []
        }), 500

    try:

        headers = {
            "x-apisports-key": API_FOOTBALL_KEY
        }

        response = requests.get(
            f"{API_BASE_URL}/fixtures",
            headers=headers,
            params={
                "date": "2026-08-12"
            },
            timeout=15
        )

        if response.status_code != 200:
            return jsonify({
                "success": False,
                "error": f"API HTTP {response.status_code}",
                "matches": []
            }), response.status_code

        data = response.json()

        matches = []

        for item in data.get("response", []):

            fixture = item.get("fixture", {})
            teams = item.get("teams", {})
            league = item.get("league", {})

            home = teams.get("home", {}).get(
                "name",
                "Home"
            )

            away = teams.get("away", {}).get(
                "name",
                "Away"
            )

            date_time = fixture.get(
                "date",
                ""
            )

            time_text = "--:--"

            if "T" in date_time:
                time_text = date_time.split("T")[1][:5]

            matches.append({
                "id": fixture.get("id"),
                "home": home,
                "away": away,
                "time": time_text,
                "league": league.get(
                    "name",
                    "Other"
                ),
                "country": league.get(
                    "country",
                    ""
                ),
                "label": "⚽ MATCH",
                "odds": {
                    "1": "-",
                    "X": "-",
                    "2": "-"
                },
                "markets": {
                    "Over 2.5": "-",
                    "BTTS": "-"
                }
            })

        return jsonify({
            "success": True,
            "matches": matches
        })

    except Exception as error:

        return jsonify({
            "success": False,
            "error": str(error),
            "matches": []
        }), 500


# =========================================================
# WEB API - BEST BET
# =========================================================

@web_app.route("/api/best-bet")
def api_best_bet():

    if not API_FOOTBALL_KEY:
        return jsonify({
            "success": False,
            "error": "API_FOOTBALL_KEY is not configured."
        }), 500

    try:

        headers = {
            "x-apisports-key": API_FOOTBALL_KEY
        }

        response = requests.get(
            f"{API_BASE_URL}/fixtures",
            headers=headers,
            params={
                "date": "2026-08-12"
            },
            timeout=15
        )

        if response.status_code != 200:
            return jsonify({
                "success": False,
                "error": f"API HTTP {response.status_code}"
            }), response.status_code

        data = response.json()

        fixtures = data.get(
            "response",
            []
        )

        if not fixtures:

            return jsonify({
                "success": False,
                "error": "No matches found."
            })

        item = fixtures[0]

        fixture = item.get(
            "fixture",
            {}
        )

        teams = item.get(
            "teams",
            {}
        )

        league = item.get(
            "league",
            {}
        )

        date_time = fixture.get(
            "date",
            ""
        )

        time_text = "--:--"

        if "T" in date_time:
            time_text = date_time.split("T")[1][:5]

        match = {
            "home": teams.get(
                "home",
                {}
            ).get(
                "name",
                "Home"
            ),

            "away": teams.get(
                "away",
                {}
            ).get(
                "name",
                "Away"
            ),

            "league": league.get(
                "name",
                "Football"
            ),

            "time": time_text,

            "confidence": 60
        }

        return jsonify({
            "success": True,
            "match": match
        })

    except Exception as error:

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# =========================================================
# FLASK RUNNER
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

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# KENO MENU
# =========================================================

def keno_menu():

    keyboard = []

    for start in range(1, 81, 10):

        row = []

        for number in range(
            start,
            start + 10
        ):

            row.append(
                InlineKeyboardButton(
                    str(number),
                    callback_data=(
                        f"keno_number_{number}"
                    )
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

    return InlineKeyboardMarkup(
        keyboard
    )


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

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    first_name = (
        user.first_name
        if user
        else "User"
    )

    text = (
        f"👋 Baga nagaan dhuftan, "
        f"{first_name}!\n\n"
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
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO NUMBER
    # =====================================================

    elif query.data.startswith(
        "keno_number_"
    ):

        number = query.data.replace(
            "keno_number_",
            ""
        )

        await query.edit_message_text(
            f"🔢 Lakkoofsa filatame: "
            f"*{number}*\n\n"
            "Lakkoofsa biraa filachuu "
            "ykn RANDOM DRAW gochuu "
            "dandeessa.\n\n"
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
    # FOOTBALL MAIN
    # =====================================================

    elif query.data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Filannoo armaan gadii "
            "keessaa tokko filadhu:",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL MATCHES
    # =====================================================

    elif query.data == "football_matches":

        await query.edit_message_text(
            "📅 *MATCHES*\n\n"
            "Taphoota har'aa API-Football "
            "irraa fidna.\n\n"
            "⏳ Mee xiqqoo eegii...",
            parse_mode="Markdown"
        )

        matches = get_today_matches()

        if not matches:

            await query.edit_message_text(
                "📅 *MATCHES*\n\n"
                "⚠️ Har'a match argachuu "
                "hin dandeenye.\n\n"
                "API key ykn API connection "
                "keessan ilaali.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        text = "📅 *TODAY'S MATCHES*\n\n"

        for match in matches[:15]:

            text += (
                f"⚽ *{match['league']}*\n"
                f"🏠 {match['home']}\n"
                f"🆚 {match['away']}\n"
                f"🕐 {match['time']}\n\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL LIVE
    # =====================================================

    elif query.data == "football_live":

        await query.edit_message_text(
            "🔴 *LIVE FOOTBALL*\n\n"
            "⏳ Live data barbaadaa jira...",
            parse_mode="Markdown"
        )

        live_matches = get_live_matches()

        if not live_matches:

            await query.edit_message_text(
                "🔴 *LIVE FOOTBALL*\n\n"
                "Amma live match hin jiru "
                "ykn API irraa data hin argamne.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        text = "🔴 *LIVE FOOTBALL*\n\n"

        for match in live_matches[:15]:

            text += (
                f"🏆 {match['league']}\n"
                f"⚽ {match['home']} "
                f"vs "
                f"{match['away']}\n"
                f"📊 {match['score']}\n"
                f"⏱️ {match['elapsed']}'\n\n"
            )

        await query.edit_message_text(
            text,
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
            "⚽ Bundesliga\n"
            "⚽ Ligue 1",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL STANDINGS
    # =====================================================

    elif query.data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "League filadhuuf standings "
            "API irraa itti aansee "
            "dabaluu dandeenya.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL TEAMS
    # =====================================================

    elif query.data == "football_teams":

        await query.edit_message_text(
            "🔎 *TEAMS*\n\n"
            "Team barbaaduuf maqaa "
            "garee sanaa fayyadamna.",
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
# API-FOOTBALL: TODAY MATCHES
# =========================================================

def get_today_matches():

    if not API_FOOTBALL_KEY:
        return []

    try:

        headers = {
            "x-apisports-key": API_FOOTBALL_KEY
        }

        response = requests.get(
            f"{API_BASE_URL}/fixtures",
            headers=headers,
            params={
                "date": "2026-08-12"
            },
            timeout=15
        )

        if response.status_code != 200:
            print(
                "API error:",
                response.status_code,
                response.text
            )

            return []

        data = response.json()

        result = []

        for item in data.get(
            "response",
            []
        ):

            fixture = item.get(
                "fixture",
                {}
            )

            teams = item.get(
                "teams",
                {}
            )

            league = item.get(
                "league",
                {}
            )

            date_time = fixture.get(
                "date",
                ""
            )

            time_text = "--:--"

            if "T" in date_time:

                time_text = (
                    date_time
                    .split("T")[1][:5]
                )

            result.append({

                "home": teams.get(
                    "home",
                    {}
                ).get(
                    "name",
                    "Home"
                ),

                "away": teams.get(
                    "away",
                    {}
                ).get(
                    "name",
                    "Away"
                ),

                "league": league.get(
                    "name",
                    "Football"
                ),

                "time": time_text
            })

        return result

    except Exception as error:

        print(
            "get_today_matches error:",
            error
        )

        return []


# =========================================================
# API-FOOTBALL: LIVE MATCHES
# =========================================================

def get_live_matches():

    if not API_FOOTBALL_KEY:
        return []

    try:

        headers = {
            "x-apisports-key": API_FOOTBALL_KEY
        }

        response = requests.get(
            f"{API_BASE_URL}/fixtures",
            headers=headers,
            params={
                "live": "all"
            },
            timeout=15
        )

        if response.status_code != 200:
            return []

        data = response.json()

        result = []

        for item in data.get(
            "response",
            []
        ):

            fixture = item.get(
                "fixture",
                {}
            )

            teams = item.get(
                "teams",
                {}
            )

            league = item.get(
                "league",
                {}
            )

            goals = item.get(
                "goals",
                {}
            )

            status = fixture.get(
                "status",
                {}
            )

            home_score = goals.get(
                "home"
            )

            away_score = goals.get(
                "away"
            )

            if home_score is None:
                home_score = 0

            if away_score is None:
                away_score = 0

            elapsed = status.get(
                "elapsed"
            )

            if elapsed is None:
                elapsed = 0

            result.append({

                "home": teams.get(
                    "home",
                    {}
                ).get(
                    "name",
                    "Home"
                ),

                "away": teams.get(
                    "away",
                    {}
                ).get(
                    "name",
                    "Away"
                ),

                "league": league.get(
                    "name",
                    "Football"
                ),

                "score": (
                    f"{home_score} - "
                    f"{away_score}"
                ),

                "elapsed": elapsed
            })

        return result

    except Exception as error:

        print(
            "get_live_matches error:",
            error
        )

        return []


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable "
            "hin jiru."
        )

    if not API_FOOTBALL_KEY:

        print(
            "⚠️ API_FOOTBALL_KEY hin jiru."
        )

        print(
            "Football API hin hojjetu hanga "
            "API key environment variable "
            "keessa galchitutti."
        )


    # =====================================================
    # START FLASK
    # =====================================================

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()


    # =====================================================
    # TELEGRAM APPLICATION
    # =====================================================

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )


    # =====================================================
    # /START
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    # =====================================================
    # INLINE BUTTONS
    # =====================================================

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

    print(
        "⚽ Football menu ready..."
    )


    # =====================================================
    # POLLING
    # =====================================================

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
