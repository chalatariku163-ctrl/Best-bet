import os
import logging
import threading
from datetime import datetime, timezone

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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "10000"))

ODDS_BASE_URL = "https://api.the-odds-api.com/v4"

# Regions:
# eu = European bookmakers
# uk = UK bookmakers
# us = US bookmakers
REGIONS = os.getenv("ODDS_REGIONS", "eu").strip()

# Featured football markets supported by Odds API
MARKETS = "h2h,spreads,totals"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


# =========================================================
# FLASK HEALTH CHECK
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "The Odds API",
        "api_key_configured": bool(ODDS_API_KEY),
        "time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "odds_api_key": bool(ODDS_API_KEY),
    })


def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# API HELPERS
# =========================================================

def api_headers():
    return {
        "Accept": "application/json",
        "User-Agent": "Best-Bet-Telegram-Bot/1.0",
    }


def check_api_key():
    if not ODDS_API_KEY:
        return False, (
            "⚠️ <b>Error</b>\n\n"
            "ODDS_API_KEY hin jiru.\n\n"
            "Render → Environment keessatti:\n"
            "<code>ODDS_API_KEY</code>\n"
            "galchi."
        )

    return True, ""


def get_sports():
    """
    Get currently available sports from The Odds API.
    """
    ok, error = check_api_key()

    if not ok:
        return None, error

    url = f"{ODDS_BASE_URL}/sports"

    params = {
        "apiKey": ODDS_API_KEY,
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=api_headers(),
            timeout=20,
        )

        if response.status_code != 200:
            return None, (
                f"⚠️ API Error\n\n"
                f"Status: {response.status_code}\n"
                f"{response.text[:500]}"
            )

        return response.json(), None

    except requests.RequestException as e:
        logger.exception("Sports API error")
        return None, f"⚠️ Connection error: {e}"


def get_football_sports():
    """
    Return active soccer competitions.
    """
    sports, error = get_sports()

    if error:
        return None, error

    football = []

    for sport in sports:
        key = sport.get("key", "")

        if key.startswith("soccer_") and sport.get("active"):
            football.append(sport)

    return football, None


def get_odds(sport_key):
    """
    Get current/upcoming football games and odds.
    """
    ok, error = check_api_key()

    if not ok:
        return None, error

    url = f"{ODDS_BASE_URL}/sports/{sport_key}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=api_headers(),
            timeout=25,
        )

        if response.status_code != 200:
            return None, (
                f"⚠️ <b>Odds API Error</b>\n\n"
                f"Status: <code>{response.status_code}</code>\n"
                f"{response.text[:700]}"
            )

        data = response.json()

        return data, None

    except requests.RequestException as e:
        logger.exception("Odds API error")
        return None, f"⚠️ Connection error: {e}"


# =========================================================
# ODDS PROCESSING
# =========================================================

def best_market_odds(event, market_key):
    """
    Find the best available bookmaker odds for one market.
    """

    best = []

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):

            if market.get("key") != market_key:
                continue

            for outcome in market.get("outcomes", []):

                name = outcome.get("name")
                price = outcome.get("price")

                if name is None or price is None:
                    continue

                best.append({
                    "name": name,
                    "price": float(price),
                    "bookmaker": bookmaker.get("title", "Unknown"),
                })

    return best


def event_summary(event):
    home = event.get("home_team", "Home")
    away = event.get("away_team", "Away")

    return f"{home} vs {away}"


def format_event(event):
    """
    Show useful markets for a single game.
    """

    home = event.get("home_team", "Home")
    away = event.get("away_team", "Away")

    text = (
        f"⚽ <b>{home}</b>\n"
        f"🆚 <b>{away}</b>\n\n"
    )

    # -----------------------------------------------------
    # 1X2
    # -----------------------------------------------------

    h2h = best_market_odds(event, "h2h")

    if h2h:
        text += "🎯 <b>1X2</b>\n"

        for outcome in h2h[:6]:
            text += (
                f"• {outcome['name']}: "
                f"<b>{outcome['price']:.2f}</b> "
                f"({outcome['bookmaker']})\n"
            )

        text += "\n"

    # -----------------------------------------------------
    # HANDICAP
    # -----------------------------------------------------

    spreads = best_market_odds(event, "spreads")

    if spreads:
        text += "📊 <b>Handicap</b>\n"

        for outcome in spreads[:6]:
            point = outcome.get("point")

            if point is not None:
                text += (
                    f"• {outcome['name']} "
                    f"({point}): "
                    f"<b>{outcome['price']:.2f}</b>\n"
                )
            else:
                text += (
                    f"• {outcome['name']}: "
                    f"<b>{outcome['price']:.2f}</b>\n"
                )

        text += "\n"

    # -----------------------------------------------------
    # OVER / UNDER
    # -----------------------------------------------------

    totals = best_market_odds(event, "totals")

    if totals:
        text += "⚽ <b>Over / Under</b>\n"

        for outcome in totals[:8]:
            point = outcome.get("point")

            if point is not None:
                text += (
                    f"• {outcome['name']} {point}: "
                    f"<b>{outcome['price']:.2f}</b>\n"
                )
            else:
                text += (
                    f"• {outcome['name']}: "
                    f"<b>{outcome['price']:.2f}</b>\n"
                )

        text += "\n"

    return text


