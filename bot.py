
import os
import random
import threading
import time
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
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip()

API_URL = "https://v3.football.api-sports.io"
TIMEZONE = "Africa/Addis_Ababa"

web_app = Flask(__name__)
users = {}

# Small in-memory cache. This reduces API calls and helps with rate limits.
CACHE = {}
CACHE_TTL = 60


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
# CACHE
# =========================================================

def cache_get(key):
    item = CACHE.get(key)
    if not item:
        return None

    if time.time() - item["time"] > CACHE_TTL:
        CACHE.pop(key, None)
        return None

    return item["value"]


def cache_set(key, value):
    CACHE[key] = {
        "time": time.time(),
        "value": value,
    }


# =========================================================
# FOOTBALL API
# =========================================================

def football_request(endpoint, params=None, cache_seconds=CACHE_TTL):
    if not FOOTBALL_API_KEY:
        return None, "FOOTBALL_API_KEY hin jiru."

    params = params or {}
    cache_key = (
        endpoint,
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
    )

    cached = cache_get(cache_key)
    if cached is not None:
        return cached, None

    try:
        r = requests.get(
            f"{API_URL}/{endpoint}",
            headers={
                "x-apisports-key": FOOTBALL_API_KEY,
                "Accept": "application/json",
            },
            params=params,
            timeout=20,
        )

        print(
            f"[API] {endpoint} "
            f"status={r.status_code} "
            f"params={params}"
        )

        if r.status_code != 200:
            try:
                body = r.json()
            except Exception:
                body = r.text[:500]

            return None, f"API HTTP {r.status_code}: {body}"

        data = r.json()

        if data.get("errors"):
            return None, f"API ERROR: {data['errors']}"

        if cache_seconds > 0:
            cache_set(cache_key, data)

        return data, None

    except requests.RequestException as e:
        return None, f"REQUEST ERROR: {e}"

    except ValueError as e:
        return None, f"JSON ERROR: {e}"


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


