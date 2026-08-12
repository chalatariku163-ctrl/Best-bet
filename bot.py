import os
import random
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, send_from_directory

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
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

PORT = int(os.getenv("PORT", "10000"))

API_URL = "https://v3.football.api-sports.io"

web_app = Flask(__name__, static_folder=".")


# =========================================================
# SIMPLE USER DATA
# =========================================================

users = {}


def get_user(user_id, first_name="User"):
    if user_id not in users:
        users[user_id] = {
            "name": first_name,
            "balance": 0.0,
            "history": [],
        }

    return users[user_id]


# =========================================================
# FOOTBALL API
# =========================================================

def football_request(endpoint, params=None):

    if not FOOTBALL_API_KEY:
        return None, "FOOTBALL_API_KEY hin jiru."

    headers = {
        "x-apisports-key": FOOTBALL_API_KEY
    }

    try:
        response = requests.get(
            f"{API_URL}/{endpoint}",
            headers=headers,
            params=params or {},
            timeout=20
        )

        if response.status_code != 200:
            return None, f"API HTTP {response.status_code}"

        data = response.json()

        if data.get("errors"):
            return None, str(data["errors"])

        return data, None

    except requests.RequestException as error:
        return None, str(error)


# =========================================================
# GET TODAY MATCHES
# =========================================================

def get_today_matches():

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data, error = football_request(
        "fixtures",
        {
            "date": today,
            "timezone": "Africa/Addis_Ababa"
        }
    )

    if error:
        return [], error

    matches = []

    for item in data.get("response", []):

        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        home = teams.get("home", {})
        away = teams.get("away", {})

        date_string = fixture.get("date", "")

        time_text = "--:--"

        try:
            dt = datetime.fromisoformat(
                date_string.replace("Z", "+00:00")
            )

            time_text = dt.astimezone().strftime("%H:%M")

        except Exception:
            pass

        status = fixture.get("status", {}).get(
            "short",
            ""
        )

        matches.append({
            "id": fixture.get("id"),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
            "home_logo": home.get("logo"),
            "away_logo": away.get("logo"),
            "league": league.get("name", "Unknown"),
            "country": league.get("country", ""),
            "time": time_text,
            "status": status,
        })

    return matches, None


# =========================================================
# GET PREDICTION
# =========================================================

def get_prediction(fixture_id):

    if not fixture_id:
        return None

    data, error = football_request(
        "predictions",
        {
            "fixture": fixture_id
        }
    )

    if error:
        return None

    response = data.get("response", [])

    if not response:
        return None

    prediction_data = response[0]

    prediction = prediction_data.get(
        "predictions",
        {}
    )

    teams = prediction_data.get(
        "teams",
        {}
    )

    winner = prediction.get("winner") or {}

    advice = prediction.get(
        "advice",
        ""
    )

    percent = prediction.get(
        "percent",
        {}
    )

    home_percent = percent.get(
        "home",
        "0%"
    )

    draw_percent = percent.get(
        "draw",
        "0%"
    )

    away_percent = percent.get(
        "away",
        "0%"
    )

    return {
        "advice": advice,
        "winner": winner.get("name"),
        "winner_comment": winner.get(
            "comment"
        ),
        "home_percent": home_percent,
        "draw_percent": draw_percent,
        "away_percent": away_percent,
        "under_over": prediction.get(
            "under_over"
        ),
        "goals_home": prediction.get(
            "goals",
            {}
        ).get("home"),
        "goals_away": prediction.get(
            "goals",
            {}
        ).get("away"),
        "teams": teams,
    }


# =========================================================
# BEST BET
# =========================================================

def calculate_confidence(prediction):

    if not prediction:
        return 0

    values = []

    for key in [
        "home_percent",
        "draw_percent",
        "away_percent"
    ]:

        value = prediction.get(key, "0%")

        try:
            values.append(
                float(
                    str(value).replace("%", "")
                )
            )
        except Exception:
            pass

    if not values:
        return 0

    return int(max(values))


def get_best_bet():

    matches, error = get_today_matches()

    if error:
        return None, error

    if not matches:
        return None, "Har'a match hin argamne."

    # Check several matches until a prediction is found.
    for match in matches[:15]:

        prediction = get_prediction(
            match.get("id")
        )

        if not prediction:
            continue

        confidence = calculate_confidence(
            prediction
        )

        if confidence <= 0:
            continue

        if prediction.get("winner"):

            bet = prediction.get(
                "winner"
            )

        else:

            bet = prediction.get(
                "advice"
            ) or "Analysis"

        result = {
            **match,
            "prediction": prediction,
            "bet": bet,
            "confidence": confidence
        }

        return result, None

    return None, "Prediction har'aaf hin argamne."


# =========================================================
# FORMAT MATCH FOR TELEGRAM
# =========================================================