# =========================================================
# BEST BET ALGORITHM
# =========================================================

def calculate_best_bet(event):
    """
    Simple confidence-based selection.

    This is NOT a guarantee of winning.
    It simply selects a relatively strong decimal price
    from available markets.
    """

    candidates = []

    h2h = best_market_odds(event, "h2h")

    for item in h2h:
        price = item["price"]

        # Lower decimal odds generally imply higher
        # bookmaker implied probability.
        if 1.15 <= price <= 2.20:
            confidence = 1 / price

            candidates.append({
                "market": "1X2",
                "selection": item["name"],
                "odds": price,
                "confidence": confidence,
                "bookmaker": item["bookmaker"],
            })

    totals = best_market_odds(event, "totals")

    for item in totals:

        price = item["price"]

        if 1.15 <= price <= 2.20:

            candidates.append({
                "market": "Over/Under",
                "selection": item["name"],
                "odds": price,
                "confidence": 1 / price,
                "bookmaker": item["bookmaker"],
            })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["confidence"],
        reverse=True,
    )

    return candidates[0]


# =========================================================
# TELEGRAM UI
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎯 Best Bet",
                callback_data="bestbet"
            ),
            InlineKeyboardButton(
                "🔴 Live",
                callback_data="live"
            ),
        ],
        [
            InlineKeyboardButton(
                "⚽ Football",
                callback_data="football"
            ),
            InlineKeyboardButton(
                "🎟️ Bet Slip",
                callback_data="betslip"
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ Help",
                callback_data="help"
            ),
        ],
    ])


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "👋 <b>Welcome to Best Bet</b>\n\n"
        "⚽ Football odds\n"
        "🎯 Best Bet\n"
        "📊 Multiple markets\n"
        "🎟️ Bet Slip\n\n"
        "Tap a button below:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# =========================================================
# FOOTBALL COMPETITIONS
# =========================================================

