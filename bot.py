import os
import random
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# NEW API
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "10000"))

WEB_APP_URL = "https://best-bet-7t7f.onrender.com"

ODDS_API_URL = "https://api.odds-api.io/v3"

# You can change this to another bookmaker later.
BOOKMAKERS = os.getenv(
    "BOOKMAKERS",
    "Bet365,Betfair,Betway,Unibet"
).strip()

web_app = Flask(__name__)

users = {}


# =========================================================
# USER DATA - DEMO ONLY
# =========================================================

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
# ODDS-API.IO REQUEST
# =========================================================

def odds_request(endpoint, params=None):
    if not ODDS_API_KEY:
        return None, "ODDS_API_KEY hin jiru."

    try:
        final_params = dict(params or {})
        final_params["apiKey"] = ODDS_API_KEY

        response = requests.get(
            f"{ODDS_API_URL}/{endpoint}",
            params=final_params,
            timeout=20,
        )

        if response.status_code != 200:
            try:
                body = response.json()
            except Exception:
                body = response.text[:300]

            return None, (
                f"Odds API HTTP {response.status_code}: "
                f"{body}"
            )

        data = response.json()

        if isinstance(data, dict) and data.get("error"):
            return None, str(data["error"])

        return data, None

    except requests.RequestException as e:
        return None, str(e)


# =========================================================
# TIME
# =========================================================

def format_time(date_string):
    if not date_string:
        return "--:--"

    try:
        dt = datetime.fromisoformat(
            date_string.replace("Z", "+00:00")
        )

        return dt.astimezone().strftime("%H:%M")

    except Exception:
        return "--:--"


def local_date(date_string):
    if not date_string:
        return ""

    try:
        dt = datetime.fromisoformat(
            date_string.replace("Z", "+00:00")
        )

        return dt.astimezone().strftime("%Y-%m-%d")

    except Exception:
        return ""


# =========================================================
# ODDS DEFAULT
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
        "bookmaker": "-",
    }


# =========================================================
# PARSE ODDS-API.IO
# =========================================================

def safe_float(value):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def parse_odds(data):
    result = empty_odds()

    if not isinstance(data, dict):
        return result

    bookmakers = data.get("bookmakers", {})

    if not isinstance(bookmakers, dict):
        return result

    # -----------------------------------------------------
    # Find available bookmaker
    # -----------------------------------------------------

    selected_bookmaker = None

    preferred = [
        x.strip()
        for x in BOOKMAKERS.split(",")
        if x.strip()
    ]

    for name in preferred:
        if name in bookmakers:
            selected_bookmaker = name
            break

    if not selected_bookmaker:
        names = list(bookmakers.keys())

        if names:
            selected_bookmaker = names[0]

    if not selected_bookmaker:
        return result

    result["bookmaker"] = selected_bookmaker

    markets = bookmakers.get(
        selected_bookmaker,
        [],
    )

    if not isinstance(markets, list):
        return result

    # -----------------------------------------------------
    # Markets
    # -----------------------------------------------------

    for market in markets:
        market_name = str(
            market.get("name", "")
        ).lower()

        values = market.get("odds", [])

        if not values:
            continue

        first = values[0]

        if not isinstance(first, dict):
            continue

        # -------------------------------------------------
        # MATCH WINNER / ML
        # -------------------------------------------------

        if market_name in (
            "ml",
            "match winner",
            "moneyline",
        ):
            result["1"] = first.get(
                "home",
                result["1"],
            )

            result["X"] = first.get(
                "draw",
                result["X"],
            )

            result["2"] = first.get(
                "away",
                result["2"],
            )

        # -------------------------------------------------
        # TOTALS / OVER UNDER
        # -------------------------------------------------

        elif (
            "total" in market_name
            or "over" in market_name
        ):
            line = safe_float(
                first.get("hdp")
                or first.get("max")
            )

            over = first.get("over")
            under = first.get("under")

            if line == 2.5 or line is None:
                if over is not None:
                    result["Over 2.5"] = over

                if under is not None:
                    result["Under 2.5"] = under

        # -------------------------------------------------
        # BTTS
        # -------------------------------------------------

        elif (
            "both teams" in market_name
            or "btts" in market_name
        ):
            yes = first.get("yes")
            no = first.get("no")

            if yes is not None:
                result["BTTS"] = yes

            if no is not None:
                result["No BTTS"] = no

    return result


# =========================================================
# EVENTS
# =========================================================

def get_events(status="pending", limit=100):
    data, error = odds_request(
        "events",
        {
            "sport": "football",
            "status": status,
            "limit": limit,
        },
    )

    if error:
        return [], error

    if not isinstance(data, list):
        return [], "Events response sirrii miti."

    return data, None


