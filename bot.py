import os
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
# SIMPLE MEMORY DATABASE
# =========================================================

users = {}


def get_user(user_id, first_name="User"):
    if user_id not in users:
        users[user_id] = {
            "name": first_name,
            "balance": 0.0,
            "history": [],
            "betslip": [],
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
            timeout=25,
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
# TIME
# =========================================================

def format_match_time(date_string):

    if not date_string:
        return "--:--"

    try:
        dt = datetime.fromisoformat(
            date_string.replace("Z", "+00:00")
        )

        return dt.astimezone().strftime("%H:%M")

    except Exception:
        return "--:--"


# =========================================================
# TODAY MATCHES
# =========================================================

def get_today_matches():

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data, error = football_request(
        "fixtures",
        {
            "date": today,
            "timezone": "Africa/Addis_Ababa",
        },
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

        status = fixture.get("status", {}).get(
            "short", ""
        )

        matches.append({
            "id": fixture.get("id"),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
            "home_logo": home.get("logo"),
            "away_logo": away.get("logo"),
            "league": league.get("name", "Unknown"),
            "country": league.get("country", ""),
            "time": format_match_time(
                fixture.get("date")
            ),
            "date": fixture.get("date"),
            "status": status,
            "odds": {
                "1": "-",
                "X": "-",
                "2": "-",
            },
            "markets": {
                "Over 2.5": "-",
                "Under 2.5": "-",
                "BTTS": "-",
                "No BTTS": "-",
            },
        })

    return matches, None


# =========================================================
# ODDS PARSER
# =========================================================

def empty_odds():

    return {
        "1": "-",
        "X": "-",
        "2": "-",
        "Over 2.5": "-",
        "Under 2.5": "-",
        "BTTS": "-",
        "No BTTS": "-",
    }


def parse_odds(data):

    result = empty_odds()

    response = data.get("response", [])

    if not response:
        return result

    bookmakers = response[0].get(
        "bookmakers", []
    )

    if not bookmakers:
        return result

    bets = bookmakers[0].get("bets", [])

    for bet in bets:

        bet_name = str(
            bet.get("name", "")
        ).lower()

        bet_id = bet.get("id")

        values = bet.get("values", [])

        # 1X2
        if (
            "match winner" in bet_name
            or bet_id == 1
        ):

            for value in values:

                label = str(
                    value.get("value", "")
                ).lower()

                odd = value.get("odd", "-")

                if label in ("home", "1"):
                    result["1"] = odd

                elif label in ("draw", "x"):
                    result["X"] = odd

                elif label in ("away", "2"):
                    result["2"] = odd

        # OVER UNDER
        if (
            "over/under" in bet_name
            or bet_id == 5
        ):

            for value in values:

                label = str(
                    value.get("value", "")
                ).lower()

                odd = value.get("odd", "-")

                if "over 2.5" in label:
                    result["Over 2.5"] = odd

                elif "under 2.5" in label:
                    result["Under 2.5"] = odd

        # BTTS
        if (
            "both teams" in bet_name
            or bet_id == 8
        ):

            for value in values:

                label = str(
                    value.get("value", "")
                ).lower()

                odd = value.get("odd", "-")

                if label == "yes":
                    result["BTTS"] = odd

                elif label == "no":
                    result["No BTTS"] = odd

    return result


# =========================================================
# GET ODDS
# =========================================================

def get_match_odds(fixture_id):

    if not fixture_id:
        return empty_odds()

    data, error = football_request(
        "odds",
        {
            "fixture": fixture_id
        },
    )

    if error:
        return empty_odds()

    return parse_odds(data)


# =========================================================
# PREDICTION
# =========================================================

def get_prediction(fixture_id):

    if not fixture_id:
        return None

    data, error = football_request(
        "predictions",
        {
            "fixture": fixture_id
        },
    )

    if error:
        return None

    response = data.get("response", [])

    if not response:
        return None

    item = response[0]

    prediction = item.get(
        "predictions", {}
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
            "advice", ""
        ),
        "winner": winner.get("name"),
        "winner_comment": winner.get(
            "comment"
        ),
        "home_percent": percent.get(
            "home", "0%"
        ),
        "draw_percent": percent.get(
            "draw", "0%"
        ),
        "away_percent": percent.get(
            "away", "0%"
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
    }


# =========================================================
# CONFIDENCE
# =========================================================

def calculate_confidence(prediction):

    if not prediction:
        return 0

    values = []

    for key in (
        "home_percent",
        "draw_percent",
        "away_percent",
    ):

        try:
            value = float(
                str(
                    prediction.get(key, "0%")
                ).replace("%", "")
            )

            values.append(value)

        except Exception:
            pass

    if not values:
        return 0

    return int(max(values))


# =========================================================
# BEST BET
# =========================================================

def get_best_bet():

    matches, error = get_today_matches()

    if error:
        return None, error

    if not matches:
        return None, "Har'a match hin argamne."

    best = None

    for match in matches[:20]:

        prediction = get_prediction(
            match["id"]
        )

        if not prediction:
            continue

        confidence = calculate_confidence(
            prediction
        )

        if confidence <= 0:
            continue

        odds = get_match_odds(
            match["id"]
        )

        winner = prediction.get("winner")

        if winner:
            bet = winner
        else:
            bet = (
                prediction.get("advice")
                or "Analysis"
            )

        candidate = {
            **match,
            "prediction": prediction,
            "confidence": confidence,
            "bet": bet,
            "odds": odds,
        }

        if best is None:
            best = candidate

        elif confidence > best["confidence"]:
            best = candidate

    if best:
        return best, None

    return None, (
        "Prediction har'aaf hin argamne."
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
                callback_data="keno"
            ),
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
                callback_data="how"
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
                callback_data="matches"
            )
        ],

        [
            InlineKeyboardButton(
                "🎯 PREDICTIONS",
                callback_data="prediction"
            )
        ],

        [
            InlineKeyboardButton(
                "🔥 BEST BET",
                callback_data="best_bet"
            )
        ],

        [
            InlineKeyboardButton(
                "🔴 LIVE",
                callback_data="live"
            )
        ],

        [
            InlineKeyboardButton(
                "🏆 LEAGUES",
                callback_data="leagues"
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
                "⬅️ HOME",
                callback_data="home"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# BETSLIP
# =========================================================

def betslip_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🗑️ CLEAR",
                callback_data="clear_betslip"
            )
        ],

        [
            InlineKeyboardButton(
                "💰 DEMO BET",
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
                "🏠 HOME",
                callback_data="home"
            )
        ],
    ])