async def show_football(update, context):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⏳ <b>Football leagues loading...</b>",
        parse_mode="HTML",
    )

    sports, error = get_football_sports()

    if error:
        await query.edit_message_text(
            error,
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    if not sports:
        await query.edit_message_text(
            "⚠️ Ammaaf football competition active hin jiru.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    buttons = []

    # Most relevant competitions first
    preferred = [
        "soccer_epl",
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_usa_mls",
    ]

    ordered = []

    for key in preferred:
        for sport in sports:
            if sport.get("key") == key:
                ordered.append(sport)

    for sport in sports:
        if sport not in ordered:
            ordered.append(sport)

    for sport in ordered[:30]:

        title = sport.get("title", sport.get("key"))

        buttons.append([
            InlineKeyboardButton(
                f"⚽ {title}",
                callback_data=f"league:{sport['key']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="menu",
        )
    ])

    await query.edit_message_text(
        "⚽ <b>Select Football League</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# SHOW MATCHES
# =========================================================

async def show_matches(update, context):

    query = update.callback_query
    await query.answer()

    sport_key = query.data.split(":", 1)[1]

    await query.edit_message_text(
        "⏳ <b>Matches loading...</b>",
        parse_mode="HTML",
    )

    events, error = get_odds(sport_key)

    if error:
        await query.edit_message_text(
            error,
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    if not events:
        await query.edit_message_text(
            "⚠️ Taphoonni amma hin argamne.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    context.user_data["events"] = events
    context.user_data["sport_key"] = sport_key

    buttons = []

    for index, event in enumerate(events[:30]):

        home = event.get("home_team", "Home")
        away = event.get("away_team", "Away")

        buttons.append([
            InlineKeyboardButton(
                f"⚽ {home} - {away}",
                callback_data=f"event:{index}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Leagues",
            callback_data="football",
        )
    ])

    await query.edit_message_text(
        "⚽ <b>Select Match</b>\n\n"
        "Tap match tokko filadhu. Sana booda markets hedduu ni argita.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# MATCH DETAILS
# =========================================================

async def show_event(update, context):

    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split(":", 1)[1])
    except Exception:
        await query.edit_message_text(
            "⚠️ Match selection error.",
            reply_markup=main_menu(),
        )
        return

    events = context.user_data.get("events", [])

    if index >= len(events):
        await query.edit_message_text(
            "⚠️ Match hin argamne.",
            reply_markup=main_menu(),
        )
        return

    event = events[index]

    context.user_data["selected_event"] = event

    text = format_event(event)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏆 Best Bet",
                callback_data=f"pickbest:{index}",
            )
        ],
        [
            InlineKeyboardButton(
                "1️⃣ 1X2",
                callback_data=f"market1x2:{index}",
            ),
            InlineKeyboardButton(
                "⚽ O/U",
                callback_data=f"marketou:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Handicap",
                callback_data=f"marketspread:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎟️ Add to Bet Slip",
                callback_data=f"addslip:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Matches",
                callback_data=f"backmatches",
            )
        ],
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# MARKET: 1X2
# =========================================================

async def market_1x2(update, context):

    query = update.callback_query
    await query.answer()

    index = int(query.data.split(":", 1)[1])
    events = context.user_data.get("events", [])

    if index >= len(events):
        return

    event = events[index]

    outcomes = best_market_odds(event, "h2h")

    text = (
        f"🎯 <b>1X2</b>\n\n"
        f"⚽ {event.get('home_team')}\n"
        f"🆚 {event.get('away_team')}\n\n"
    )

    if not outcomes:
        text += "⚠️ 1X2 odds hin argamne."
    else:
        for item in outcomes:
            text += (
                f"• <b>{item['name']}</b> — "
                f"{item['price']:.2f}\n"
                f"  🏦 {item['bookmaker']}\n\n"
            )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data=f"event:{index}",
                )
            ]
        ]),
    )


# =========================================================
# MARKET: OVER / UNDER
# =========================================================

async def market_ou(update, context):

    query = update.callback_query
    await query.answer()

    index = int(query.data.split(":", 1)[1])
    events = context.user_data.get("events", [])

    if index >= len(events):
        return

    event = events[index]

    outcomes = best_market_odds(event, "totals")

    text = (
        f"⚽ <b>Over / Under</b>\n\n"
        f"{event.get('home_team')} vs "
        f"{event.get('away_team')}\n\n"
    )

    if not outcomes:
        text += "⚠️ O/U odds hin argamne."
    else:
        for item in outcomes:

            point = item.get("point")

            if point is not None:
                label = f"{item['name']} {point}"
            else:
                label = item["name"]

            text += (
                f"• <b>{label}</b> — "
                f"{item['price']:.2f}\n"
                f"  🏦 {item['bookmaker']}\n\n"
            )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data=f"event:{index}",
                )
            ]
        ]),
    )


# =========================================================
# MARKET: HANDICAP
# =========================================================

async def market_spread(update, context):

    query = update.callback_query
    await query.answer()

    index = int(query.data.split(":", 1)[1])
    events = context.user_data.get("events", [])

    if index >= len(events):
        return

    event = events[index]

    outcomes = best_market_odds(event, "spreads")

    text = (
        f"📊 <b>Handicap</b>\n\n"
        f"{event.get('home_team')} vs "
        f"{event.get('away_team')}\n\n"
    )

    if not outcomes:
        text += "⚠️ Handicap odds hin argamne."
    else:
        for item in outcomes:

            point = item.get("point")

            if point is not None:
                label = f"{item['name']} ({point})"
            else:
                label = item["name"]

            text += (
                f"• <b>{label}</b> — "
                f"{item['price']:.2f}\n"
                f"  🏦 {item['bookmaker']}\n\n"
            )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data=f"event:{index}",
                )
            ]
        ]),
    )


# =========================================================
# BEST BET
# =========================================================