# =========================================================
# NORMALIZE EVENT
# =========================================================

def normalize_event(item):
    league = item.get("league") or {}

    return {
        "id": item.get("id"),
        "home": item.get("home", "Home"),
        "away": item.get("away", "Away"),
        "date": item.get("date"),
        "league": league.get(
            "name",
            "Unknown League",
        ),
        "league_slug": league.get(
            "slug",
            "",
        ),
        "status": item.get(
            "status",
            "pending",
        ),
        "time": format_time(
            item.get("date")
        ),
        "odds": empty_odds(),
    }


# =========================================================
# TODAY MATCHES
# =========================================================

def get_today_matches():
    events, error = get_events(
        status="pending",
        limit=100,
    )

    if error:
        return [], error

    today = datetime.now(
        timezone.utc
    ).astimezone().strftime(
        "%Y-%m-%d"
    )

    matches = []

    for item in events:
        event = normalize_event(item)

        if local_date(event["date"]) != today:
            continue

        matches.append(event)

    matches.sort(
        key=lambda x: x.get("date") or ""
    )

    return matches, None


# =========================================================
# ODDS FOR MULTIPLE EVENTS
# =========================================================

def get_multi_odds(matches):
    if not matches:
        return matches

    ids = [
        str(x["id"])
        for x in matches[:10]
        if x.get("id")
    ]

    if not ids:
        return matches

    data, error = odds_request(
        "odds/multi",
        {
            "eventIds": ",".join(ids),
            "bookmakers": BOOKMAKERS,
        },
    )

    if error:
        # Do not destroy matches if odds fail.
        return matches

    if not isinstance(data, list):
        return matches

    odds_map = {}

    for item in data:
        try:
            event_id = str(item.get("id"))
        except Exception:
            continue

        odds_map[event_id] = parse_odds(item)

    for match in matches:
        event_id = str(match.get("id"))

        if event_id in odds_map:
            match["odds"] = odds_map[event_id]

    return matches


# =========================================================
# SINGLE EVENT ODDS
# =========================================================

def get_match_odds(event_id):
    data, error = odds_request(
        "odds",
        {
            "eventId": event_id,
            "bookmakers": BOOKMAKERS,
        },
    )

    if error:
        return empty_odds()

    return parse_odds(data)


# =========================================================
# IMPLIED PROBABILITY
# =========================================================

def implied_probability(odd):
    value = safe_float(odd)

    if not value or value <= 1:
        return 0.0

    return 1.0 / value


# =========================================================
# BEST MARKET
# =========================================================

def choose_best_market(match):
    odds = match.get(
        "odds",
        empty_odds(),
    )

    candidates = []

    markets = [
        (
            "1X2",
            match.get("home", "Home"),
            odds.get("1"),
        ),
        (
            "1X2",
            "Draw",
            odds.get("X"),
        ),
        (
            "1X2",
            match.get("away", "Away"),
            odds.get("2"),
        ),
        (
            "Over/Under 2.5",
            "Over 2.5",
            odds.get("Over 2.5"),
        ),
        (
            "Over/Under 2.5",
            "Under 2.5",
            odds.get("Under 2.5"),
        ),
        (
            "BTTS",
            "Yes",
            odds.get("BTTS"),
        ),
    ]

    for market, selection, odd in markets:
        probability = implied_probability(odd)

        if probability <= 0:
            continue

        candidates.append(
            {
                "market": market,
                "selection": selection,
                "odd": safe_float(odd),
                "probability": probability,
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["probability"],
        reverse=True,
    )

    best = candidates[0]

    best["confidence"] = int(
        best["probability"] * 100
    )

    return best


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

    # One request for up to 10 events.
    matches = get_multi_odds(matches[:10])

    candidates = []

    for match in matches:
        best = choose_best_market(match)

        if not best:
            continue

        match["best_market"] = best
        match["bet"] = best["selection"]
        match["confidence"] = best["confidence"]

        candidates.append(match)

    if not candidates:
        return None, (
            "Odds har'aaf hin argamne."
        )

    candidates.sort(
        key=lambda x: (
            x["confidence"],
            -(safe_float(
                x["best_market"]["odd"]
            ) or 999)
        ),
        reverse=True,
    )

    return candidates[0], None


# =========================================================
# TELEGRAM MENUS
# =========================================================

def main_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎮 PLAY BEST BET",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🎯 BEST BET",
                    callback_data="best_bet",
                )
            ],
            [
                InlineKeyboardButton(
                    "⚽ FOOTBALL",
                    callback_data="football",
                ),
                InlineKeyboardButton(
                    "⚡ KENO FAST",
                    callback_data="keno",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip",
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 PROFILE",
                    callback_data="profile",
                ),
                InlineKeyboardButton(
                    "💳 BALANCE",
                    callback_data="balance",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 HISTORY",
                    callback_data="history",
                ),
                InlineKeyboardButton(
                    "🏆 WINNERS",
                    callback_data="winners",
                ),
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ HOW TO PLAY",
                    callback_data="how",
                )
            ],
            [
                InlineKeyboardButton(
                    "📞 SUPPORT",
                    callback_data="support",
                )
            ],
        ]
    )