def get_today_matches():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data, error = football_request(
        "fixtures",
        {
            "date": today,
            "timezone": TIMEZONE,
        },
        cache_seconds=45,
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

        fixture_id = fixture.get("id")
        if not fixture_id:
            continue

        matches.append(
            {
                "id": fixture_id,
                "home": home.get("name", "Home"),
                "away": away.get("name", "Away"),
                "home_logo": home.get("logo", ""),
                "away_logo": away.get("logo", ""),
                "league": league.get("name", "Unknown"),
                "country": league.get("country", ""),
                "time": format_time(fixture.get("date")),
                "status": fixture.get("status", {}).get("short", ""),
            }
        )

    return matches, None


# =========================================================
# DYNAMIC ODDS / MARKETS
# =========================================================

def clean_market_name(name):
    name = str(name or "").strip()

    aliases = {
        "Match Winner": "🏆 Match Winner",
        "Both Teams Score": "⚽ Both Teams To Score",
        "Over/Under": "⚽ Goals Over/Under",
        "Double Chance": "🎯 Double Chance",
        "Draw No Bet": "🛡 Draw No Bet",
        "First Half Winner": "⏱ First Half Winner",
        "Second Half Winner": "⏱ Second Half Winner",
        "Correct Score": "🎯 Correct Score",
        "First Half Goals 0.5": "⏱ First Half Goals 0.5",
        "First Half Goals 1.5": "⏱ First Half Goals 1.5",
        "Goals Over/Under": "⚽ Goals Over/Under",
    }

    return aliases.get(name, name)


def get_match_odds(fixture_id):
    data, error = football_request(
        "odds",
        {
            "fixture": fixture_id,
            "timezone": TIMEZONE,
        },
        cache_seconds=60,
    )

    if error:
        print(f"[ODDS ERROR] fixture={fixture_id}: {error}")
        return [], error

    response = data.get("response", [])
    if not response:
        print(f"[ODDS] fixture={fixture_id}: no response/bookmaker")
        return [], "Odds hin argamne."

    # API-Football returns bookmakers -> bets -> values.
    # Merge bets with the same bet id across returned bookmakers.
    merged = {}

    for bookmaker in response:
        for bet in bookmaker.get("bets", []):
            bet_id = bet.get("id")
            bet_name = str(bet.get("name", "")).strip()

            if not bet_name:
                continue

            key = (bet_id, bet_name)

            if key not in merged:
                merged[key] = {
                    "id": bet_id,
                    "name": clean_market_name(bet_name),
                    "raw_name": bet_name,
                    "selections": [],
                }

            existing = merged[key]["selections"]
            seen = {
                (str(x.get("value")), str(x.get("odd")))
                for x in existing
            }

            for value in bet.get("values", []):
                label = str(value.get("value", "")).strip()
                odd = value.get("odd")

                if not label:
                    continue

                if odd is None:
                    continue

                try:
                    odd_float = float(str(odd).replace(",", "."))
                except Exception:
                    continue

                if odd_float <= 1:
                    continue

                item_key = (label, str(odd_float))
                if item_key in seen:
                    continue

                existing.append(
                    {
                        "value": label,
                        "odd": odd_float,
                    }
                )

    markets = list(merged.values())

    # Keep the response manageable in Telegram.
    # The Web App can still display all returned markets.
    markets.sort(key=lambda x: (
        x["id"] is None,
        x["id"] if x["id"] is not None else 9999,
        x["raw_name"],
    ))

    print(
        f"[ODDS] fixture={fixture_id}: "
        f"{len(markets)} markets"
    )

    return markets, None


def find_market_selection(markets, bet_id, selection_index):
    for market in markets:
        if str(market.get("id")) != str(bet_id):
            continue

        selections = market.get("selections", [])
        if 0 <= selection_index < len(selections):
            return market, selections[selection_index]

    return None, None


# =========================================================
# PREDICTIONS
# =========================================================

def get_prediction(fixture_id):
    data, error = football_request(
        "predictions",
        {"fixture": fixture_id},
        cache_seconds=120,
    )

    if error:
        print(f"[PREDICTION ERROR] {fixture_id}: {error}")
        return None

    response = data.get("response", [])
    if not response:
        return None

    item = response[0]
    prediction = item.get("predictions", {})
    winner = prediction.get("winner") or {}
    percent = prediction.get("percent") or {}
    goals = prediction.get("goals") or {}

    return {
        "advice": prediction.get("advice", ""),
        "winner": winner.get("name"),
        "home_percent": percent.get("home", "0%"),
        "draw_percent": percent.get("draw", "0%"),
        "away_percent": percent.get("away", "0%"),
        "under_over": prediction.get("under_over"),
        "goals_home": goals.get("home"),
        "goals_away": goals.get("away"),
    }


def confidence(prediction):
    if not prediction:
        return 0

    values = []

    for key in (
        "home_percent",
        "draw_percent",
        "away_percent",
    ):
        try:
            values.append(
                float(
                    str(
                        prediction.get(key, "0%")
                    ).replace("%", "")
                )
            )
        except Exception:
            pass

    return int(max(values)) if values else 0


def get_best_bet():
    matches, error = get_today_matches()

    if error:
        return None, error

    if not matches:
        return None, "Har'a match hin argamne."

    candidates = []

    for match in matches[:12]:
        prediction = get_prediction(match["id"])

        if not prediction:
            continue

        conf = confidence(prediction)

        if conf <= 0:
            continue

        match = dict(match)
        match["prediction"] = prediction
        match["confidence"] = conf
        match["bet"] = (
            prediction.get("winner")
            or prediction.get("advice")
            or "Analysis"
        )

        candidates.append(match)

    if not candidates:
        return None, "Prediction har'aaf hin argamne."

    candidates.sort(
        key=lambda x: x["confidence"],
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
                    web_app=WebAppInfo(url=WEB_APP_URL),
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
                    "🎯 PREDICTIONS",
                    callback_data="prediction",
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
                    web_app=WebAppInfo(url=WEB_APP_URL),
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
                    "🧪 DEMO BET",
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
                for n in range(start, start + 10)
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
# TELEGRAM ODDS DISPLAY
# =========================================================

def market_keyboard(fixture_id, markets):
    rows = []

    # Telegram callback_data is limited to 64 bytes.
    # We therefore store only fixture, bet id and selection index.
    for market in markets[:20]:
        bet_id = market.get("id")
        name = market.get("name", "Market")
        selections = market.get("selections", [])

        if bet_id is None or not selections:
            continue

        # Market title as a non-clickable text is not possible in
        # InlineKeyboard, so each row begins with the market name.
        for index, selection in enumerate(selections[:12]):
            label = selection["value"]
            odd = selection["odd"]

            button_text = f"{label} @ {odd:.2f}"

            # Keep each button readable on mobile.
            if len(button_text) > 55:
                button_text = button_text[:52] + "..."

            rows.append(
                [
                    InlineKeyboardButton(
                        f"{name}: {button_text}",
                        callback_data=(
                            f"pick|{fixture_id}|"
                            f"{bet_id}|{index}"
                        ),
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                callback_data="betslip",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ MATCHES",
                callback_data="matches",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def format_markets_text(markets):
    if not markets:
        return (
            "⚠️ *Odds available hin jiru.*\n\n"
            "Bookmaker/league kanaaf markets hin argamne."
        )

    text = "📊 *AVAILABLE BET MARKETS*\n\n"

    for market in markets[:20]:
        text += f"🔹 *{market['name']}*\n"

        for selection in market["selections"][:12]:
            text += (
                f"• {selection['value']} "
                f"@ *{selection['odd']:.2f}*\n"
            )

        if len(market["selections"]) > 12:
            text += "• ...\n"

        text += "\n"

    return text


# =========================================================
# BET SLIP
# =========================================================

def add_bet(
    user_id,
    match,
    market,
    selection,
    odd,
    bet_id=None,
):
    user = get_user(user_id)

    try:
        odd = float(str(odd).replace(",", "."))
    except Exception:
        return False

    if odd <= 1:
        return False

    # One selection per market for the same fixture.
    user["betslip"] = [
        x
        for x in user["betslip"]
        if not (
            str(x["fixture_id"]) == str(match["id"])
            and str(x.get("bet_id")) == str(bet_id)
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
            "bet_id": bet_id,
        }
    )

    return True


def total_bet_odds(user_id):
    total = 1.0

    for item in get_user(user_id)["betslip"]:
        total *= float(item["odd"])

    return total


def betslip_text(user_id):
    user = get_user(user_id)
    slips = user["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa market fi "
            "selection filadhu."
        )

    total = total_bet_odds(user_id)
    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(slips, 1):
        text += (
            f"*{i}.* {item['home']} vs {item['away']}\n"
            f"📊 {item['market']}\n"
            f"🎯 *{item['selection']}*\n"
            f"Odd: *{item['odd']:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* {total:.2f}\n\n"
        "🧪 Demo/testing qofa."
    )

    return text


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.first_name or "User")

    await update.message.reply_text(
        f"👋 Baga nagaan dhuftan *{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n\n"
        "⚽ Football odds\n"
        "📊 Multiple bet markets\n"
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
    get_user(user.id, user.first_name or "User")
    data = query.data or ""

    try:
        if data == "home":
            await query.edit_message_text(
                "🏠 *BEST BET*\n\nMenu keessaa filadhu.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data == "football":
            await query.edit_message_text(
                "⚽ *FOOTBALL*\n\nFootball keessaa filadhu.",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )

        elif data == "matches":
            matches, error = get_today_matches()

            if error:
                await query.edit_message_text(
                    f"⚽ *TODAY MATCHES*\n\n❌ {error}",
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

            text = "📅 *TODAY'S MATCHES*\n\n"
            buttons = []

            for m in matches[:20]:
                text += (
                    f"⚽ *{m['home']}* vs *{m['away']}*\n"
                    f"🏆 {m['league']}\n"
                    f"🕐 {m['time']}\n\n"
                )

                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"⚽ {m['home']} vs {m['away']}"[:60],
                            callback_data=f"match|{m['id']}",
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
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )

        elif data.startswith("match|"):
            fixture_id = data.split("|", 1)[1]
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
                    if str(m["id"]) == str(fixture_id)
                ),
                None,
            )

            if not selected:
                await query.answer(
                    "Match hin argamne.",
                    show_alert=True,
                )
                return

            markets, odds_error = get_match_odds(fixture_id)
            pred = get_prediction(fixture_id)
            conf = confidence(pred)

            if pred:
                pred_text = (
                    f"🎯 Prediction: "
                    f"*{pred.get('winner') or pred.get('advice') or 'Analysis'}*\n"
                    f"📊 Confidence: *{conf}%*\n"
                    f"🏠 Home: {pred.get('home_percent', '0%')}\n"
                    f"🤝 Draw: {pred.get('draw_percent', '0%')}\n"
                    f"✈️ Away: {pred.get('away_percent', '0%')}\n"
                )
            else:
                pred_text = "🎯 Prediction: *Unavailable*\n"

            if odds_error:
                market_text = (
                    f"⚠️ Odds error: `{odds_error}`\n\n"
                    "Markets hin argamne."
                )
            else:
                market_text = format_markets_text(markets)

            text = (
                "⚽ *MATCH DETAILS*\n\n"
                f"*{selected['home']} vs {selected['away']}*\n"
                f"🏆 {selected['league']}\n"
                f"🌍 {selected['country']}\n"
                f"🕐 {selected['time']}\n\n"
                f"{pred_text}\n"
                f"{market_text}\n"
                "👇 Market keessaa selection filadhu."
            )

            # Telegram message text has a size limit.
            if len(text) > 3900:
                text = text[:3850] + "\n\n... markets hedduu jiru."

            await query.edit_message_text(
                text,
                reply_markup=market_keyboard(
                    fixture_id,
                    markets,
                ),
                parse_mode="Markdown",
            )

        elif data.startswith("pick|"):
            parts = data.split("|")

            if len(parts) != 4:
                await query.answer(
                    "Selection dogoggora.",
                    show_alert=True,
                )
                return

            _, fixture_id, bet_id, index_text = parts

            try:
                selection_index = int(index_text)
            except ValueError:
                await query.answer(
                    "Selection dogoggora.",
                    show_alert=True,
                )
                return

            matches, error = get_today_matches()

            if error:
                await query.answer(
                    "Football API error.",
                    show_alert=True,
                )
                return

            selected = next(
                (
                    m
                    for m in matches
                    if str(m["id"]) == str(fixture_id)
                ),
                None,
            )

            if not selected:
                await query.answer(
                    "Match hin argamne.",
                    show_alert=True,
                )
                return

            markets, odds_error = get_match_odds(fixture_id)

            if odds_error:
                await query.answer(
                    "Odds yeroo ammaa hin jiru.",
                    show_alert=True,
                )
                return

            market, selection = find_market_selection(
                markets,
                bet_id,
                selection_index,
            )

            if not market or not selection:
                await query.answer(
                    "Selection hin argamne.",
                    show_alert=True,
                )
                return

            ok = add_bet(
                user.id,
                selected,
                market["raw_name"],
                selection["value"],
                selection["odd"],
                bet_id=bet_id,
            )

            if not ok:
                await query.answer(
                    "Selection hin galchine.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                "✅ *Selection Bet Slip keessa gale!*\n\n"
                + betslip_text(user.id),
                reply_markup=betslip_keyboard(),
                parse_mode="Markdown",
            )

        elif data == "betslip":
            await query.edit_message_text(
                betslip_text(user.id),
                reply_markup=betslip_keyboard(),
                parse_mode="Markdown",
            )

        elif data == "clear":
            get_user(user.id)["betslip"] = []

            await query.edit_message_text(
                "🗑️ Bet slip qulqullaa'e.",
                reply_markup=football_menu(),
            )

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
            total = total_bet_odds(user.id)

            if u["balance"] < stake:
                await query.edit_message_text(
                    "💳 *BALANCE XIQQAA*\n\n"
                    f"Balance: *{u['balance']:.2f}*\n"
                    f"Demo stake: *{stake:.2f}*\n\n"
                    "⚠️ Real-money payment hin dabalamin.",
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
                    ).strftime("%Y-%m-%d %H:%M"),
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
                f"📈 Total Odds: *{total:.2f}*\n"
                f"🏆 Potential: *{potential:.2f}*\n"
                f"💳 Balance: *{u['balance']:.2f}*\n\n"
                "⚠️ Demo/testing qofa.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data in ("best_bet", "prediction"):
            match, error = get_best_bet()

            if error:
                await query.edit_message_text(
                    f"🎯 *PREDICTION*\n\n❌ {error}",
                    reply_markup=football_menu(),
                    parse_mode="Markdown",
                )
                return

            p = match["prediction"]

            text = (
                "🎯 *BEST BET / PREDICTION*\n\n"
                f"⚽ *{match['home']} vs {match['away']}*\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']}\n\n"
                f"🔥 Prediction: *{match['bet']}*\n"
                f"📊 Confidence: *{match['confidence']}%*\n\n"
                f"🏠 Home: {p['home_percent']}\n"
                f"🤝 Draw: {p['draw_percent']}\n"
                f"✈️ Away: {p['away_percent']}\n\n"
                "⚠️ Prediction bu'aa mirkanaa'aa miti."
            )

            await query.edit_message_text(
                text,
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )

        elif data == "live":
            live, error = football_request(
                "fixtures",
                {"live": "all"},
                cache_seconds=20,
            )

            if error:
                await query.edit_message_text(
                    f"🔴 *LIVE*\n\n❌ {error}",
                    reply_markup=football_menu(),
                    parse_mode="Markdown",
                )
                return

            items = live.get("response", [])

            if not items:
                await query.edit_message_text(
                    "🔴 *LIVE*\n\n"
                    "Ammaaf live match hin jiru.",
                    reply_markup=football_menu(),
                    parse_mode="Markdown",
                )
                return

            text = "🔴 *LIVE MATCHES*\n\n"

            for item in items[:15]:
                teams = item.get("teams", {})
                goals = item.get("goals", {})
                status = item.get(
                    "fixture",
                    {},
                ).get("status", {})

                h = teams.get("home", {}).get(
                    "name",
                    "Home",
                )
                a = teams.get("away", {}).get(
                    "name",
                    "Away",
                )
                hg = goals.get("home")
                ag = goals.get("away")
                minute = status.get("elapsed") or "-"

                text += (
                    f"⚽ *{h}* {hg if hg is not None else 0} - "
                    f"{ag if ag is not None else 0} *{a}* "
                    f"⏱️ {minute}'\n\n"
                )

            await query.edit_message_text(
                text,
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )

        elif data == "leagues":
            await query.edit_message_text(
                "🏆 *LEAGUES*\n\n"
                "🏴 Premier League\n"
                "🇪🇸 La Liga\n"
                "🇮🇹 Serie A\n"
                "🇩🇪 Bundesliga\n"
                "🇫🇷 Ligue 1\n"
                "🏆 Champions League",
                reply_markup=football_menu(),
                parse_mode="Markdown",
            )

        elif data == "keno":
            await query.edit_message_text(
                "⚡ *KENO FAST*\n\n"
                "Lakkoofsa 1 hanga 80 keessaa filadhu.\n"
                "🧪 Demo qofa.",
                reply_markup=keno_menu(),
                parse_mode="Markdown",
            )

        elif data.startswith("keno_") and data != "keno_draw":
            number = data.split("_", 1)[1]

            await query.answer(
                f"Lakkoofsa {number} filatte.",
                show_alert=True,
            )

        elif data == "keno_draw":
            nums = sorted(
                random.sample(range(1, 81), 10)
            )

            await query.edit_message_text(
                "🎲 *KENO FAST DRAW*\n\n"
                + " • ".join(map(str, nums))
                + "\n\n🧪 Demo qofa.",
                reply_markup=keno_menu(),
                parse_mode="Markdown",
            )

        elif data == "profile":
            u = get_user(user.id)

            await query.edit_message_text(
                f"👤 *PROFILE*\n\n"
                f"Name: *{u['name']}*\n"
                f"Balance: *{u['balance']:.2f}*\n"
                f"Selections: *{len(u['betslip'])}*",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data == "balance":
            u = get_user(user.id)

            await query.edit_message_text(
                f"💳 *BALANCE*\n\n"
                f"Balance: *{u['balance']:.2f}*\n\n"
                "⚠️ Real-money payment hin dabalamin.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data == "history":
            u = get_user(user.id)

            if not u["history"]:
                text = "📜 *HISTORY*\n\nHistory hin jiru."
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

        elif data == "winners":
            await query.edit_message_text(
                "🏆 *WINNERS*\n\n"
                "Demo system keessatti winners list "
                "yeroo ammaa hin jiru.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data == "how":
            await query.edit_message_text(
                "ℹ️ *HOW TO PLAY*\n\n"
                "1. ⚽ Football seeni\n"
                "2. 📅 Today's Matches bani\n"
                "3. Match filadhu\n"
                "4. Market hedduu keessaa filadhu\n"
                "5. 🎟️ Bet Slip ilaali\n"
                "6. 🧪 Demo bet qofa.\n\n"
                "⚠️ Real-money payment/wagering hin dabalamin.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

        elif data == "support":
            await query.edit_message_text(
                "📞 *SUPPORT*\n\n"
                "Bot owner/contact kee asitti dabali.",
                reply_markup=main_menu(),
                parse_mode="Markdown",
            )

    except Exception as e:
        print(f"[BUTTON ERROR] {type(e).__name__}: {e}")

        try:
            await query.answer(
                "Rakkoo uumame. Mee irra deebi'i.",
                show_alert=True,
            )
        except Exception:
            pass


# =========================================================
# WEBSITE
# =========================================================

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>BEST BET - Football</title>

<style>
*{box-sizing:border-box}

body{
    margin:0;
    font-family:Arial,sans-serif;
    background:#0d1724;
    color:#fff;
}

header{
    background:#152536;
    padding:18px 16px;
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
    position:sticky;
    top:83px;
    z-index:4;
}

button{
    border:0;
    border-radius:12px;
    padding:12px 16px;
    background:#233547;
    color:#fff;
    font-weight:700;
    cursor:pointer;
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

h2{
    margin:0 0 12px;
}

.status{
    font-size:13px;
    color:#9fb0c2;
}

.error{
    color:#ff7b7b;
    white-space:pre-wrap;
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
    grid-template-columns:repeat(3,1fr);
    gap:8px;
}

.odd{
    background:#26394b;
    padding:12px;
    border-radius:12px;
    text-align:center;
    cursor:pointer;
}

.odd:hover{
    background:#30485d;
}

.odd b{
    display:block;
    font-size:18px;
    margin-top:4px;
}

.market{
    background:#172a3b;
    border-radius:14px;
    margin-top:12px;
    overflow:hidden;
}

.market-title{
    padding:12px;
    font-weight:800;
    background:#203448;
}

.market-grid{
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:8px;
    padding:10px;
}

.selection{
    background:#26394b;
    border:1px solid transparent;
    border-radius:11px;
    padding:11px;
    cursor:pointer;
    text-align:left;
    color:#fff;
}

.selection:hover{
    border-color:#ffd400;
}

.selection span{
    display:block;
    font-size:12px;
    color:#aebdca;
}

.selection b{
    display:block;
    margin-top:3px;
}

.badge{
    display:inline-block;
    background:#203448;
    padding:7px 10px;
    border-radius:10px;
    margin:3px;
    font-size:12px;
}

.yellow{
    color:#ffd400;
}

.green{
    color:#83d13b;
}

.slip-item{
    background:#1b2c3d;
    padding:12px;
    border-radius:12px;
    margin:8px 0;
}

@media(max-width:600px){
    .market-grid{
        grid-template-columns:1fr 1fr;
    }

    .teams{
        font-size:17px;
    }
}
</style>
</head>

<body>

<header>
    <div class="logo">🎯 BEST BET</div>
    <div class="sub">
        Football odds • multiple markets • bet slip
    </div>
</header>

<nav>
    <button class="active"
            onclick="showTab('today',this)">
        📅 Today
    </button>

    <button onclick="showTab('best',this)">
        🎯 Best Bet
    </button>

    <button onclick="showTab('live',this)">
        🔴 Live
    </button>

    <button onclick="showTab('slip',this)">
        🎟️ Bet Slip
    </button>
</nav>

<main>
    <section id="content" class="panel">
        Loading football...
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
        throw new Error(d.error || 'Server error');
    }

    return d;
}

function showTab(tab,btn){
    document
        .querySelectorAll('nav button')
        .forEach(x => x.classList.remove('active'));

    if(btn){
        btn.classList.add('active');
    }

    if(tab === 'today') loadToday();
    if(tab === 'best') loadBest();
    if(tab === 'live') loadLive();
    if(tab === 'slip') renderSlip();
}

async function loadToday(){
    const c = document.getElementById('content');

    c.innerHTML =
        '<div class="status">Loading matches...</div>';

    try{
        const d = await api('/api/matches');
        matches = d.matches || [];

        if(!matches.length){
            c.innerHTML =
                '<h2>📅 Today Matches</h2>' +
                '<div class="status">' +
                'No matches found.' +
                '</div>';
            return;
        }

        c.innerHTML =
            '<h2>📅 Today Matches</h2>' +
            '<div class="status">' +
            'Addis Ababa time • Real API data' +
            '</div>' +
            matches.map(renderMatch).join('');

    }catch(e){
        c.innerHTML =
            '<h2>⚠️ Error</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>' +
            '<p class="status">' +
            'Check FOOTBALL_API_KEY and API account on Render.' +
            '</p>';
    }
}

function renderMatch(m){
    return `
    <div class="match">
        <div class="teams">
            ${esc(m.home)}
            <span class="status">vs</span>
            ${esc(m.away)}
        </div>

        <div class="meta">
            🏆 ${esc(m.league)}
            • 🕐 ${esc(m.time)}
        </div>

        <button onclick="openMatch(${m.id})">
            📊 View all betting markets
        </button>
    </div>`;
}

async function openMatch(id){
    const c = document.getElementById('content');

    c.innerHTML =
        '<div class="status">Loading markets...</div>';

    try{
        const d = await api('/api/match/' + id);
        const m = d.match;
        const markets = d.markets || [];
        const p = d.prediction;

        let html = `
        <button onclick="loadToday()">⬅️ Matches</button>
        <h2 style="margin-top:15px">
            ⚽ ${esc(m.home)} vs ${esc(m.away)}
        </h2>

        <div class="status">
            🏆 ${esc(m.league)}
            • 🕐 ${esc(m.time)}
        </div>`;

        if(p){
            html += `
            <div class="market">
                <div class="market-title">
                    🎯 Prediction
                </div>
                <div style="padding:12px">
                    <div class="yellow">
                        <b>${esc(
                            p.winner || p.advice || 'Analysis'
                        )}</b>
                    </div>
                    <div class="meta">
                        Confidence:
                        ${esc(p.confidence || 0)}%
                    </div>
                </div>
            </div>`;
        }

        if(!markets.length){
            html += `
            <div class="market">
                <div class="market-title">
                    ⚠️ Odds
                </div>
                <div style="padding:12px"
                     class="error">
                    ${esc(
                        d.odds_error ||
                        'No betting markets available.'
                    )}
                </div>
            </div>`;
        }

        markets.forEach((market,mi) => {
            html += `
            <div class="market">
                <div class="market-title">
                    ${esc(market.name)}
                </div>
                <div class="market-grid">`;

            (market.selections || []).forEach(
                (s,si) => {
                    html += `
                    <button class="selection"
                            onclick="addBet(
                                ${m.id},
                                ${JSON.stringify(
                                    market.raw_name
                                )},
                                ${JSON.stringify(
                                    s.value
                                )},
                                ${Number(s.odd)},
                                ${Number(market.id ?? 0)}
                            )">
                        <span>
                            ${esc(s.value)}
                        </span>
                        <b>
                            @ ${Number(s.odd).toFixed(2)}
                        </b>
                    </button>`;
                }
            );

            html += `
                </div>
            </div>`;
        });

        html += `
        <div class="market">
            <div class="market-title">
                🎟️ Bet Slip
            </div>
            <div style="padding:12px">
                ${slip.length} selection(s)
                <br><br>
                <button onclick="renderSlip()">
                    Open Bet Slip
                </button>
            </div>
        </div>`;

        c.innerHTML = html;

    }catch(e){
        c.innerHTML =
            '<h2>⚠️ Error</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>';
    }
}

async function loadBest(){
    const c = document.getElementById('content');

    c.innerHTML =
        '<div class="status">Analysing...</div>';

    try{
        const d = await api('/api/best');
        const m = d.match;

        if(!m){
            c.innerHTML =
                '<h2>🎯 Best Bet</h2>' +
                '<div class="error">' +
                esc(d.error || 'No prediction') +
                '</div>';
            return;
        }

        const p = m.prediction || {};

        c.innerHTML = `
        <h2>🎯 Best Bet</h2>

        <div class="match">
            <div class="teams">
                ${esc(m.home)} vs ${esc(m.away)}
            </div>

            <div class="meta">
                🏆 ${esc(m.league)}
                • 🕐 ${esc(m.time)}
            </div>

            <p class="yellow">
                <b>
                    Prediction:
                    ${esc(m.bet)}
                </b>
            </p>

            <p>
                📊 Confidence:
                <b>${esc(m.confidence)}%</b>
            </p>

            <p>
                🏠 Home: ${esc(p.home_percent)}<br>
                🤝 Draw: ${esc(p.draw_percent)}<br>
                ✈️ Away: ${esc(p.away_percent)}
            </p>

            <button onclick="openMatch(${m.id})">
                📊 View all markets
            </button>
        </div>

        <div class="status">
            ⚠️ Prediction is analysis,
            not a guaranteed result.
        </div>`;
    }catch(e){
        c.innerHTML =
            '<h2>🎯 Best Bet</h2>' +
            '<div class="error">' +
            esc(e.message) +
            '</div>';
    }
}

async function loadLive(){
    const c = document.getElementById('content');

    c.innerHTML =
        '<div class="status">Loading live...</div>';

    try{
        const d = await api('/api/live');
        const list = d.matches || [];

        c.innerHTML =
            '<h2>🔴 Live</h2>' +
            (
                list.length
                ? list.map(x => `
                    <div class="match">
                        <div class="teams">
                            ${esc(x.home)}
                            ${x.home_goals ?? 0}
                            -
                            ${x.away_goals ?? 0}
                            ${esc(x.away)}
                        </div>
                        <div class="meta">
                            ⏱️ ${esc(
                                x.elapsed || '-'
                            )}'
                        </div>
                    </div>
                `).join('')
                : '<div class="status">' +
                  'No live matches now.' +
                  '</div>'
            );

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
    market,
    selection,
    odd,
    betId
){
    if(!odd || Number(odd) <= 1){
        alert('Odd is not available.');
        return;
    }

    // One selection per market for one fixture.
    slip = slip.filter(
        x => !(
            x.id === id &&
            String(x.betId) === String(betId)
        )
    );

    slip.push({
        id:id,
        market:market,
        selection:selection,
        odd:Number(odd),
        betId:betId
    });

    alert(
        '✅ ' +
        selection +
        ' added to Bet Slip'
    );
}

function renderSlip(){
    const c = document.getElementById('content');

    if(!slip.length){
        c.innerHTML =
            '<h2>🎟️ Bet Slip</h2>' +
            '<div class="status">' +
            'Your bet slip is empty.' +
            '</div>';
        return;
    }

    let total = 1;
    let html = '<h2>🎟️ Bet Slip</h2>';

    slip.forEach((x,i) => {
        total *= x.odd;

        html += `
        <div class="slip-item">
            <b>${i+1}. ${esc(x.market)}</b>
            <div class="meta">
                ${esc(x.selection)}
                • Odd: ${x.odd.toFixed(2)}
            </div>
            <button
                onclick="removeBet(${i})">
                Remove
            </button>
        </div>`;
    });

    html += `
    <div class="panel">
        <b>Total Odds:
            ${total.toFixed(2)}
        </b>
    </div>

    <button onclick="slip=[];renderSlip()">
        🗑️ Clear
    </button>

    <div class="status"
         style="margin-top:12px">
        🧪 Demo/testing qofa.
    </div>`;

    c.innerHTML = html;
}

function removeBet(index){
    slip.splice(index,1);
    renderSlip();
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
    return render_template_string(HTML)


@web_app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "Best Bet",
            "football_api_key": bool(
                FOOTBALL_API_KEY
            ),
            "web_app_url": WEB_APP_URL,
        }
    )


@web_app.route("/api/matches")
def api_matches():
    matches, error = get_today_matches()

    if error:
        return jsonify({"error": error}), 500

    return jsonify(
        {
            "matches": matches[:30]
        }
    )


@web_app.route("/api/match/<int:fixture_id>")
def api_match(fixture_id):
    matches, error = get_today_matches()

    if error:
        return jsonify({"error": error}), 500

    match = next(
        (
            m
            for m in matches
            if int(m["id"]) == fixture_id
        ),
        None,
    )

    if not match:
        return jsonify(
            {"error": "Match hin argamne."}
        ), 404

    markets, odds_error = get_match_odds(
        fixture_id
    )

    prediction = get_prediction(
        fixture_id
    )

    if prediction:
        prediction["confidence"] = confidence(
            prediction
        )

    return jsonify(
        {
            "match": match,
            "markets": markets,
            "odds_error": odds_error,
            "prediction": prediction,
        }
    )


@web_app.route("/api/best")
def api_best():
    match, error = get_best_bet()

    if error:
        return jsonify(
            {"error": error}
        ), 404

    return jsonify(
        {"match": match}
    )


@web_app.route("/api/live")
def api_live():
    data, error = football_request(
        "fixtures",
        {"live": "all"},
        cache_seconds=20,
    )

    if error:
        return jsonify(
            {"error": error}
        ), 500

    result = []

    for item in data.get("response", []):
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        status = item.get(
            "fixture",
            {},
        ).get("status", {})

        result.append(
            {
                "home": teams.get(
                    "home",
                    {}
                ).get(
                    "name",
                    "Home",
                ),
                "away": teams.get(
                    "away",
                    {}
                ).get(
                    "name",
                    "Away",
                ),
                "home_goals": goals.get(
                    "home"
                ),
                "away_goals": goals.get(
                    "away"
                ),
                "elapsed": status.get(
                    "elapsed"
                ),
            }
        )

    return jsonify(
        {"matches": result}
    )


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
            "Render Environment Variables keessatti galchi."
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
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("BEST BET BOT + WEBSITE started")
    print(f"PORT={PORT}")
    print(f"WEB_APP_URL={WEB_APP_URL}")
    print(
        "Dynamic multi-market odds system enabled."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