async def best_bet(update, context):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⏳ <b>Best Bet searching...</b>",
        parse_mode="HTML",
    )

    sports, error = get_football_sports()

    if error:
        await query.edit_message_text(
            error,
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    all_candidates = []

    # Limit API calls to avoid unnecessary quota usage
    for sport in sports[:12]:

        events, err = get_odds(sport["key"])

        if err or not events:
            continue

        for event in events:

            pick = calculate_best_bet(event)

            if pick:

                pick["event"] = event
                pick["sport_title"] = sport.get(
                    "title",
                    sport.get("key"),
                )

                all_candidates.append(pick)

    if not all_candidates:

        await query.edit_message_text(
            "⚠️ <b>Best Bet hin argamne.</b>\n\n"
            "Ammaaf odds gahaa hin jirre ta'uu danda'a.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    all_candidates.sort(
        key=lambda x: x["confidence"],
        reverse=True,
    )

    top = all_candidates[:10]

    text = "🎯 <b>BEST BET</b>\n\n"

    buttons = []

    for i, item in enumerate(top):

        event = item["event"]

        home = event.get("home_team")
        away = event.get("away_team")

        text += (
            f"<b>{i + 1}. {home} vs {away}</b>\n"
            f"🏆 {item['market']}\n"
            f"👉 {item['selection']}\n"
            f"💰 Odds: <b>{item['odds']:.2f}</b>\n"
            f"🏦 {item['bookmaker']}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"🎟️ {home} vs {away}",
                callback_data=f"bestpick:{i}",
            )
        ])

    context.user_data["best_candidates"] = top

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Menu",
            callback_data="menu",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# BEST PICK DETAIL
# =========================================================

async def best_pick_detail(update, context):

    query = update.callback_query
    await query.answer()

    index = int(query.data.split(":", 1)[1])

    candidates = context.user_data.get(
        "best_candidates",
        [],
    )

    if index >= len(candidates):
        return

    item = candidates[index]

    event = item["event"]

    text = (
        "🎯 <b>BEST BET PICK</b>\n\n"
        f"⚽ <b>{event.get('home_team')}</b>\n"
        f"🆚 <b>{event.get('away_team')}</b>\n\n"
        f"🏆 Market: <b>{item['market']}</b>\n"
        f"👉 Pick: <b>{item['selection']}</b>\n"
        f"💰 Odds: <b>{item['odds']:.2f}</b>\n"
        f"🏦 Bookmaker: {item['bookmaker']}\n\n"
        "⚠️ Best Bet jechuun guarantee miti."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎟️ Add Bet Slip",
                    callback_data="addbest",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="bestbet",
                )
            ],
        ]),
    )


# =========================================================
# BET SLIP
# =========================================================

async def add_to_slip(update, context):

    query = update.callback_query
    await query.answer("Bet Slip keessatti dabalame!")

    index = int(query.data.split(":", 1)[1])

    events = context.user_data.get("events", [])

    if index >= len(events):
        return

    event = events[index]

    slip = context.user_data.setdefault(
        "betslip",
        [],
    )

    slip.append({
        "home": event.get("home_team"),
        "away": event.get("away_team"),
        "event": event.get("id"),
    })

    await query.edit_message_text(
        "🎟️ <b>Bet Slip</b>\n\n"
        f"⚽ {event.get('home_team')}\n"
        f"🆚 {event.get('away_team')}\n\n"
        "✅ Bet Slip keessatti dabalame.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


async def show_betslip(update, context):

    query = update.callback_query
    await query.answer()

    slip = context.user_data.get(
        "betslip",
        [],
    )

    if not slip:

        await query.edit_message_text(
            "🎟️ <b>Bet Slip</b>\n\n"
            "Bet hin dabalamin.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

        return

    text = "🎟️ <b>BET SLIP</b>\n\n"

    for i, bet in enumerate(slip, 1):

        text += (
            f"{i}. ⚽ {bet['home']}\n"
            f"   🆚 {bet['away']}\n\n"
        )

    text += "⚠️ Kun selection list qofa; payment/betting real hin raawwatu."

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗑️ Clear",
                    callback_data="clearslip",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Menu",
                    callback_data="menu",
                )
            ],
        ]),
    )


async def clear_slip(update, context):

    query = update.callback_query
    await query.answer()

    context.user_data["betslip"] = []

    await query.edit_message_text(
        "🗑️ Bet Slip qulqullaa'e.",
        reply_markup=main_menu(),
    )