def add_to_betslip(
    user_id,
    fixture_id,
    home,
    away,
    market,
    selection,
    odd,
):

    user = get_user(user_id)

    user["betslip"] = [
        item
        for item in user["betslip"]
        if not (
            str(item["fixture_id"])
            == str(fixture_id)
            and item["market"] == market
        )
    ]

    user["betslip"].append({
        "fixture_id": fixture_id,
        "home": home,
        "away": away,
        "market": market,
        "selection": selection,
        "odd": float(odd),
    })


def get_betslip_text(user_id):

    user = get_user(user_id)

    slips = user["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet tokko illee hin qabdu.\n\n"
            "⚽ Football → match → odds filadhu."
        )

    total_odds = 1.0

    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(
        slips,
        start=1
    ):

        odd = float(item["odd"])
        total_odds *= odd

        text += (
            f"*{i}.* "
            f"{item['home']} vs "
            f"{item['away']}\n"
            f"🎯 {item['market']}: "
            f"*{item['selection']}*\n"
            f"📊 Odd: *{odd:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 Total Odds: *{total_odds:.2f}*\n\n"
        "⚠️ Demo betting qofa."
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
        "⚽ Football matches\n"
        "💰 Odds\n"
        "🎯 Predictions\n"
        "🔥 Best Bet\n"
        "🔴 Live football\n"
        "🎟️ Bet Slip\n\n"
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

    # -----------------------------------------------------
    # HOME
    # -----------------------------------------------------

    if data == "home":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filadhu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # FOOTBALL
    # -----------------------------------------------------

    if data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Filannoo kee godhi.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # TODAY MATCHES
    # -----------------------------------------------------

    if data == "matches":

        matches, error = get_today_matches()

        if error:

            await query.edit_message_text(
                f"⚽ *TODAY MATCHES*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        if not matches:

            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\n"
                "Har'a match hin argamne.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        text = "📅 *TODAY MATCHES*\n\n"
        keyboard = []

        for match in matches[:15]:

            text += (
                f"⚽ *{match['home']}* vs "
                f"*{match['away']}*\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']}\n\n"
            )

            keyboard.append([
                InlineKeyboardButton(
                    (
                        f"⚽ {match['home']} "
                        f"vs {match['away']}"
                    )[:60],
                    callback_data=(
                        f"match|{match['id']}"
                    )
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ FOOTBALL",
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
        return

    # -----------------------------------------------------
    # MATCH DETAILS
    # -----------------------------------------------------

    if data.startswith("match|"):

        fixture_id = data.split("|")[1]

        matches, error = get_today_matches()

        if error:
            await query.answer(
                "API error.",
                show_alert=True
            )
            return

        selected = None

        for match in matches:

            if str(match["id"]) == str(
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
                f"{prediction.get('away_percent', '0%')}"
            )

        else:

            prediction_text = (
                "🎯 Prediction: Unavailable"
            )

        text = (
            "⚽ *MATCH DETAILS*\n\n"
            f"*{selected['home']} vs "
            f"{selected['away']}*\n\n"
            f"🏆 {selected['league']}\n"
            f"🌍 {selected['country']}\n"
            f"🕐 {selected['time']}\n\n"
            "💰 *1X2 ODDS*\n"
            f"1️⃣ {odds['1']}\n"
            f"❌ {odds['X']}\n"
            f"2️⃣ {odds['2']}\n\n"
            "📊 *MARKETS*\n"
            f"⬆️ Over 2.5: {odds['Over 2.5']}\n"
            f"⬇️ Under 2.5: {odds['Under 2.5']}\n"
            f"⚽ BTTS Yes: {odds['BTTS']}\n"
            f"🚫 BTTS No: {odds['No BTTS']}\n\n"
            f"{prediction_text}\n\n"
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
                ),
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
                    callback_data="matches"
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # ADD BET
    # -----------------------------------------------------

    if data.startswith("bet|"):

        parts = data.split("|")

        if len(parts) != 3:
            return

        fixture_id = parts[1]
        code = parts[2]

        matches, error = get_today_matches()

        if error:
            await query.answer(
                "API error.",
                show_alert=True
            )
            return

        selected = None

        for match in matches:

            if str(match["id"]) == str(
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
            ),
        }

        if code not in mapping:

            await query.answer(
                "Selection hin beekamne.",
                show_alert=True
            )
            return

        market, selection, odd = mapping[code]

        try:
            odd_value = float(
                str(odd).replace(",", ".")
            )

        except Exception:

            await query.answer(
                "Odd hin jiru.",
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
            odd_value,
        )

        await query.answer(
            "✅ Bet slip keessa gale.",
            show_alert=True
        )

        await query.edit_message_text(
            get_betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # BET SLIP
    # -----------------------------------------------------

    if data == "betslip":

        await query.edit_message_text(
            get_betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # CLEAR BETSLIP
    # -----------------------------------------------------

    if data == "clear_betslip":

        get_user(user.id)["betslip"] = []

        await query.edit_message_text(
            "🗑️ *BET SLIP QULQULLEESAME*\n\n"
            "Bet slip kee duwwaa dha.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # DEMO BET
    # -----------------------------------------------------

    if data == "place_bet":

        user_data = get_user(user.id)

        slips = user_data["betslip"]

        if not slips:

            await query.answer(
                "Bet slip duwwaa dha.",
                show_alert=True
            )
            return

        stake = 10.0

        if user_data["balance"] < stake:

            await query.edit_message_text(
                "💳 *BALANCE XIQQA*\n\n"
                f"Balance: "
                f"*{user_data['balance']:.2f}*\n\n"
                f"Demo stake: *{stake:.2f}*\n\n"
                "⚠️ Deposit dhugaa hin jiru.",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
            return

        total_odds = 1.0

        for item in slips:
            total_odds *= float(item["odd"])

        potential = stake * total_odds

        user_data["balance"] -= stake

        user_data["history"].append({
            "time": datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M"),
            "stake": stake,
            "odds": total_odds,
            "potential": potential,
            "status": "OPEN",
        })

        user_data["betslip"] = []

        await query.edit_message_text(
            "✅ *DEMO BET PLACED*\n\n"
            f"💰 Stake: *{stake:.2f}*\n"
            f"📈 Odds: *{total_odds:.2f}*\n"
            f"🏆 Potential: *{potential:.2f}*\n\n"
            "⚠️ Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # BEST BET
    # -----------------------------------------------------

    if data == "best_bet":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "⏳ Match fi prediction barbaadaa jira...",
            parse_mode="Markdown"
        )

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                f"🎯 *BEST BET*\n\n"
                f"❌ {error}\n\n"
                "FOOTBALL_API_KEY fi API quota kee ilaali.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        prediction = match["prediction"]

        text = (
            "🔥 *BEST BET*\n\n"
            f"⚽ *{match['home']} vs "
            f"{match['away']}*\n\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🎯 Pick: *{match['bet']}*\n"
            f"📊 Confidence: *{match['confidence']}%*\n\n"
            f"🏠 Home: {prediction['home_percent']}\n"
            f"🤝 Draw: {prediction['draw_percent']}\n"
            f"✈️ Away: {prediction['away_percent']}\n\n"
            f"1️⃣ {match['odds']['1']}\n"
            f"❌ {match['odds']['X']}\n"
            f"2️⃣ {match['odds']['2']}\n\n"
            "⚠️ Prediction bu'aa mirkanaa'aa miti."
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    f"1️⃣ {match['odds']['1']}",
                    callback_data=(
                        f"bet|{match['id']}|1"
                    )
                ),
                InlineKeyboardButton(
                    f"❌ {match['odds']['X']}",
                    callback_data=(
                        f"bet|{match['id']}|X"
                    )
                ),
                InlineKeyboardButton(
                    f"2️⃣ {match['odds']['2']}",
                    callback_data=(
                        f"bet|{match['id']}|2"
                    )
                ),
            ],

            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip"
                )
            ],

            [
                InlineKeyboardButton(
                    "⚽ FOOTBALL",
                    callback_data="football"
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # PREDICTION
    # -----------------------------------------------------

    if data == "prediction":

        match, error = get_best_bet()

        if error:

            await query.edit_message_text(
                f"🎯 *PREDICTION*\n\n"
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
            f"🔥 Pick: *{match['bet']}*\n"
            f"📊 Confidence: "
            f"*{match['confidence']}%*\n\n"
            f"🏠 Home: {prediction['home_percent']}\n"
            f"🤝 Draw: {prediction['draw_percent']}\n"
            f"✈️ Away: {prediction['away_percent']}\n\n"
            "⚠️ API analysis qofa."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # LIVE
    # -----------------------------------------------------

    if data == "live":

        live_data, error = football_request(
            "fixtures",
            {"live": "all"}
        )

        if error:

            await query.edit_message_text(
                f"🔴 *LIVE*\n\n❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        live = live_data.get(
            "response", []
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

        for item in live[:15]:

            teams = item.get(
                "teams", {}
            )

            goals = item.get(
                "goals", {}
            )

            home = teams.get(
                "home", {}
            ).get(
                "name", "Home"
            )

            away = teams.get(
                "away", {}
            ).get(
                "name", "Away"
            )

            hg = goals.get("home")
            ag = goals.get("away")

            elapsed = item.get(
                "fixture", {}
            ).get(
                "status", {}
            ).get(
                "elapsed"
            )

            text += (
                f"⚽ *{home}* "
                f"{hg or 0}-{ag or 0} "
                f"*{away}*\n"
                f"⏱️ {elapsed or '-'}'\n\n"
            )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # LEAGUES
    # -----------------------------------------------------

    if data == "leagues":

        await query.edit_message_text(
            "🏆 *POPULAR LEAGUES*\n\n"
            "🇬🇧 Premier League\n"
            "🇪🇸 La Liga\n"
            "🇮🇹 Serie A\n"
            "🇩🇪 Bundesliga\n"
            "🇫🇷 Ligue 1\n"
            "🏆 UEFA Champions League\n"
            "🏆 UEFA Europa League\n\n"
            "⚠️ League filtering dabalataan "
            "ni cimsina.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if data == "profile":

        user_data = get_user(user.id)

        await query.edit_message_text(
            "👤 *PROFILE*\n\n"
            f"Name: *{user_data['name']}*\n"
            f"User ID: `{user.id}`\n"
            f"Balance: *{user_data['balance']:.2f}*\n"
            f"Bets: *{len(user_data['history'])}*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # BALANCE
    # -----------------------------------------------------

    if data == "balance":

        user_data = get_user(user.id)

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            f"Balance kee: "
            f"*{user_data['balance']:.2f}*\n\n"
            "⚠️ Real-money deposit hin hojjenne.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    if data == "history":

        user_data = get_user(user.id)

        history = user_data["history"]

        if not history:

            text = (
                "📜 *HISTORY*\n\n"
                "History hin jiru."
            )

        else:

            text = "📜 *HISTORY*\n\n"

            for item in history[-10:]:

                text += (
                    f"🕐 {item['time']}\n"
                    f"💰 Stake: {item['stake']:.2f}\n"
                    f"📈 Odds: {item['odds']:.2f}\n"
                    f"🏆 Potential: {item['potential']:.2f}\n"
                    f"📌 {item['status']}\n\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # WINNERS
    # -----------------------------------------------------

    if data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo winners board yeroo ammaa "
            "qopheeffamaa jira.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # HOW
    # -----------------------------------------------------

    if data == "how":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ Football seeni\n"
            "2️⃣ Match filadhu\n"
            "3️⃣ Odds filadhu\n"
            "4️⃣ Bet Slip ilaali\n"
            "5️⃣ Demo bet qofa yaali\n\n"
            "⚠️ Kun demo/testing system dha.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # SUPPORT
    # -----------------------------------------------------

    if data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Rakkoo yoo qabaatte admin kee qunnami.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return

    # -----------------------------------------------------
    # KENO
    # -----------------------------------------------------

    if data == "keno":

        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "Keno system dabalataan ni ijaarrama.\n\n"
            "Amma Football irratti xiyyeeffanna.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        return


# =========================================================
# FLASK WEBSITE
# =========================================================

@web_app.route("/")
def index():

    if os.path.exists("index.html"):
        return send_from_directory(
            ".",
            "index.html"
        )

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>BEST BET</title>
        <meta name="viewport"
              content="width=device-width, initial-scale=1">
    </head>
    <body>
        <h1>🎯 BEST BET</h1>
        <p>Football prediction system is running.</p>
    </body>
    </html>
    """


@web_app.route("/api/status")
def api_status():

    return jsonify({
        "status": "online",
        "service": "BEST BET",
        "football_api": bool(
            FOOTBALL_API_KEY
        ),
        "telegram_bot": bool(
            BOT_TOKEN
        ),
    })


@web_app.route("/api/matches")
def api_matches():

    matches, error = get_today_matches()

    if error:
        return jsonify({
            "success": False,
            "error": error,
            "matches": [],
        }), 500

    # Odds are API calls, so limit them.
    for match in matches[:15]:

        odds = get_match_odds(
            match["id"]
        )

        match["odds"] = odds

    return jsonify({
        "success": True,
        "count": len(matches),
        "matches": matches,
    })


@web_app.route("/api/live")
def api_live():

    data, error = football_request(
        "fixtures",
        {"live": "all"}
    )

    if error:

        return jsonify({
            "success": False,
            "error": error,
            "matches": [],
        }), 500

    return jsonify({
        "success": True,
        "matches": data.get(
            "response", []
        ),
    })


@web_app.route("/api/prediction/<int:fixture_id>")
def api_prediction(fixture_id):

    prediction = get_prediction(
        fixture_id
    )

    if not prediction:

        return jsonify({
            "success": False,
            "error": "Prediction hin argamne.",
        }), 404

    return jsonify({
        "success": True,
        "prediction": prediction,
        "confidence": calculate_confidence(
            prediction
        ),
    })


@web_app.route("/api/odds/<int:fixture_id>")
def api_odds(fixture_id):

    odds = get_match_odds(
        fixture_id
    )

    return jsonify({
        "success": True,
        "fixture_id": fixture_id,
        "odds": odds,
    })


@web_app.route("/api/best-bet")
def api_best_bet():

    match, error = get_best_bet()

    if error:

        return jsonify({
            "success": False,
            "error": error,
        }), 404

    return jsonify({
        "success": True,
        "best_bet": match,
    })


# =========================================================
# FLASK SERVER
# =========================================================

def run_web():

    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN hin jiru. "
            "Render Environment Variables keessatti "
            "BOT_TOKEN galchi."
        )

    # Start Flask in background.
    web_thread = threading.Thread(
        target=run_web,
        daemon=True,
    )

    web_thread.start()

    print(
        "===================================="
    )
    print("BEST BET BOT STARTING")
    print(
        "===================================="
    )
    print(
        f"Web server: PORT {PORT}"
    )
    print(
        f"Football API: "
        f"{'READY' if FOOTBALL_API_KEY else 'MISSING'}"
    )
    print(
        "Telegram Bot: READY"
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print(
        "Telegram polling started..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
