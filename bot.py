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
# DEMO USER DATA
# =========================================================

users = {}


def get_user(user_id, first_name="User"):

    if user_id not in users:

        users[user_id] = {
            "name": first_name,
            "balance": 0.0,
            "history": [],
            "betslip": []
        }

    return users[user_id]


# =========================================================
# API REQUEST
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
            timeout=25
        )

        if response.status_code != 200:

            return None, (
                f"API HTTP {response.status_code}"
            )

        data = response.json()

        if data.get("errors"):

            return None, str(
                data.get("errors")
            )

        return data, None

    except requests.RequestException as error:

        return None, str(error)


# =========================================================
# TIME
# =========================================================

def format_match_time(date_string):

    if not date_string:

        return "--:--"

    try:

        dt = datetime.fromisoformat(
            date_string.replace(
                "Z",
                "+00:00"
            )
        )

        return dt.astimezone().strftime(
            "%H:%M"
        )

    except Exception:

        return "--:--"


# =========================================================
# GET TODAY MATCHES
# =========================================================

def get_today_matches():

    today = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")

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

        home = teams.get(
            "home",
            {}
        )

        away = teams.get(
            "away",
            {}
        )

        status = fixture.get(
            "status",
            {}
        ).get(
            "short",
            ""
        )

        match = {

            "id": fixture.get("id"),

            "home": home.get(
                "name",
                "Home"
            ),

            "away": away.get(
                "name",
                "Away"
            ),

            "home_logo": home.get(
                "logo"
            ),

            "away_logo": away.get(
                "logo"
            ),

            "league": league.get(
                "name",
                "Unknown"
            ),

            "country": league.get(
                "country",
                ""
            ),

            "time": format_match_time(
                fixture.get("date")
            ),

            "status": status,

            "odds": {
                "1": "-",
                "X": "-",
                "2": "-"
            },

            "markets": {
                "Over 2.5": "-",
                "BTTS": "-"
            }

        }

        matches.append(match)

    return matches, None


# =========================================================
# ODDS PARSER
# =========================================================

def parse_odds(data):

    result = {

        "1": "-",
        "X": "-",
        "2": "-",

        "Over 2.5": "-",
        "Under 2.5": "-",

        "BTTS": "-",
        "No BTTS": "-"

    }

    response = data.get(
        "response",
        []
    )

    if not response:

        return result

    bookmakers = response[0].get(
        "bookmakers",
        []
    )

    if not bookmakers:

        return result

    # Use first available bookmaker.
    bookmaker = bookmakers[0]

    bets = bookmaker.get(
        "bets",
        []
    )

    for bet in bets:

        bet_name = str(
            bet.get("name", "")
        ).lower()

        values = bet.get(
            "values",
            []
        )

        # -------------------------------------------------
        # MATCH WINNER
        # -------------------------------------------------

        if (
            "match winner"
            in bet_name
            or bet.get("id") == 1
        ):

            for value in values:

                label = str(
                    value.get(
                        "value",
                        ""
                    )
                ).lower()

                odd = value.get(
                    "odd",
                    "-"
                )

                if label in [
                    "home",
                    "1"
                ]:

                    result["1"] = odd

                elif label in [
                    "draw",
                    "x"
                ]:

                    result["X"] = odd

                elif label in [
                    "away",
                    "2"
                ]:

                    result["2"] = odd

        # -------------------------------------------------
        # OVER / UNDER
        # -------------------------------------------------

        if (
            "over/under"
            in bet_name
            or bet.get("id") == 5
        ):

            for value in values:

                label = str(
                    value.get(
                        "value",
                        ""
                    )
                )

                odd = value.get(
                    "odd",
                    "-"
                )

                if (
                    "over 2.5"
                    in label.lower()
                ):

                    result[
                        "Over 2.5"
                    ] = odd

                elif (
                    "under 2.5"
                    in label.lower()
                ):

                    result[
                        "Under 2.5"
                    ] = odd

        # -------------------------------------------------
        # BOTH TEAMS TO SCORE
        # -------------------------------------------------

        if (
            "both teams"
            in bet_name
            or bet.get("id") == 8
        ):

            for value in values:

                label = str(
                    value.get(
                        "value",
                        ""
                    )
                ).lower()

                odd = value.get(
                    "odd",
                    "-"
                )

                if label == "yes":

                    result[
                        "BTTS"
                    ] = odd

                elif label == "no":

                    result[
                        "No BTTS"
                    ] = odd

    return result


# =========================================================
# GET MATCH ODDS
# =========================================================