# =========================================================
# LIVE
# =========================================================

async def show_live(update, context):

    query = update.callback_query
    await query.answer()

    # The Odds API can provide live and upcoming games,
    # but availability depends on sport/bookmaker coverage.
    sports, error = get_football_sports()

    if error:
        await query.edit_message_text(
            error,
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    found = []

    for sport in sports[:10]:

        events, err = get_odds(sport["key"])

        if err:
            continue

        for event in events:

            # Odds API data may not contain a universal
            # live-status field for every sport.
            # We therefore show events currently returned
            # by the provider rather than inventing scores.

            found.append(event)

    if not found:

        await query.edit_message_text(
            "🔴 <b>Live</b>\n\n"
            "Ammaaf live football odds hin argamne.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    text = (
        "🔴 <b>FOOTBALL ODDS</b>\n\n"
        f"Games returned: <b>{len(found)}</b>\n\n"
    )

    for event in found[:15]:

        text += (
            f"⚽ {event.get('home_team')}\n"
            f"🆚 {event.get('away_team')}\n\n"
        )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# =========================================================
# HELP
# =========================================================

async def show_help(update, context):

    query = update.callback_query
    await query.answer()

    text = (
        "ℹ️ <b>Best Bet Help</b>\n\n"
        "🎯 Best Bet — picks filatamee\n"
        "⚽ Football — league fi match filadhu\n"
        "1️⃣ 1X2 — Home / Draw / Away\n"
        "⚽ O/U — Over / Under\n"
        "📊 Handicap — handicap odds\n"
        "🎟️ Bet Slip — matches kuusaa\n\n"
        "⚠️ Odds jijjiiramuu danda'u; Best Bet guarantee miti."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def button_handler(update, context):

    query = update.callback_query

    data = query.data

    if data == "menu":
        await query.answer()

        await query.edit_message_text(
            "🏠 <b>Best Bet</b>\n\n"
            "Filannoo kee godhi:",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

    elif data == "football":
        await show_football(update, context)

    elif data.startswith("league:"):
        await show_matches(update, context)

    elif data.startswith("event:"):
        await show_event(update, context)

    elif data.startswith("market1x2:"):
        await market_1x2(update, context)

    elif data.startswith("marketou:"):
        await market_ou(update, context)

    elif data.startswith("marketspread:"):
        await market_spread(update, context)

    elif data == "bestbet":
        await best_bet(update, context)

    elif data.startswith("bestpick:"):
        await best_pick_detail(update, context)

    elif data.startswith("addslip:"):
        await add_to_slip(update, context)

    elif data == "betslip":
        await show_betslip(update, context)

    elif data == "clearslip":
        await clear_slip(update, context)

    elif data == "live":
        await show_live(update, context)

    elif data == "help":
        await show_help(update, context)

    elif data == "backmatches":

        sport_key = context.user_data.get(
            "sport_key"
        )

        if sport_key:

            # Fake callback routing by rebuilding
            # the same callback state.
            query = update.callback_query
            await query.answer()

            events = context.user_data.get(
                "events",
                [],
            )

            buttons = []

            for index, event in enumerate(events[:30]):

                buttons.append([
                    InlineKeyboardButton(
                        f"⚽ {event.get('home_team')} - "
                        f"{event.get('away_team')}",
                        callback_data=f"event:{index}",
                    )
                ])

            buttons.append([
                InlineKeyboardButton(
                    "⬅️ Leagues",
                    callback_data="football",
                )
            ])

            await query.edit_message_text(
                "⚽ <b>Select Match</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    elif data == "addbest":

        query = update.callback_query
        await query.answer("Best Bet saved!")

        best_candidates = context.user_data.get(
            "best_candidates",
            [],
        )

        if best_candidates:

            item = best_candidates[0]
            event = item["event"]

            slip = context.user_data.setdefault(
                "betslip",
                [],
            )

            slip.append({
                "home": event.get("home_team"),
                "away": event.get("away_team"),
                "event": event.get("id"),
                "selection": item["selection"],
                "odds": item["odds"],
            })

        await query.edit_message_text(
            "🎟️ <b>Best Bet Bet Slip keessatti dabalame.</b>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):

    logger.exception(
        "Telegram error:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not ODDS_API_KEY:
        logger.warning(
            "ODDS_API_KEY is missing."
        )

    # Flask web server
    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    application = (
        Application.builder()
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
            button_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Best Bet bot starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