def football_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📅 TODAY MATCHES",
                    callback_data="matches",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎯 BEST BET",
                    callback_data="best_bet",
                ),
                InlineKeyboardButton(
                    "🔴 LIVE",
                    callback_data="live",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏆 LEAGUES",
                    callback_data="leagues",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip",
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐 OPEN BEST BET",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ BACK",
                    callback_data="home",
                )
            ],
        ]
    )


def betslip_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑️ CLEAR",
                    callback_data="clear",
                ),
                InlineKeyboardButton(
                    "💰 DEMO BET",
                    callback_data="place",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚽ FOOTBALL",
                    callback_data="football",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ HOME",
                    callback_data="home",
                )
            ],
        ]
    )


def keno_menu():
    rows = []

    for start in range(1, 81, 10):
        rows.append(
            [
                InlineKeyboardButton(
                    str(n),
                    callback_data=f"keno_{n}",
                )
                for n in range(
                    start,
                    start + 10
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🎲 RANDOM DRAW",
                callback_data="keno_draw",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ HOME",
                callback_data="home",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


# =========================================================
# BET SLIP
# =========================================================

def add_bet(
    user_id,
    match,
    market,
    selection,
    odd,
):
    user = get_user(user_id)

    try:
        odd = float(
            str(odd).replace(",", ".")
        )
    except Exception:
        return False

    if odd <= 1:
        return False

    user["betslip"] = [
        x
        for x in user["betslip"]
        if not (
            str(x["fixture_id"])
            == str(match["id"])
            and x["market"] == market
        )
    ]

    user["betslip"].append(
        {
            "fixture_id": match["id"],
            "home": match["home"],
            "away": match["away"],
            "market": market,
            "selection": selection,
            "odd": odd,
        }
    )

    return True


def betslip_text(user_id):
    user = get_user(user_id)
    slips = user["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa odds filadhu."
        )

    total = 1.0

    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(
        slips,
        1,
    ):
        total *= item["odd"]

        text += (
            f"*{i}.* "
            f"{item['home']} vs "
            f"{item['away']}\n"
            f"🎯 {item['market']}: "
            f"*{item['selection']}*\n"
            f"📊 Odd: "
            f"*{item['odd']:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* "
        f"{total:.2f}\n\n"
        "⚠️ Demo/testing qofa."
    )

    return text


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    get_user(
        user.id,
        user.first_name or "User",
    )

    await update.message.reply_text(
        f"👋 Baga nagaan dhuftan "
        f"*{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n\n"
        "⚽ Football odds\n"
        "📊 Best market analysis\n"
        "🎟️ Bet Slip\n"
        "🔴 Live football\n"
        "🌐 Website\n\n"
        "👇 *PLAY BEST BET* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    get_user(
        user.id,
        user.first_name or "User",
    )

    data = query.data

    # -----------------------------------------------------
    # HOME
    # -----------------------------------------------------

    if data == "home":
        await query.edit_message_text(
            "🏠 *BEST BET*\n\n"
            "Menu keessaa filadhu.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # FOOTBALL
    # -----------------------------------------------------

    elif data == "football":
        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Football keessaa filadhu.",
            reply_markup=football_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # MATCHES
    # -----------------------------------------------------

    elif data == "matches":
        matches, error = get_today_matches()

        if error:
            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )
            return

        if not matches:
            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\n"
                "Match har'aa hin argamne.",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )
            return

        matches = get_multi_odds(
            matches[:10]
        )

        text = "📅 *TODAY'S MATCHES*\n\n"

        buttons = []

        for m in matches:
            o = m["odds"]

            text += (
                f"⚽ *{m['home']}* "
                f"vs *{m['away']}*\n"
                f"🏆 {m['league']}\n"
                f"🕐 {m['time']}\n"
                f"1️⃣ {o['1']}   "
                f"❌ {o['X']}   "
                f"2️⃣ {o['2']}\n\n"
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        (
                            f"⚽ "
                            f"{m['home']} vs "
                            f"{m['away']}"
                        )[:60],
                        callback_data=(
                            f"match|{m['id']}"
                        ),
                    )
                ]
            )

        buttons += [
            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ BACK",
                    callback_data="football",
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # MATCH DETAILS
    # -----------------------------------------------------

    elif data.startswith("match|"):
        fixture_id = data.split(
            "|",
            1,
        )[1]

        matches, error = get_today_matches()

        if error:
            await query.edit_message_text(
                f"❌ {error}",
                reply_markup=football_menu(),
            )
            return

        selected = next(
            (
                m
                for m in matches
                if str(m["id"])
                == fixture_id
            ),
            None,
        )

        if not selected:
            await query.answer(
                "Match hin argamne.",
                show_alert=True,
            )
            return

        odds = get_match_odds(
            fixture_id
        )

        text = (
            "⚽ *MATCH DETAILS*\n\n"
            f"*{selected['home']} "
            f"vs "
            f"{selected['away']}*\n"
            f"🏆 {selected['league']}\n"
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
            f"*{odds['BTTS']}*\n\n"
            f"🏦 Bookmaker: "
            f"*{odds['bookmaker']}*\n\n"
            "👇 Odds filadhu."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    f"1️⃣ {odds['1']}",
                    callback_data=(
                        f"bet|{fixture_id}|1"
                    ),
                ),
                InlineKeyboardButton(
                    f"❌ {odds['X']}",
                    callback_data=(
                        f"bet|{fixture_id}|X"
                    ),
                ),
                InlineKeyboardButton(
                    f"2️⃣ {odds['2']}",
                    callback_data=(
                        f"bet|{fixture_id}|2"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    (
                        f"⬆️ O2.5 "
                        f"{odds['Over 2.5']}"
                    ),
                    callback_data=(
                        f"bet|{fixture_id}|O25"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    (
                        f"⬇️ U2.5 "
                        f"{odds['Under 2.5']}"
                    ),
                    callback_data=(
                        f"bet|{fixture_id}|U25"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    (
                        f"⚽ BTTS "
                        f"{odds['BTTS']}"
                    ),
                    callback_data=(
                        f"bet|{fixture_id}|BTTS"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🎟️ BET SLIP",
                    callback_data="betslip",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ MATCHES",
                    callback_data="matches",
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # ADD BET
    # -----------------------------------------------------

    elif data.startswith("bet|"):
        _, fixture_id, code = data.split(
            "|"
        )

        matches, error = get_today_matches()

        if error:
            await query.answer(
                "API error.",
                show_alert=True,
            )
            return

        selected = next(
            (
                m
                for m in matches
                if str(m["id"])
                == fixture_id
            ),
            None,
        )

        if not selected:
            await query.answer(
                "Match hin argamne.",
                show_alert=True,
            )
            return

        odds = get_match_odds(
            fixture_id
        )

        mapping = {
            "1": (
                "1X2",
                selected["home"],
                odds["1"],
            ),
            "X": (
                "1X2",
                "Draw",
                odds["X"],
            ),
            "2": (
                "1X2",
                selected["away"],
                odds["2"],
            ),
            "O25": (
                "Over/Under 2.5",
                "Over 2.5",
                odds["Over 2.5"],
            ),
            "U25": (
                "Over/Under 2.5",
                "Under 2.5",
                odds["Under 2.5"],
            ),
            "BTTS": (
                "BTTS",
                "Yes",
                odds["BTTS"],
            ),
        }

        if code not in mapping:
            await query.answer(
                "Market hin jiru.",
                show_alert=True,
            )
            return

        if not add_bet(
            user.id,
            selected,
            *mapping[code],
        ):
            await query.answer(
                "Odd yeroo ammaa hin jiru.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            "✅ *Bet slip keessa gale!*\n\n"
            + betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # BET SLIP
    # -----------------------------------------------------

    elif data == "betslip":
        await query.edit_message_text(
            betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    elif data == "clear":
        get_user(user.id)["betslip"] = []

        await query.edit_message_text(
            "🗑️ Bet slip qulqullaa'e.",
            reply_markup=football_menu(),
        )

    # -----------------------------------------------------
    # DEMO BET
    # -----------------------------------------------------

    elif data == "place":
        u = get_user(user.id)

        slips = u["betslip"]

        if not slips:
            await query.answer(
                "Bet slip duwwaa dha.",
                show_alert=True,
            )
            return

        stake = 10.0
        total = 1.0

        for item in slips:
            total *= item["odd"]

        if u["balance"] < stake:
            await query.edit_message_text(
                "💳 *BALANCE XIQQAA*\n\n"
                f"Balance: "
                f"*{u['balance']:.2f}*\n"
                f"Demo stake: "
                f"*{stake:.2f}*\n\n"
                "⚠️ Real-money payment "
                "hin dabalamin.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )
            return

        potential = stake * total

        u["balance"] -= stake

        u["history"].append(
            {
                "time": datetime.now(
                    timezone.utc
                ).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "stake": stake,
                "odds": total,
                "potential": potential,
                "status": "OPEN",
            }
        )

        u["betslip"] = []

        await query.edit_message_text(
            "✅ *DEMO BET PLACED*\n\n"
            f"💰 Stake: *{stake:.2f}*\n"
            f"📈 Total Odds: "
            f"*{total:.2f}*\n"
            f"🏆 Potential: "
            f"*{potential:.2f}*\n"
            f"💳 Balance: "
            f"*{u['balance']:.2f}*\n\n"
            "⚠️ Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # BEST BET
    # -----------------------------------------------------

    elif data == "best_bet":
        match, error = get_best_bet()

        if error:
            await query.edit_message_text(
                "🎯 *BEST BET*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )
            return

        best = match["best_market"]

        text = (
            "🎯 *BEST BET*\n\n"
            f"⚽ *{match['home']} "
            f"vs "
            f"{match['away']}*\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🔥 Market: "
            f"*{best['market']}*\n"
            f"🎯 Selection: "
            f"*{best['selection']}*\n"
            f"📈 Odd: "
            f"*{best['odd']:.2f}*\n"
            f"📊 Implied probability: "
            f"*{best['confidence']}%*\n\n"
            f"🏦 Bookmaker: "
            f"*{match['odds']['bookmaker']}*\n\n"
            "⚠️ Kun odds irraa shallagame; "
            "bu'aa mirkanaa'aa miti."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # LIVE
    # -----------------------------------------------------

    elif data == "live":
        live, error = get_events(
            status="live",
            limit=100,
        )

        if error:
            await query.edit_message_text(
                "🔴 *LIVE*\n\n"
                f"❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )
            return

        if not live:
            await query.edit_message_text(
                "🔴 *LIVE*\n\n"
                "Ammaaf live match hin jiru.",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )
            return

        text = "🔴 *LIVE MATCHES*\n\n"

        for item in live[:15]:
            home = item.get(
                "home",
                "Home",
            )

            away = item.get(
                "away",
                "Away",
            )

            scores = item.get(
                "scores"
            ) or {}

            home_score = scores.get(
                "home",
                0,
            )

            away_score = scores.get(
                "away",
                0,
            )

            text += (
                f"⚽ *{home}* "
                f"{home_score or 0} - "
                f"{away_score or 0} "
                f"*{away}*\n"
            )

            text += "🔴 LIVE\n\n"

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # LEAGUES
    # -----------------------------------------------------

    elif data == "leagues":
        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "🏴 Premier League\n"
            "🇪🇸 La Liga\n"
            "🇮🇹 Serie A\n"
            "🇩🇪 Bundesliga\n"
            "🇫🇷 Ligue 1\n"
            "🏆 Champions League\n\n"
            "Events API irraa leagues "
            "bal'inaan argachuu dandeessa.",
            reply_markup=football_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # KENO
    # -----------------------------------------------------

    elif data == "keno":
        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "Lakkoofsa 1 hanga 80 keessaa filadhu.\n"
            "🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown",
        )

    elif (
        data.startswith("keno_")
        and data != "keno_draw"
    ):
        number = data.split(
            "_",
            1,
        )[1]

        await query.answer(
            f"Lakkoofsa {number} filatte.",
            show_alert=True,
        )

    elif data == "keno_draw":
        nums = sorted(
            random.sample(
                range(1, 81),
                10,
            )
        )

        await query.edit_message_text(
            "🎲 *KENO FAST DRAW*\n\n"
            + " • ".join(
                map(str, nums)
            )
            + "\n\n🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    elif data == "profile":
        u = get_user(user.id)

        await query.edit_message_text(
            f"👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: "
            f"*{u['balance']:.2f}*\n"
            f"Open selections: "
            f"*{len(u['betslip'])}*",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # BALANCE
    # -----------------------------------------------------

    elif data == "balance":
        u = get_user(user.id)

        await query.edit_message_text(
            f"💳 *BALANCE*\n\n"
            f"Balance: "
            f"*{u['balance']:.2f}*\n\n"
            "⚠️ Real-money payment "
            "hin dabalamin.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    elif data == "history":
        u = get_user(user.id)

        if not u["history"]:
            text = (
                "📜 *HISTORY*\n\n"
                "History hin jiru."
            )
        else:
            text = "📜 *HISTORY*\n\n"

            for item in u["history"][-10:]:
                text += (
                    f"🕐 {item['time']}\n"
                    f"💰 {item['stake']:.2f} | "
                    f"📈 {item['odds']:.2f} | "
                    f"{item['status']}\n\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # WINNERS
    # -----------------------------------------------------

    elif data == "winners":
        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo system keessatti "
            "winners list hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # HOW
    # -----------------------------------------------------

    elif data == "how":
        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football seeni\n"
            "2. 📅 Today's Matches bani\n"
            "3. Match filadhu\n"
            "4. Odd filadhu\n"
            "5. 🎟️ Bet Slip ilaali\n"
            "6. Demo bet qofa.\n\n"
            "⚠️ Real-money betting/payment "
            "hin dabalamin.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # SUPPORT
    # -----------------------------------------------------

    elif data == "support":
        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Bot owner/contact kee "
            "asitti dabali.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEBSITE HTML
# =========================================================

HTML = r"""
<!doctype html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>BEST BET</title>

<style>

*{
    box-sizing:border-box;
}

body{
    margin:0;
    font-family:Arial,sans-serif;
    background:#0d1724;
    color:#fff;
}

header{
    background:#152536;
    padding:18px;
    text-align:center;
    position:sticky;
    top:0;
    z-index:5;
}

.logo{
    font-size:30px;
    font-weight:900;
    color:#ffd400;
}

.sub{
    font-size:13px;
    color:#9fb0c2;
    margin-top:5px;
}

nav{
    display:flex;
    gap:8px;
    overflow:auto;
    padding:12px;
    background:#111f2e;
}

button{
    border:0;
    border-radius:12px;
    padding:12px 16px;
    background:#233547;
    color:#fff;
    font-weight:700;
}

button.active{
    background:#ffd400;
    color:#111;
}

main{
    padding:14px;
    max-width:900px;
    margin:auto;
}

.panel{
    background:#152536;
    border-radius:18px;
    padding:16px;
    margin-bottom:14px;
}

.match{
    background:#1b2c3d;
    border-radius:16px;
    padding:15px;
    margin:12px 0;
}

.teams{
    font-size:18px;
    font-weight:800;
    margin-bottom:7px;
}

.meta{
    font-size:12px;
    color:#9fb0c2;
    margin-bottom:12px;
}

.odds{
    display:grid;
    grid-template-columns:
        repeat(3,1fr);
    gap:8px;
}

.odd{
    background:#26394b;
    padding:12px;
    border-radius:12px;
    text-align:center;
    cursor:pointer;
}

.odd b{
    display:block;
    font-size:18px;
    margin-top:4px;
}

.markets{
    display:flex;
    gap:7px;
    flex-wrap:wrap;
    margin-top:9px;
}

.market{
    background:#203448;
    padding:8px 10px;
    border-radius:10px;
    font-size:12px;
}

.yellow{
    color:#ffd400;
}

.error{
    color:#ff7b7b;
}

.loader{
    text-align:center;
    padding:25px;
}

.small{
    color:#9fb0c2;
    font-size:12px;
}

</style>

</head>

<body>

<header>

<div class="logo">
🎯 BEST BET
</div>

<div class="sub">
Football odds • predictions • bet slip
</div>

</header>

<nav>

<button
    class="active"
    onclick="showTab('today',this)"
>
📅 Today
</button>

<button
    onclick="showTab('best',this)"
>
🎯 Best Bet
</button>

<button
    onclick="showTab('live',this)"
>
🔴 Live
</button>

<button
    onclick="showTab('slip',this)"
>
🎟️ Bet Slip
</button>

</nav>

<main>

<section
    id="content"
    class="panel"
>

<div class="loader">
Loading football...
</div>

</section>

</main>

<script>

let matches = [];
let slip = [];


function esc(x){

    return String(x ?? '').replace(
        /[&<>"']/g,
        m => ({
            '&':'&amp;',
            '<':'&lt;',
            '>':'&gt;',
            '"':'&quot;',
            "'":'&#039;'
        }[m])
    );

}


async function api(path){

    const r = await fetch(path);

    const d = await r.json();

    if(!r.ok){

        throw new Error(
            d.error || 'Server error'
        );

    }

    return d;

}


function showTab(tab,btn){

    document
        .querySelectorAll('nav button')
        .forEach(
            x => x.classList.remove(
                'active'
            )
        );

    if(btn){
        btn.classList.add('active');
    }

    if(tab === 'today')
        loadToday();

    if(tab === 'best')
        loadBest();

    if(tab === 'live')
        loadLive();

    if(tab === 'slip')
        renderSlip();

}


async function loadToday(){

    const c =
        document.getElementById(
            'content'
        );

    c.innerHTML =
        '<div class="loader">' +
        'Loading matches...' +
        '</div>';

    try{

        const d =
            await api('/api/matches');

        matches =
            d.matches || [];

        if(!matches.length){

            c.innerHTML =
                '<h2>📅 Today Matches</h2>' +
                '<div class="small">' +
                'No matches found.' +
                '</div>';

            return;

        }

        c.innerHTML =
            '<h2>📅 Today Matches</h2>' +
            '<div class="small">' +
            'Football • Odds API' +
            '</div>' +
            matches
                .map(renderMatch)
                .join('');

    }catch(e){

        c.innerHTML =
            '<h2>⚠️ Error</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>' +
            '<p class="small">' +
            'Check ODDS_API_KEY on Render.' +
            '</p>';

    }

}


function renderMatch(m){

    const o =
        m.odds || {};

    return `

    <div class="match">

        <div class="teams">

            ${esc(m.home)}

            <span class="small">
                vs
            </span>

            ${esc(m.away)}

        </div>

        <div class="meta">

            🏆 ${esc(m.league)}
            • 🕐 ${esc(m.time)}

        </div>

        <div class="odds">

            <div
                class="odd"
                onclick="addBet(
                    ${m.id},
                    '1',
                    ${JSON.stringify(
                        m.home
                    )},
                    ${JSON.stringify(
                        o['1'] || '-'
                    )}
                )"
            >

                1

                <b>
                    ${esc(
                        o['1'] || '-'
                    )}
                </b>

            </div>


            <div
                class="odd"
                onclick="addBet(
                    ${m.id},
                    'X',
                    'Draw',
                    ${JSON.stringify(
                        o['X'] || '-'
                    )}
                )"
            >

                X

                <b>
                    ${esc(
                        o['X'] || '-'
                    )}
                </b>

            </div>


            <div
                class="odd"
                onclick="addBet(
                    ${m.id},
                    '2',
                    ${JSON.stringify(
                        m.away
                    )},
                    ${JSON.stringify(
                        o['2'] || '-'
                    )}
                )"
            >

                2

                <b>
                    ${esc(
                        o['2'] || '-'
                    )}
                </b>

            </div>

        </div>


        <div class="markets">

            <div class="market">
                O2.5:
                <b>
                    ${esc(
                        o['Over 2.5'] || '-'
                    )}
                </b>
            </div>

            <div class="market">
                U2.5:
                <b>
                    ${esc(
                        o['Under 2.5'] || '-'
                    )}
                </b>
            </div>

            <div class="market">
                BTTS:
                <b>
                    ${esc(
                        o['BTTS'] || '-'
                    )}
                </b>
            </div>

        </div>

    </div>

    `;

}


async function loadBest(){

    const c =
        document.getElementById(
            'content'
        );

    c.innerHTML =
        '<div class="loader">' +
        'Analysing odds...' +
        '</div>';

    try{

        const d =
            await api('/api/best');

        const m =
            d.match;

        if(!m){

            c.innerHTML =
                '<h2>🎯 Best Bet</h2>' +
                '<div class="error">' +
                esc(
                    d.error ||
                    'No best market'
                ) +
                '</div>';

            return;

        }

        const b =
            m.best_market || {};

        c.innerHTML = `

        <h2>
            🎯 Best Bet
        </h2>

        <div class="match">

            <div class="teams">

                ${esc(m.home)}
                vs
                ${esc(m.away)}

            </div>

            <div class="meta">

                🏆 ${esc(m.league)}
                • 🕐 ${esc(m.time)}

            </div>

            <p class="yellow">

                <b>
                    Market:
                    ${esc(b.market)}
                </b>

            </p>

            <p>

                🎯 Selection:
                <b>
                    ${esc(b.selection)}
                </b>

            </p>

            <p>

                📈 Odd:
                <b>
                    ${esc(b.odd)}
                </b>

            </p>

            <p>

                📊 Implied probability:
                <b>
                    ${esc(
                        b.confidence
                    )}%
                </b>

            </p>

            <p class="small">

                This is odds-based analysis,
                not a guaranteed prediction.

            </p>

        </div>

        `;

    }catch(e){

        c.innerHTML =
            '<h2>🎯 Best Bet</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>';

    }

}


async function loadLive(){

    const c =
        document.getElementById(
            'content'
        );

    c.innerHTML =
        '<div class="loader">' +
        'Loading live...' +
        '</div>';

    try{

        const d =
            await api('/api/live');

        const list =
            d.matches || [];

        if(!list.length){

            c.innerHTML =
                '<h2>🔴 Live</h2>' +
                '<div class="small">' +
                'No live matches now.' +
                '</div>';

            return;

        }

        c.innerHTML =
            '<h2>🔴 Live</h2>' +

            list.map(
                x => `

                <div class="match">

                    <div class="teams">

                        ${esc(x.home)}
                        ${x.home_goals ?? 0}
                        -
                        ${x.away_goals ?? 0}
                        ${esc(x.away)}

                    </div>

                    <div class="meta">

                        🔴 LIVE

                    </div>

                </div>

                `
            ).join('');

    }catch(e){

        c.innerHTML =
            '<h2>🔴 Live</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>';

    }

}


function addBet(
    id,
    selection,
    label,
    odd
){

    if(
        !odd ||
        odd === '-' ||
        Number(odd) <= 1
    ){

        alert(
            'Odd is not available.'
        );

        return;

    }

    slip =
        slip.filter(
            x => !(
                x.id === id &&
                x.market === '1X2'
            )
        );

    slip.push({

        id:id,

        market:'1X2',

        selection:
            selection,

        label:
            label,

        odd:
            Number(odd)

    });

    alert(
        '✅ Added to Bet Slip'
    );

}


function renderSlip(){

    const c =
        document.getElementById(
            'content'
        );

    if(!slip.length){

        c.innerHTML =
            '<h2>🎟️ Bet Slip</h2>' +
            '<div class="small">' +
            'Your bet slip is empty.' +
            '</div>';

        return;

    }

    let total = 1;

    let html =
        '<h2>🎟️ Bet Slip</h2>';

    slip.forEach(
        (x,i) => {

            total *= x.odd;

            html += `

            <div class="match">

                <b>
                    ${i+1}.
                    ${esc(x.label)}
                </b>

                <div class="meta">

                    Selection:
                    ${esc(x.selection)}

                    • Odd:
                    ${x.odd.toFixed(2)}

                </div>

            </div>

            `;

        }
    );

    html += `

    <div class="panel">

        <b>
            Total Odds:
            ${total.toFixed(2)}
        </b>

    </div>

    <button
        onclick="
            slip=[];
            renderSlip()
        "
    >
        🗑️ Clear
    </button>

    `;

    c.innerHTML = html;

}


loadToday();

</script>

</body>
</html>
"""


# =========================================================
# FLASK ROUTES
# =========================================================

@web_app.route("/")
def index():
    return render_template_string(
        HTML
    )


@web_app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "Best Bet",
            "odds_api_key": bool(
                ODDS_API_KEY
            ),
            "api": "Odds-API.io",
        }
    )


@web_app.route("/api/matches")
def api_matches():
    matches, error = get_today_matches()

    if error:
        return jsonify(
            {
                "error": error
            }
        ), 500

    matches = get_multi_odds(
        matches[:10]
    )

    return jsonify(
        {
            "matches": matches
        }
    )


@web_app.route("/api/best")
def api_best():
    match, error = get_best_bet()

    if error:
        return jsonify(
            {
                "error": error
            }
        ), 404

    return jsonify(
        {
            "match": match
        }
    )


@web_app.route("/api/live")
def api_live():
    live, error = get_events(
        status="live",
        limit=100,
    )

    if error:
        return jsonify(
            {
                "error": error
            }
        ), 500

    result = []

    for item in live:
        scores = item.get(
            "scores"
        ) or {}

        result.append(
            {
                "id": item.get("id"),
                "home": item.get(
                    "home",
                    "Home",
                ),
                "away": item.get(
                    "away",
                    "Away",
                ),
                "home_goals": scores.get(
                    "home"
                ),
                "away_goals": scores.get(
                    "away"
                ),
                "date": item.get(
                    "date"
                ),
                "league": (
                    item.get("league")
                    or {}
                ).get(
                    "name",
                    "Unknown",
                ),
            }
        )

    return jsonify(
        {
            "matches": result
        }
    )


# =========================================================
# WEB SERVER
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
            "Render Environment Variables "
            "keessatti galchi."
        )

    if not ODDS_API_KEY:

        print(
            "WARNING: ODDS_API_KEY hin jiru."
        )

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print(
        "BEST BET BOT + WEBSITE started"
    )

    print(
        f"PORT={PORT}"
    )

    print(
        f"WEB_APP_URL={WEB_APP_URL}"
    )

    print(
        "API=Odds-API.io"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
