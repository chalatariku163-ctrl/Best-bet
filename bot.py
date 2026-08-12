import os
import threading
import sqlite3
from datetime import datetime

import requests
from flask import Flask, jsonify, request

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

DATABASE = "bestbet.db"

web_app = Flask(__name__)


# =========================================================
# DATABASE
# =========================================================

def get_db():

    connection = sqlite3.connect(
        DATABASE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            match_name TEXT,
            market TEXT,
            selection TEXT,
            odd REAL,
            stake REAL,
            potential_return REAL,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )
    """)

    db.commit()
    db.close()


def save_user(
    user_id,
    username,
    first_name
):

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT id
        FROM users
        WHERE id = ?
    """, (user_id,))

    existing = cursor.fetchone()

    if existing:

        cursor.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE id = ?
        """, (
            username,
            first_name,
            user_id
        ))

    else:

        cursor.execute("""
            INSERT INTO users (
                id,
                username,
                first_name,
                balance,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            username,
            first_name,
            0,
            datetime.utcnow().isoformat()
        ))

    db.commit()
    db.close()


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

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <title>BEST BET</title>

    </head>

    <body style="
        background:#10182f;
        color:white;
        font-family:Arial;
        text-align:center;
        padding:40px;
    ">

        <h1>⚽ BEST BET</h1>

        <p>Football betting dashboard is running.</p>

        <p>🤖 Telegram Bot: ONLINE</p>

        <p>⚽ Football API: CONNECTED</p>

        <p>💰 Balance system: READY</p>

        <p>🧾 Bet Slip: READY</p>

    </body>

    </html>
    """


@web_app.route("/health")
def health():

    return "OK"


# =========================================================
# FOOTBALL API HELPER
# =========================================================

def football_request(
    endpoint,
    params=None
):

    if not API_FOOTBALL_KEY:

        return None

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY
    }

    try:

        response = requests.get(
            f"{API_BASE_URL}/{endpoint}",
            headers=headers,
            params=params or {},
            timeout=20
        )

        if response.status_code != 200:

            print(
                "Football API error:",
                response.status_code
            )

            return None

        return response.json()

    except Exception as error:

        print(
            "Football API request error:",
            error
        )

        return None


# =========================================================
# API - TODAY MATCHES
# =========================================================

@web_app.route("/api/matches")
def api_matches():

    today = datetime.utcnow().strftime(
        "%Y-%m-%d"
    )

    data = football_request(
        "fixtures",
        {
            "date": today
        }
    )

    if not data:

        return jsonify({
            "success": False,
            "matches": [],
            "error": "Football API unavailable"
        }), 500

    matches = []

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

        fixture_date = fixture.get(
            "date",
            ""
        )

        time_text = "--:--"

        if "T" in fixture_date:

            time_text = (
                fixture_date
                .split("T")[1][:5]
            )

        matches.append({

            "id": fixture.get(
                "id"
            ),

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

            "time": time_text,

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
                "Under 2.5": "-",
                "BTTS": "-"
            }
        })

    return jsonify({
        "success": True,
        "matches": matches
    })


# =========================================================
# API - LIVE MATCHES
# =========================================================

@web_app.route("/api/live")
def api_live():

    data = football_request(
        "fixtures",
        {
            "live": "all"
        }
    )

    if not data:

        return jsonify({
            "success": False,
            "matches": []
        }), 500

    matches = []

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

        matches.append({

            "id": fixture.get(
                "id"
            ),

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

            "home_score": goals.get(
                "home",
                0
            ),

            "away_score": goals.get(
                "away",
                0
            ),

            "elapsed": status.get(
                "elapsed",
                0
            )
        })

    return jsonify({
        "success": True,
        "matches": matches
    })


# =========================================================
# PROFILE API
# =========================================================

@web_app.route("/api/profile/<int:user_id>")
def profile(user_id):

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,))

    user = cursor.fetchone()

    if not user:

        db.close()

        return jsonify({
            "success": False,
            "error": "User not found"
        }), 404

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM bets
        WHERE user_id = ?
    """, (user_id,))

    total_bets = cursor.fetchone()["total"]

    cursor.execute("""
        SELECT COUNT(*) AS won
        FROM bets
        WHERE user_id = ?
        AND status = 'WON'
    """, (user_id,))

    won = cursor.fetchone()["won"]

    cursor.execute("""
        SELECT COUNT(*) AS lost
        FROM bets
        WHERE user_id = ?
        AND status = 'LOST'
    """, (user_id,))

    lost = cursor.fetchone()["lost"]

    db.close()

    return jsonify({

        "success": True,

        "profile": {

            "id": user["id"],

            "username": user["username"],

            "first_name": user["first_name"],

            "balance": user["balance"],

            "total_bets": total_bets,

            "won": won,

            "lost": lost

        }
    })