def get_match_odds(
    fixture_id
):

    if not fixture_id:

        return {
            "1": "-",
            "X": "-",
            "2": "-",
            "Over 2.5": "-",
            "Under 2.5": "-",
            "BTTS": "-",
            "No BTTS": "-"
        }

    data, error = football_request(
        "odds",
        {
            "fixture": fixture_id
        }
    )

    if error:

        return {
            "1": "-",
            "X": "-",
            "2": "-",
            "Over 2.5": "-",
            "Under 2.5": "-",
            "BTTS": "-",
            "No BTTS": "-"
        }

    return parse_odds(data)


# =========================================================
# ADD ODDS TO MATCHES
# =========================================================

def enrich_matches_with_odds(
    matches,
    limit=20
):

    for match in matches[:limit]:

        fixture_id = match.get(
            "id"
        )

        odds = get_match_odds(
            fixture_id
        )

        match["odds"] = {
            "1": odds.get("1", "-"),
            "X": odds.get("X", "-"),
            "2": odds.get("2", "-")
        }

        match["markets"] = {
            "Over 2.5": odds.get(
                "Over 2.5",
                "-"
            ),
            "BTTS": odds.get(
                "BTTS",
                "-"
            )
        }

        match["extra_markets"] = {
            "Under 2.5": odds.get(
                "Under 2.5",
                "-"
            ),
            "No BTTS": odds.get(
                "No BTTS",
                "-"
            )
        }

    return matches


# =========================================================
# GET PREDICTION
# =========================================================

def get_prediction(
    fixture_id
):

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

    response = data.get(
        "response",
        []
    )

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

    winner = prediction.get(
        "winner"
    ) or {}

    percent = prediction.get(
        "percent"
    ) or {}

    goals = prediction.get(
        "goals"
    ) or {}

    return {

        "advice": prediction.get(
            "advice",
            ""
        ),

        "winner": winner.get(
            "name"
        ),

        "winner_comment": winner.get(
            "comment"
        ),

        "home_percent": percent.get(
            "home",
            "0%"
        ),

        "draw_percent": percent.get(
            "draw",
            "0%"
        ),

        "away_percent": percent.get(
            "away",
            "0%"
        ),

        "under_over": prediction.get(
            "under_over"
        ),

        "goals_home": goals.get(
            "home"
        ),

        "goals_away": goals.get(
            "away"
        ),

        "teams": teams
    }


# =========================================================
# CONFIDENCE
# =========================================================

def calculate_confidence(
    prediction
):

    if not prediction:

        return 0

    values = []

    for key in [
        "home_percent",
        "draw_percent",
        "away_percent"
    ]:

        value = prediction.get(
            key,
            "0%"
        )

        try:

            values.append(
                float(
                    str(
                        value
                    ).replace(
                        "%",
                        ""
                    )
                )
            )

        except Exception:

            pass

    if not values:

        return 0

    return int(
        max(values)
    )


# =========================================================
# BEST BET
# =========================================================

def get_best_bet():

    matches, error = get_today_matches()

    if error:

        return None, error

    if not matches:

        return None, (
            "Har'a match hin argamne."
        )

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

        odds = get_match_odds(
            match.get("id")
        )

        match["odds"] = {
            "1": odds.get("1", "-"),
            "X": odds.get("X", "-"),
            "2": odds.get("2", "-")
        }

        match["markets"] = {
            "Over 2.5": odds.get(
                "Over 2.5",
                "-"
            ),
            "BTTS": odds.get(
                "BTTS",
                "-"
            )
        }

        if prediction.get(
            "winner"
        ):

            bet = prediction[
                "winner"
            ]

        else:

            bet = (
                prediction.get(
                    "advice"
                )
                or "Analysis"
            )

        return {
            **match,
            "prediction": prediction,
            "bet": bet,
            "confidence": confidence
        }, None

    return None, (
        "Prediction har'aaf "
        "hin argamne."
    )


# =========================================================
# TELEGRAM MAIN MENU
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
                "🎟️ BET SLIP",
                callback_data="betslip"
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
        ]
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
                "🎟️ BET SLIP",
                callback_data="betslip"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="back_main"
            )
        ]
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
# BET SLIP KEYBOARD
# =========================================================

def betslip_keyboard():

    keyboard = [

        [
            InlineKeyboardButton(
                "🗑️ CLEAR",
                callback_data="clear_betslip"
            ),

            InlineKeyboardButton(
                "💰 PLACE DEMO BET",
                callback_data="place_bet"
            )
        ],

        [
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                callback_data="football"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ HOME",
                callback_data="back_main"
            )
        ]
    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# ADD BET TO SLIP