def match_text(match):

    return (
        f"⚽ *{match['home']} vs "
        f"{match['away']}*\n\n"
        f"🏆 {match['league']}\n"
        f"🌍 {match['country']}\n"
        f"🕐 {match['time']}\n"
        f"📡 Status: {match['status'] or 'NS'}"
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
                "⚽ FOOTBALL",
                callback_data="football"
            ),

            InlineKeyboardButton(
                "⚡ KENO FAST",
                callback_data="keno_fast"
            )
        ],

        [
            InlineKeyboardButton(
                "👤 PROFILE",
                callback_data="profile"
            ),

            InlineKeyboardButton(
                "💳 BALANCE",
                callback_data="balance"
            )
        ],

        [
            InlineKeyboardButton(
                "📜 HISTORY",
                callback_data="history"
            ),

            InlineKeyboardButton(
                "🏆 WINNERS",
                callback_data="winners"
            )
        ],

        [
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
# FOOTBALL MENU
# =========================================================

def football_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "📅 TODAY MATCHES",
                callback_data="football_matches"
            )
        ],

        [
            InlineKeyboardButton(
                "🎯 PREDICTIONS",
                callback_data="football_prediction"
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
                "⬅️ BACK",
                callback_data="back_main"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# KENO MENU
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
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    get_user(
        user.id,
        user.first_name or "User"
    )

    text = (
        f"👋 Baga nagaan dhuftan "
        f"*{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n\n"
        "⚽ Football prediction\n"
        "📅 Today's matches\n"
        "👤 Profile\n"
        "💳 Balance\n\n"
        "👇 Menu keessaa filadhu."
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

    user = query.from_user

    get_user(
        user.id,
        user.first_name or "User"
    )

    # =====================================================
    # BEST BET
    # =====================================================

    if query.data == "best_bet":

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                "🎯 *BEST BET*\n\n"
                f"⚠️ {error}\n\n"
                "API key fi quota kee ilaali.",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )

            return

        prediction = match["prediction"]

        text = (
            "🎯 *BEST BET*\n\n"
            f"⚽ *{match['home']} vs "
            f"{match['away']}*\n\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🔥 *Prediction:* "
            f"{match['bet']}\n"
            f"📊 *Confidence:* "
            f"{match['confidence']}%\n\n"
            f"🏠 Home: "
            f"{prediction['home_percent']}\n"
            f"🤝 Draw: "
            f"{prediction['draw_percent']}\n"
            f"✈️ Away: "
            f"{prediction['away_percent']}\n\n"
            "⚠️ Prediction is API analysis, "
            "not a guaranteed result."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # FOOTBALL
    # =====================================================

    elif query.data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Football menu keessaa filadhu.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # FOOTBALL MATCHES
    # =====================================================

    elif query.data == "football_matches":

        matches, error = get_today_matches()

        if error:

            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        if not matches:

            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\n"
                "Match har'aa hin argamne.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        text = "📅 *TODAY'S MATCHES*\n\n"

        for match in matches[:12]:

            text += (
                f"⚽ *{match['home']}* "
                f"vs "
                f"*{match['away']}*\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']}\n\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # FOOTBALL PREDICTION
    # =====================================================

    elif query.data == "football_prediction":

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                "🎯 *PREDICTION*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        prediction = match["prediction"]

        text = (
            "🎯 *FOOTBALL PREDICTION*\n\n"
            f"⚽ *{match['home']} vs "
            f"{match['away']}*\n\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🔥 Prediction: *{match['bet']}*\n"
            f"📊 Confidence: "
            f"*{match['confidence']}%*\n\n"
            f"🏠 Home: "
            f"{prediction['home_percent']}\n"
            f"🤝 Draw: "
            f"{prediction['draw_percent']}\n"
            f"✈️ Away: "
            f"{prediction['away_percent']}\n\n"
            "⚠️ Kun prediction API irratti "
            "hundaa'e; bu'aan mirkanaa'aa miti."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # LIVE
    # =====================================================

    elif query.data == "football_live":

        data, error = football_request(
            "fixtures",
            {
                "live": "all"
            }
        )

        if error:

            await query.edit_message_text(
                "🔴 *LIVE*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        live = data.get(
            "response",
            []
        )

        if not live:

            await query.edit_message_text(
                "🔴 *LIVE*\n\n"
                "Ammaaf live match hin jiru.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        text = "🔴 *LIVE MATCHES*\n\n"

        for item in live[:10]:

            teams = item.get(
                "teams",
                {}
            )

            goals = item.get(
                "goals",
                {}
            )

            home = teams.get(
                "home",
                {}
            ).get(
                "name",
                "Home"
            )

            away = teams.get(
                "away",
                {}
            ).get(
                "name",
                "Away"
            )

            home_goals = goals.get(
                "home"
            )

            away_goals = goals.get(
                "away"
            )

            elapsed = item.get(
                "fixture",
                {}
            ).get(
                "status",
                {}
            ).get(
                "elapsed",
                ""
            )

            text += (
                f"⚽ *{home}* "
                f"{home_goals or 0} - "
                f"{away_goals or 0} "
                f"*{away}*\n"
                f"⏱️ {elapsed or '-'}'\n\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # LEAGUES
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
    # KENO
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
            "Lakkoofsa biraa filadhu.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "keno_draw":

        result = random.sample(
            range(1, 81),
            10
        )

        result.sort()

        result_text = ", ".join(
            str(x)
            for x in result
        )

        await query.edit_message_text(
            "🎲 *RANDOM DRAW*\n\n"
            f"🔢 *{result_text}*\n\n"
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # PROFILE
    # =====================================================

    elif query.data == "profile":

        profile = get_user(
            user.id,
            user.first_name or "User"
        )

        await query.edit_message_text(
            "👤 *PROFILE*\n\n"
            f"Name: *{profile['name']}*\n"
            f"Telegram ID: `{user.id}`\n"
            f"💳 Balance: "
            f"*{profile['balance']:.2f}*\n"
            f"📜 History: "
            f"*{len(profile['history'])}*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        profile = get_user(
            user.id,
            user.first_name or "User"
        )

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            f"👤 {profile['name']}\n"
            f"💰 Balance: "
            f"*{profile['balance']:.2f}*\n\n"
            "Deposit/payment system "
            "ammaaf hin hidhamne.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        profile = get_user(
            user.id,
            user.first_name or "User"
        )

        if not profile["history"]:

            text = (
                "📜 *MY HISTORY*\n\n"
                "History duwwaa dha."
            )

        else:

            text = "📜 *MY HISTORY*\n\n"

            for item in profile[
                "history"
            ][-10:]:

                text += (
                    f"• {item}\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # WINNERS
    # =====================================================

    elif query.data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo winners table.\n\n"
            "🥇 User — Best Bet\n"
            "🥈 User — Football\n"
            "🥉 User — Keno",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif query.data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ ⚽ Football filadhu.\n"
            "2️⃣ 📅 Today's Matches ilaali.\n"
            "3️⃣ 🎯 Prediction filadhu.\n"
            "4️⃣ Best Bet analysis ilaali.\n\n"
            "⚠️ Prediction bu'aa mirkanaa'aa "
            "miti.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # SUPPORT
    # =====================================================

    elif query.data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Admin/support qunnami.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # BACK
    # =====================================================

    elif query.data == "back_main":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filadhu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


# =========================================================
# WEB API — MATCHES
# =========================================================

@web_app.route("/api/matches")
def api_matches():

    matches, error = get_today_matches()

    if error:

        return jsonify({
            "success": False,
            "error": error,
            "matches": []
        }), 500

    return jsonify({
        "success": True,
        "matches": matches
    })


# =========================================================
# WEB API — BEST BET
# =========================================================

@web_app.route("/api/best-bet")
def api_best_bet():

    match, error = get_best_bet()

    if error:

        return jsonify({
            "success": False,
            "error": error
        }), 500

    return jsonify({
        "success": True,
        "match": {
            "id": match.get("id"),
            "home": match.get("home"),
            "away": match.get("away"),
            "league": match.get("league"),
            "country": match.get("country"),
            "time": match.get("time"),
            "prediction": match.get(
                "prediction"
            ),
            "bet": match.get("bet"),
            "confidence": match.get(
                "confidence"
            )
        }
    })


# =========================================================
# WEB HOME
# =========================================================

@web_app.route("/")
def home():

    index_path = os.path.join(
        os.getcwd(),
        "index.html"
    )

    if os.path.exists(index_path):

        return send_from_directory(
            os.getcwd(),
            "index.html"
        )

    return """
    <html>
    <head>
        <meta name="viewport"
              content="width=device-width,initial-scale=1">
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

        <p>⚽ Football API is running.</p>

        <p>🤖 Telegram Bot is running.</p>

    </body>
    </html>
    """


# =========================================================
# HEALTH
# =========================================================

@web_app.route("/health")
def health():

    return jsonify({
        "status": "OK",
        "bot": bool(BOT_TOKEN),
        "football_api": bool(
            FOOTBALL_API_KEY
        )
    })


# =========================================================
# WEB SERVER
# =========================================================

def run_web():

    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable "
            "hin jiru."
        )

    if not FOOTBALL_API_KEY:

        print(
            "⚠️ FOOTBALL_API_KEY hin jiru. "
            "Telegram bot ni jalqaba, "
            "garuu football API hin hojjetu."
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
        "🌐 BEST BET web server started"
    )

    print(
        "🤖 BEST BET Telegram bot started"
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