# =========================================================
# BALANCE API
# =========================================================

@web_app.route("/api/balance/<int:user_id>")
def balance(user_id):

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT balance
        FROM users
        WHERE id = ?
    """, (user_id,))

    user = cursor.fetchone()

    db.close()

    if not user:

        return jsonify({
            "success": False,
            "balance": 0
        }), 404

    return jsonify({
        "success": True,
        "balance": user["balance"]
    })


# =========================================================
# BET HISTORY API
# =========================================================

@web_app.route("/api/history/<int:user_id>")
def history(user_id):

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT *
        FROM bets
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 100
    """, (user_id,))

    rows = cursor.fetchall()

    db.close()

    bets = []

    for row in rows:

        bets.append({

            "id": row["id"],

            "match_name": row["match_name"],

            "market": row["market"],

            "selection": row["selection"],

            "odd": row["odd"],

            "stake": row["stake"],

            "potential_return": (
                row["potential_return"]
            ),

            "status": row["status"],

            "created_at": row["created_at"]
        })

    return jsonify({
        "success": True,
        "bets": bets
    })


# =========================================================
# BET PLACEMENT API
# =========================================================

@web_app.route(
    "/api/place-bet",
    methods=["POST"]
)
def place_bet():

    data = request.get_json(
        silent=True
    ) or {}

    user_id = data.get(
        "user_id"
    )

    selections = data.get(
        "selections",
        []
    )

    stake = float(
        data.get(
            "stake",
            0
        )
    )

    if not user_id:

        return jsonify({
            "success": False,
            "error": "user_id required"
        }), 400

    if not selections:

        return jsonify({
            "success": False,
            "error": "No selections"
        }), 400

    if stake <= 0:

        return jsonify({
            "success": False,
            "error": "Invalid stake"
        }), 400

    total_odds = 1.0

    for selection in selections:

        odd = float(
            selection.get(
                "odd",
                0
            )
        )

        if odd <= 0:

            return jsonify({
                "success": False,
                "error": "Invalid odd"
            }), 400

        total_odds *= odd

    potential_return = (
        stake * total_odds
    )

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT balance
        FROM users
        WHERE id = ?
    """, (user_id,))

    user = cursor.fetchone()

    if not user:

        db.close()

        return jsonify({
            "success": False,
            "error": "User not found"
        }), 404

    current_balance = float(
        user["balance"]
    )

    if current_balance < stake:

        db.close()

        return jsonify({
            "success": False,
            "error": "Insufficient balance"
        }), 400

    new_balance = (
        current_balance - stake
    )

    cursor.execute("""
        UPDATE users
        SET balance = ?
        WHERE id = ?
    """, (
        new_balance,
        user_id
    ))

    match_name = " / ".join(
        str(
            item.get(
                "match",
                "Football"
            )
        )
        for item in selections
    )

    selection_names = " / ".join(
        str(
            item.get(
                "selection",
                ""
            )
        )
        for item in selections
    )

    markets = " / ".join(
        str(
            item.get(
                "market",
                ""
            )
        )
        for item in selections
    )

    cursor.execute("""
        INSERT INTO bets (
            user_id,
            match_name,
            market,
            selection,
            odd,
            stake,
            potential_return,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        match_name,
        markets,
        selection_names,
        total_odds,
        stake,
        potential_return,
        "PENDING",
        datetime.utcnow().isoformat()
    ))

    bet_id = cursor.lastrowid

    db.commit()

    db.close()

    return jsonify({

        "success": True,

        "bet_id": bet_id,

        "total_odds": round(
            total_odds,
            2
        ),

        "stake": stake,

        "potential_return": round(
            potential_return,
            2
        ),

        "balance": round(
            new_balance,
            2
        )
    })


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
                "👤 PROFILE",
                callback_data="profile"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 BALANCE",
                callback_data="balance"
            ),

            InlineKeyboardButton(
                "🧾 MY BETS",
                callback_data="history"
            )
        ],

        [
            InlineKeyboardButton(
                "⚡ KENO FAST",
                callback_data="keno_fast"
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
# KENO MENU
# =========================================================

def keno_menu():

    keyboard = []

    for start in range(
        1,
        81,
        10
    ):

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
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    save_user(
        user.id,
        user.username or "",
        user.first_name or ""
    )

    await update.message.reply_text(

        f"👋 Baga nagaan dhuftan, "
        f"{user.first_name}!\n\n"

        "⚽ *BEST BET*\n\n"

        "Betting table, football matches, "
        "profile fi balance fayyadami.",

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

    user = update.effective_user

    save_user(
        user.id,
        user.username or "",
        user.first_name or ""
    )

    # =====================================================
    # FOOTBALL
    # =====================================================

    if query.data == "football":

        await query.edit_message_text(

            "⚽ *FOOTBALL*\n\n"
            "Taphoota fi betting markets "
            "armaan gadii keessaa filadhu.",

            reply_markup=football_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # MATCHES
    # =====================================================

    elif query.data == "football_matches":

        matches = get_today_matches()

        if not matches:

            await query.edit_message_text(

                "📅 *MATCHES*\n\n"
                "⚠️ Match data hin argamne.\n\n"
                "API key kee fi API connection "
                "ilaali.",

                reply_markup=football_menu(),

                parse_mode="Markdown"
            )

            return

        text = (
            "📅 *TODAY'S MATCHES*\n\n"
        )

        for match in matches[:10]:

            text += (

                f"🏆 {match['league']}\n"

                f"🕐 {match['time']}\n"

                f"⚽ {match['home']}\n"

                f"🆚 {match['away']}\n\n"
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

        live = get_live_matches()

        if not live:

            await query.edit_message_text(

                "🔴 *LIVE*\n\n"
                "Amma live match hin jiru.",

                reply_markup=football_menu(),

                parse_mode="Markdown"
            )

            return

        text = "🔴 *LIVE MATCHES*\n\n"

        for match in live[:10]:

            text += (

                f"🏆 {match['league']}\n"

                f"⚽ {match['home']} "
                f"vs "
                f"{match['away']}\n"

                f"📊 "
                f"{match['home_score']} - "
                f"{match['away_score']}\n"

                f"⏱️ {match['elapsed']}'\n\n"
            )

        await query.edit_message_text(

            text,

            reply_markup=football_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # PROFILE
    # =====================================================

    elif query.data == "profile":

        db = get_db()

        cursor = db.cursor()

        cursor.execute("""
            SELECT *
            FROM users
            WHERE id = ?
        """, (user.id,))

        row = cursor.fetchone()

        db.close()

        if not row:

            await query.edit_message_text(
                "❌ Profile hin argamne.",
                reply_markup=main_menu()
            )

            return

        await query.edit_message_text(

            "👤 *MY PROFILE*\n\n"

            f"👋 Name: *{row['first_name']}*\n"

            f"🆔 ID: `{row['id']}`\n\n"

            f"💰 Balance: "
            f"*{row['balance']:.2f} ETB*\n\n"

            "🎯 BEST BET",

            reply_markup=main_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        db = get_db()

        cursor = db.cursor()

        cursor.execute("""
            SELECT balance
            FROM users
            WHERE id = ?
        """, (user.id,))

        row = cursor.fetchone()

        db.close()

        current_balance = (
            row["balance"]
            if row
            else 0
        )

        await query.edit_message_text(

            "💰 *MY BALANCE*\n\n"

            f"💵 Available:\n"
            f"*{current_balance:.2f} ETB*\n\n"

            "Deposit fi withdrawal system "
            "gara itti aanu keessatti "
            "dabalama.",

            reply_markup=main_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        db = get_db()

        cursor = db.cursor()

        cursor.execute("""
            SELECT *
            FROM bets
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (user.id,))

        bets = cursor.fetchall()

        db.close()

        if not bets:

            text = (
                "📜 *MY BETS*\n\n"
                "Ammaaf bet hin qabdu."
            )

        else:

            text = "📜 *MY BETS*\n\n"

            for bet in bets:

                text += (

                    f"⚽ {bet['match_name']}\n"

                    f"🎯 {bet['selection']}\n"

                    f"📊 Odd: {bet['odd']:.2f}\n"

                    f"💵 Stake: "
                    f"{bet['stake']:.2f} ETB\n"

                    f"📌 {bet['status']}\n\n"
                )

        await query.edit_message_text(

            text,

            reply_markup=main_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # BEST BET
    # =====================================================

    elif query.data == "best_bet":

        await query.edit_message_text(

            "🎯 *BEST BET*\n\n"

            "Prediction hin fayyadamnu.\n"

            "Odds table qofa API irraa "
            "fudhachuuf qophaa'e.",

            reply_markup=main_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # LEAGUES
    # =====================================================

    elif query.data == "football_leagues":

        await query.edit_message_text(

            "🏆 *LEAGUES*\n\n"

            "⚽ Premier League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga\n"
            "⚽ Ligue 1\n"
            "⚽ Champions League",

            reply_markup=football_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # STANDINGS
    # =====================================================

    elif query.data == "football_standings":

        await query.edit_message_text(

            "📊 *STANDINGS*\n\n"
            "Standing API itti aanu keessatti "
            "dabalama.",

            reply_markup=football_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # TEAMS
    # =====================================================

    elif query.data == "football_teams":

        await query.edit_message_text(

            "🔎 *TEAMS*\n\n"
            "Team search itti aanu keessatti "
            "dabalama.",

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
            "filadhu.",

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

            f"🔢 Lakkoofsa: *{number}*\n\n"
            "Demo qofa.",

            reply_markup=keno_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # KENO DRAW
    # =====================================================

    elif query.data == "keno_draw":

        numbers = random.sample(
            range(1, 81),
            10
        )

        numbers.sort()

        result = ", ".join(
            str(number)
            for number in numbers
        )

        await query.edit_message_text(

            "🎲 *RANDOM DRAW*\n\n"
            f"🔢 {result}\n\n"
            "Demo qofa.",

            reply_markup=keno_menu(),

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

    # =====================================================
    # WINNERS
    # =====================================================

    elif query.data == "winners":

        await query.edit_message_text(

            "🏆 *WINNERS*\n\n"
            "Winner history yeroo itti "
            "aanu keessatti dabalama.",

            reply_markup=main_menu(),

            parse_mode="Markdown"
        )

    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif query.data == "how_to_play":

        await query.edit_message_text(

            "ℹ️ *HOW TO PLAY*\n\n"

            "1️⃣ Football bani.\n"
            "2️⃣ Match filadhu.\n"
            "3️⃣ Odd filadhu.\n"
            "4️⃣ Bet Slip ilaali.\n"
            "5️⃣ Stake galchi.\n\n"

            "⚠️ Betting dhugaa keessatti "
            "seera fi umurii barbaachisu "
            "kabaji.",

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


# =========================================================
# MATCH HELPER
# =========================================================

def get_today_matches():

    today = datetime.utcnow().strftime(
        "%Y-%m-%d"
    )

    data = football_request(
        "fixtures",
        {
            "date": today
        }
    )

    if not data:

        return []

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

        fixture_date = fixture.get(
            "date",
            ""
        )

        time_text = "--:--"

        if "T" in fixture_date:

            time_text = (
                fixture_date
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


# =========================================================
# LIVE HELPER
# =========================================================

def get_live_matches():

    data = football_request(
        "fixtures",
        {
            "live": "all"
        }
    )

    if not data:

        return []

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

            "home_score": goals.get(
                "home",
                0
            ),

            "away_score": goals.get(
                "away",
                0
            ),

            "elapsed": status.get(
                "elapsed",
                0
            )
        })

    return result


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
# MAIN
# =========================================================

def main():

    init_db()

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable "
            "hin jiru."
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
        "🌐 BEST BET web server started."
    )

    print(
        "🤖 Telegram bot started."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