# =========================================================

def add_to_betslip(
    user_id,
    fixture_id,
    home,
    away,
    market,
    selection,
    odd
):

    user = get_user(
        user_id
    )

    # Same fixture + market -> replace.
    user["betslip"] = [
        item
        for item in user["betslip"]
        if not (
            item["fixture_id"]
            == fixture_id
            and item["market"]
            == market
        )
    ]

    user["betslip"].append({

        "fixture_id": fixture_id,

        "home": home,

        "away": away,

        "market": market,

        "selection": selection,

        "odd": float(odd)

    })


# =========================================================
# BETSLIP TEXT
# =========================================================

def get_betslip_text(
    user_id
):

    user = get_user(
        user_id
    )

    slips = user["betslip"]

    if not slips:

        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Football keessaa "
            "odds tokko cuqaasi."
        )

    total_odds = 1.0

    text = (
        "🎟️ *BET SLIP*\n\n"
    )

    for index, item in enumerate(
        slips,
        start=1
    ):

        odd = float(
            item["odd"]
        )

        total_odds *= odd

        text += (
            f"*{index}.* "
            f"{item['home']} "
            f"vs "
            f"{item['away']}\n"
            f"🎯 {item['market']}: "
            f"*{item['selection']}*\n"
            f"📊 Odd: *{odd:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* "
        f"{total_odds:.2f}\n\n"
        "💡 Kun demo bet slip dha."
    )

    return text


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
        "⚽ Football odds\n"
        "📊 Predictions\n"
        "🎟️ Bet Slip\n"
        "📅 Today's matches\n"
        "🔴 Live football\n"
        "👤 Profile\n\n"
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

    data = query.data

    # =====================================================
    # BEST BET
    # =====================================================

    if data == "best_bet":

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                "🎯 *BEST BET*\n\n"
                f"⚠️ {error}\n\n"
                "API key/quota kee ilaali.",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )

            return

        prediction = match[
            "prediction"
        ]

        text = (
            "🎯 *BEST BET*\n\n"
            f"⚽ *{match['home']} "
            f"vs {match['away']}*\n\n"
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
            f"💰 1: "
            f"{match['odds']['1']}\n"
            f"💰 X: "
            f"{match['odds']['X']}\n"
            f"💰 2: "
            f"{match['odds']['2']}\n\n"
            "⚠️ Prediction bu'aa "
            "mirkanaa'aa miti."
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    "🏠 1",
                    callback_data=(
                        f"bet|{match['id']}|1"
                    )
                ),

                InlineKeyboardButton(
                    "🤝 X",
                    callback_data=(
                        f"bet|{match['id']}|X"
                    )
                ),

                InlineKeyboardButton(
                    "✈️ 2",
                    callback_data=(
                        f"bet|{match['id']}|2"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip"
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ FOOTBALL",
                    callback_data="football"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown"
        )

    # =====================================================
    # FOOTBALL
    # =====================================================

    elif data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Football menu keessaa filadhu.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # TODAY MATCHES
    # =====================================================

    elif data == "football_matches":

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

        # Add odds to first 12.
        matches = enrich_matches_with_odds(
            matches,
            limit=12
        )

        text = (
            "📅 *TODAY'S MATCHES*\n\n"
        )

        keyboard = []

        for match in matches[:10]:

            text += (
                f"⚽ *{match['home']}* "
                f"vs "
                f"*{match['away']}*\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']}\n"
                f"1️⃣ {match['odds']['1']}  "
                f"❌ {match['odds']['X']}  "
                f"2️⃣ {match['odds']['2']}\n\n"
            )

            keyboard.append([
                InlineKeyboardButton(
                    (
                        f"⚽ {match['home']}"
                        f" vs "
                        f"{match['away']}"
                    )[:60],
                    callback_data=(
                        f"match|{match['id']}"
                    )
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                callback_data="betslip"
            )
        ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="football"
            )
        ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown"
        )

    # =====================================================
    # MATCH DETAILS
    # =====================================================

    elif data.startswith(
        "match|"
    ):

        fixture_id = data.split(
            "|"
        )[1]

        matches, error = get_today_matches()

        if error:

            await query.edit_message_text(
                f"❌ {error}",
                reply_markup=football_menu()
            )

            return

        selected = None

        for match in matches:

            if str(
                match["id"]
            ) == str(
                fixture_id
            ):

                selected = match
                break

        if not selected:

            await query.edit_message_text(
                "❌ Match hin argamne.",
                reply_markup=football_menu()
            )

            return

        odds = get_match_odds(
            fixture_id
        )

        prediction = get_prediction(
            fixture_id
        )

        if prediction:

            confidence = calculate_confidence(
                prediction
            )

            prediction_text = (
                f"🎯 Prediction: "
                f"*{prediction.get('winner') or prediction.get('advice') or 'Analysis'}*\n"
                f"📊 Confidence: "
                f"*{confidence}%*\n\n"
                f"🏠 Home: "
                f"{prediction.get('home_percent', '0%')}\n"
                f"🤝 Draw: "
                f"{prediction.get('draw_percent', '0%')}\n"
                f"✈️ Away: "
                f"{prediction.get('away_percent', '0%')}\n"
            )

        else:

            prediction_text = (
                "🎯 Prediction: "
                "Unavailable\n"
            )

        text = (
            "⚽ *MATCH DETAILS*\n\n"
            f"*{selected['home']} vs "
            f"{selected['away']}*\n\n"
            f"🏆 {selected['league']}\n"
            f"🌍 {selected['country']}\n"
            f"🕐 {selected['time']}\n\n"
            "💰 *1X2 ODDS*\n"
            f"🏠 1: *{odds['1']}*\n"
            f"🤝 X: *{odds['X']}*\n"
            f"✈️ 2: *{odds['2']}*\n\n"
            "📊 *MARKETS*\n"
            f"⬆️ Over 2.5: "
            f"*{odds['Over 2.5']}*\n"
            f"⬇️ Under 2.5: "
            f"*{odds['Under 2.5']}*\n"
            f"⚽ BTTS Yes: "
            f"*{odds['BTTS']}*\n"
            f"🚫 BTTS No: "
            f"*{odds['No BTTS']}*\n\n"
            f"{prediction_text}\n"
            "👇 Odds filadhu."
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    f"1️⃣ {odds['1']}",
                    callback_data=(
                        f"bet|{fixture_id}|1"
                    )
                ),

                InlineKeyboardButton(
                    f"❌ {odds['X']}",
                    callback_data=(
                        f"bet|{fixture_id}|X"
                    )
                ),

                InlineKeyboardButton(
                    f"2️⃣ {odds['2']}",
                    callback_data=(
                        f"bet|{fixture_id}|2"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    f"⬆️ O2.5 {odds['Over 2.5']}",
                    callback_data=(
                        f"bet|{fixture_id}|O25"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    f"⬇️ U2.5 {odds['Under 2.5']}",
                    callback_data=(
                        f"bet|{fixture_id}|U25"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    f"⚽ BTTS {odds['BTTS']}",
                    callback_data=(
                        f"bet|{fixture_id}|BTTS"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip"
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ MATCHES",
                    callback_data="football_matches"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown"
        )

    # =====================================================
    # ADD BET
    # =====================================================

    elif data.startswith(
        "bet|"
    ):

        parts = data.split("|")

        if len(parts) != 3:

            return

        fixture_id = parts[1]
        selection_code = parts[2]

        matches, error = get_today_matches()

        if error:

            await query.answer(
                "API error.",
                show_alert=True
            )

            return

        selected = None

        for match in matches:

            if str(
                match["id"]
            ) == str(
                fixture_id
            ):

                selected = match
                break

        if not selected:

            await query.answer(
                "Match hin argamne.",
                show_alert=True
            )

            return

        odds = get_match_odds(
            fixture_id
        )

        mapping = {

            "1": (
                "1X2",
                selected["home"],
                odds["1"]
            ),

            "X": (
                "1X2",
                "Draw",
                odds["X"]
            ),

            "2": (
                "1X2",
                selected["away"],
                odds["2"]
            ),

            "O25": (
                "Over/Under 2.5",
                "Over 2.5",
                odds["Over 2.5"]
            ),

            "U25": (
                "Over/Under 2.5",
                "Under 2.5",
                odds["Under 2.5"]
            ),

            "BTTS": (
                "BTTS",
                "Yes",
                odds["BTTS"]
            )

        }

        if selection_code not in mapping:

            await query.answer(
                "Selection hin beekamne.",
                show_alert=True
            )

            return

        market, selection, odd = mapping[
            selection_code
        ]

        try:

            odd_value = float(
                str(odd).replace(
                    ",",
                    "."
                )
            )

        except Exception:

            await query.answer(
                "Odd yeroo ammaa hin jiru.",
                show_alert=True
            )

            return

        if odd_value <= 1:

            await query.answer(
                "Odd sirrii hin jiru.",
                show_alert=True
            )

            return

        add_to_betslip(
            user.id,
            fixture_id,
            selected["home"],
            selected["away"],
            market,
            selection,
            odd_value
        )

        await query.answer(
            f"✅ {selection} bet slip keessa gale.",
            show_alert=True
        )

        await query.edit_message_text(
            get_betslip_text(
                user.id
            ),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )

    # =====================================================
    # BET SLIP
    # =====================================================

    elif data == "betslip":

        await query.edit_message_text(
            get_betslip_text(
                user.id
            ),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )

    # =====================================================
    # CLEAR BETSLIP
    # =====================================================

    elif data == "clear_betslip":

        user_data = get_user(
            user.id
        )

        user_data["betslip"] = []

        await query.edit_message_text(
            "🎟️ *BET SLIP*\n\n"
            "🗑️ Bet slip qulqullaa'e.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # PLACE DEMO BET
    # =====================================================

    elif data == "place_bet":

        user_data = get_user(
            user.id
        )

        slips = user_data[
            "betslip"
        ]

        if not slips:

            await query.answer(
                "Bet slip duwwaa dha.",
                show_alert=True
            )

            return

        stake = 10.0

        if user_data[
            "balance"
        ] < stake:

            await query.edit_message_text(
                "💰 *BALANCE XIQQA*\n\n"
                f"Balance kee: "
                f"*{user_data['balance']:.2f}*\n\n"
                "Demo bet gochuuf "
                f"{stake:.2f} barbaachisa.\n\n"
                "⚠️ Deposit dhugaa hin "
                "hojjenne.",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )

            return

        total_odds = 1.0

        for item in slips:

            total_odds *= float(
                item["odd"]
            )

        potential_win = (
            stake *
            total_odds
        )

        user_data[
            "balance"
        ] -= stake

        now = datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

        user_data[
            "history"
        ].append({

            "time": now,

            "stake": stake,

            "odds": total_odds,

            "potential": potential_win,

            "status": "OPEN",

            "selections": [
                (
                    item["home"]
                    + " vs "
                    + item["away"]
                    + " - "
                    + item["selection"]
                )
                for item in slips
            ]

        })

        user_data[
            "betslip"
        ] = []

        await query.edit_message_text(
            "✅ *DEMO BET PLACED*\n\n"
            f"💰 Stake: "
            f"*{stake:.2f}*\n"
            f"📈 Total Odds: "
            f"*{total_odds:.2f}*\n"
            f"🏆 Potential Win: "
            f"*{potential_win:.2f}*\n\n"
            f"💳 Balance: "
            f"*{user_data['balance']:.2f}*\n\n"
            "⚠️ Kun demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # FOOTBALL PREDICTION
    # =====================================================

    elif data == "football_prediction":

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                "🎯 *PREDICTION*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )

            return

        prediction = match[
            "prediction"
        ]

        text = (
            "🎯 *FOOTBALL PREDICTION*\n\n"
            f"⚽ *{match['home']} "
            f"vs {match['away']}*\n\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🔥 Prediction: "
            f"*{match['bet']}*\n"
            f"📊 Confidence: "
            f"*{match['confidence']}%*\n\n"
            f"🏠 Home: "
            f"{prediction['home_percent']}\n"
            f"🤝 Draw: "
            f"{prediction['draw_percent']}\n"
            f"✈️ Away: "
            f"{prediction['away_percent']}\n\n"
            "⚠️ Kun API analysis dha."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # LIVE
    # =====================================================

    elif data == "football_live":

        live_data, error = football_request(
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

        live = live_data.get(
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

        text = (
            "🔴 *LIVE MATCHES*\n\n"
        )

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

    elif data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "🏴 Premier League\n"
            "🇪🇸 La Liga\n"
            "🇮🇹 Serie A\n"
            "🇩🇪 Bundesliga\n"
            "🇫🇷 Ligue 1\n"
            "🏆 Champions League",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    # =====================================================
    # KENO
    # =====================================================

    elif data == "keno_fast":

        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "Lakkoofsa 1 hanga 80 "
            "keessaa filadhu.\n\n"
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif data.startswith(
        "keno_number_"
    ):

        number = data.replace(
            "keno_number_",
            ""
        )

        await query.edit_message_text(
            f"🔢 Lakkoofsa filatame: "
            f"*{number}*\n\n"
            "Lakkoofsa biraa filadhu.",
            reply_markup=keno_menu(),
            parse_mode="Markdown
