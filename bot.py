import os
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, jsonify, render_template, request

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
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip().rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

ODDS_BASE = "https://api.the-odds-api.com/v4"

REGIONS = os.getenv(
    "ODDS_REGIONS",
    "eu,uk,us,au",
).strip()

REGION_FALLBACKS = ["eu", "uk", "us", "au"]

MAX_SOCCER_SPORTS = int(
    os.getenv("MAX_SOCCER_SPORTS", "50")
)

# Main match list markets
LIST_MARKETS = os.getenv(
    "LIST_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

# Detailed match markets
DETAIL_MARKETS = os.getenv(
    "DETAIL_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

# Today + next 6 days = 7 days total
DAYS_AHEAD = 7

# Cache prevents excessive Odds API requests
CACHE_SECONDS = int(
    os.getenv("CACHE_SECONDS", "60")
)

web_app = Flask(__name__)

USERS = {}

MATCH_CACHE = {
    "time": 0,
    "matches": [],
}


# =========================================================
# USER DATA
# =========================================================

def get_user(user_id, name="User"):
    if user_id not in USERS:
        USERS[user_id] = {
            "name": name,
            "balance": 0.0,
            "history": [],
            "betslip": [],
        }

    return USERS[user_id]


def total_odds(user_id):
    total = 1.0

    for item in get_user(user_id)["betslip"]:
        try:
            total *= float(item["odd"])
        except Exception:
            pass

    return total


def add_demo_bet(
    user_id,
    match,
    market,
    selection,
    odd,
    bet_id=None,
):
    try:
        odd = float(odd)
    except Exception:
        return False

    if odd <= 1:
        return False

    user = get_user(user_id)

    user["betslip"] = [
        x
        for x in user["betslip"]
        if not (
            str(x.get("fixture_id"))
            == str(match.get("id"))
            and str(x.get("bet_id"))
            == str(bet_id)
        )
    ]

    user["betslip"].append({
        "fixture_id": match.get("id"),
        "home": match.get("home", ""),
        "away": match.get("away", ""),
        "league": match.get("league", ""),
        "market": market,
        "selection": selection,
        "odd": odd,
        "bet_id": bet_id,
    })

    return True


def betslip_text(user_id):
    user = get_user(user_id)
    slips = user["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa selection filadhu."
        )

    total = total_odds(user_id)

    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(slips, 1):
        text += (
            f"*{i}.* "
            f"{item['home']} vs {item['away']}\n"
            f"🏆 {item.get('league', '')}\n"
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
# ODDS API REQUEST
# =========================================================

def odds_request(path, params=None):
    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru. "
            "Render → Environment keessatti "
            "ODDS_API_KEY galchi."
        )

    request_params = dict(params or {})
    request_params["apiKey"] = ODDS_API_KEY

    url = ODDS_BASE + path

    response = requests.get(
        url,
        params=request_params,
        timeout=40,
        headers={
            "Accept": "application/json",
            "User-Agent": "BestBet/1.0",
        },
    )

    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = response.text[:1000]

        raise RuntimeError(
            f"Odds API error {response.status_code}: {body}"
        )

    try:
        return response.json()
    except Exception:
        raise RuntimeError(
            "Odds API JSON deebisuu dadhabe."
        )


# =========================================================
# SOCCER SPORTS
# =========================================================

def soccer_sports():
    sports = odds_request("/sports")

    result = []

    for sport in sports:
        key = str(
            sport.get("key", "")
        ).strip()

        if (
            sport.get("active")
            and key.startswith("soccer_")
        ):
            result.append(sport)

    return result


# =========================================================
# REGION LIST
# =========================================================

def region_list():
    raw = [
        x.strip().lower()
        for x in REGIONS.split(",")
        if x.strip()
    ]

    result = []

    for region in raw + REGION_FALLBACKS:
        if region and region not in result:
            result.append(region)

    return result


# =========================================================
# SAFE FLOAT
# =========================================================

def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(event, sport):
    h2h = {}
    totals = {}
    btts = {}
    spreads = []

    bookmakers = event.get("bookmakers") or []

    for bookmaker in bookmakers:

        markets = bookmaker.get("markets") or []

        for market in markets:

            market_key = market.get("key")

            outcomes = market.get("outcomes") or []

            for outcome in outcomes:

                name = str(
                    outcome.get("name", "")
                ).strip()

                price = safe_float(
                    outcome.get("price")
                )

                if price is None or price <= 1:
                    continue

                # -----------------------------
                # 1X2
                # -----------------------------

                if market_key == "h2h":

                    if name == event.get("home_team"):
                        old = h2h.get("home")
                        if old is None or price > old:
                            h2h["home"] = price

                    elif name == event.get("away_team"):
                        old = h2h.get("away")
                        if old is None or price > old:
                            h2h["away"] = price

                    elif name.lower() == "draw":
                        old = h2h.get("draw")
                        if old is None or price > old:
                            h2h["draw"] = price

                # -----------------------------
                # TOTALS
                # -----------------------------

                elif market_key == "totals":

                    point = safe_float(
                        outcome.get("point")
                    )

                    if point is None:
                        continue

                    if point == 2.5:

                        if name.lower() == "over":
                            old = totals.get("over")
                            if old is None or price > old:
                                totals["over"] = price

                        elif name.lower() == "under":
                            old = totals.get("under")
                            if old is None or price > old:
                                totals["under"] = price

                # -----------------------------
                # BTTS
                # -----------------------------

                elif market_key in (
                    "btts",
                    "both_teams_to_score",
                ):

                    if name.lower() in (
                        "yes",
                        "btts yes",
                    ):
                        old = btts.get("yes")
                        if old is None or price > old:
                            btts["yes"] = price

                    elif name.lower() in (
                        "no",
                        "btts no",
                    ):
                        old = btts.get("no")
                        if old is None or price > old:
                            btts["no"] = price

                # -----------------------------
                # SPREAD / HANDICAP
                # -----------------------------

                elif market_key == "spreads":

                    spreads.append({
                        "name": name,
                        "point": outcome.get("point"),
                        "price": price,
                    })

    # Remove duplicate spread selections
    unique_spreads = []

    seen_spreads = set()

    for item in spreads:

        key = (
            item["name"],
            str(item["point"]),
            float(item["price"]),
        )

        if key not in seen_spreads:
            seen_spreads.add(key)
            unique_spreads.append(item)

    # Sort highest odds first for display diversity
    unique_spreads.sort(
        key=lambda x: float(x["price"]),
        reverse=True,
    )

    # =====================================================
    # BEST BET
    # =====================================================

    candidates = []

    if h2h.get("home"):
        candidates.append((
            "1",
            h2h["home"],
            "1X2",
        ))

    if h2h.get("draw"):
        candidates.append((
            "X",
            h2h["draw"],
            "1X2",
        ))

    if h2h.get("away"):
        candidates.append((
            "2",
            h2h["away"],
            "1X2",
        ))

    if totals.get("over"):
        candidates.append((
            "Over 2.5",
            totals["over"],
            "Over/Under",
        ))

    if totals.get("under"):
        candidates.append((
            "Under 2.5",
            totals["under"],
            "Over/Under",
        ))

    if btts.get("yes"):
        candidates.append((
            "BTTS Yes",
            btts["yes"],
            "BTTS",
        ))

    if btts.get("no"):
        candidates.append((
            "BTTS No",
            btts["no"],
            "BTTS",
        ))

    candidates = [
        x
        for x in candidates
        if 1.01 < float(x[1]) <= 20
    ]

    # Lowest odds = market's strongest implied selection.
    candidates.sort(
        key=lambda x: float(x[1])
    )

    best = None

    if candidates:

        selection, odd, market = candidates[0]

        best = {
            "selection": selection,
            "odd": float(odd),
            "market": market,
        }

    # =====================================================
    # LOCAL TIME
    # =====================================================

    commence = event.get(
        "commence_time",
        "",
    )

    time_text = ""

    if commence:

        try:

            dt = datetime.fromisoformat(
                commence.replace(
                    "Z",
                    "+00:00",
                )
            )

            local_dt = dt.astimezone(
                timezone(
                    timedelta(hours=3)
                )
            )

            time_text = local_dt.strftime(
                "%d/%m %H:%M"
            )

        except Exception:
            time_text = ""

    return {
        "id": event.get("id"),
        "sport_key": sport.get("key"),
        "league": sport.get(
            "title",
            "Football",
        ),
        "home": event.get(
            "home_team",
            "Home",
        ),
        "away": event.get(
            "away_team",
            "Away",
        ),
        "time": time_text,
        "commence_time": commence,
        "h2h": h2h,
        "totals": totals,
        "btts": btts,
        "spreads": unique_spreads,
        "best_bet": best,
    }


# =========================================================
# GET ODDS FOR ONE SPORT
# =========================================================

def get_soccer_odds_for_sport(
    sport_key,
    start_text,
    end_text,
):
    last_error = None

    for region in region_list():

        try:

            params = {
                "regions": region,
                "markets": LIST_MARKETS,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": start_text,
                "commenceTimeTo": end_text,
            }

            events = odds_request(
                f"/sports/{sport_key}/odds",
                params,
            )

            if events:

                print(
                    "[ODDS]",
                    sport_key,
                    "region=",
                    region,
                    "events=",
                    len(events),
                )

                return events, region

            print(
                "[NO ODDS]",
                sport_key,
                "region=",
                region,
            )

        except Exception as e:

            last_error = e

            print(
                "[REGION ERROR]",
                sport_key,
                region,
                repr(e),
            )

    if last_error:
        print(
            "[ALL REGIONS FAILED]",
            sport_key,
            repr(last_error),
        )

    return [], None


# =========================================================
# GET MATCHES
# =========================================================

def get_matches(force=False):

    now_timestamp = time.time()

    # Cache
    if (
        not force
        and MATCH_CACHE["matches"]
        and now_timestamp
        - MATCH_CACHE["time"]
        < CACHE_SECONDS
    ):
        return MATCH_CACHE["matches"]

    result = []

    try:
        sports = soccer_sports()

    except Exception as e:

        print(
            "[SPORTS ERROR]",
            repr(e),
        )

        return []

    print("====================================")
    print(
        "[SOCCER SPORTS]",
        len(sports),
    )
    print(
        "[REGIONS]",
        region_list(),
    )
    print("====================================")

    now = datetime.now(timezone.utc)

    # Today + next 6 days
    end_time = now + timedelta(
        days=DAYS_AHEAD
    )

    start_text = now.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    end_text = end_time.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    print(
        "[DATE FROM]",
        start_text,
    )

    print(
        "[DATE TO]",
        end_text,
    )

    for sport in sports[
        :MAX_SOCCER_SPORTS
    ]:

        sport_key = sport.get("key")

        if not sport_key:
            continue

        events, used_region = (
            get_soccer_odds_for_sport(
                sport_key,
                start_text,
                end_text,
            )
        )

        for event in events:

            try:

                commence = event.get(
                    "commence_time"
                )

                if not commence:
                    continue

                dt = datetime.fromisoformat(
                    commence.replace(
                        "Z",
                        "+00:00",
                    )
                )

                if dt < now:
                    continue

                if dt > end_time:
                    continue

                converted = convert_event(
                    event,
                    sport,
                )

                converted[
                    "odds_region"
                ] = (
                    used_region
                    or REGIONS
                )

                # Require at least one usable market
                if (
                    converted.get("h2h")
                    or converted.get("totals")
                    or converted.get("btts")
                    or converted.get("spreads")
                ):
                    result.append(
                        converted
                    )

            except Exception as e:

                print(
                    "[EVENT ERROR]",
                    repr(e),
                )

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    unique = {}

    for match in result:

        match_id = str(
            match.get("id") or ""
        )

        if match_id:
            unique[match_id] = match

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x:
        x.get(
            "commence_time",
            "",
        )
    )

    # =====================================================
    # CACHE
    # =====================================================

    MATCH_CACHE["time"] = time.time()
    MATCH_CACHE["matches"] = result

    daily = {}

    for match in result:

        date_key = (
            match.get(
                "commence_time",
                "",
            )[:10]
        )

        daily[date_key] = (
            daily.get(
                date_key,
                0,
            )
            + 1
        )

    print("====================================")
    print(
        "[TOTAL MATCHES]",
        len(result),
    )
    print(
        "[DAILY]",
        daily,
    )
    print("====================================")

    return result


# =========================================================
# TELEGRAM MENU
# =========================================================

def main_menu():

    return InlineKeyboardMarkup([
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
                "⚽ FOOTBALL",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            ),
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            ),
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
                "ℹ️ HOW TO PLAY",
                callback_data="how",
            ),
        ],
    ])


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
        "🎯 *BEST BET*\n"
        "⚽ Football\n"
        "📅 Today → Next 7 Days\n"
        "📊 Multiple Markets\n"
        "🎟️ Bet Slip\n\n"
        "👇 *⚽ FOOTBALL* cuqaasi.",
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

    q = update.callback_query

    await q.answer()

    user = q.from_user

    u = get_user(
        user.id,
        user.first_name or "User",
    )

    if q.data == "profile":

        await q.edit_message_text(
            f"👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: *{u['balance']:.2f}*\n"
            f"Bet Slip: *{len(u['betslip'])}*",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif q.data == "balance":

        await q.edit_message_text(
            f"💳 *BALANCE*\n\n"
            f"Balance: *{u['balance']:.2f}*\n\n"
            "🧪 Demo system qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif q.data == "history":

        if not u["history"]:

            text = (
                "📜 *HISTORY*\n\n"
                "History hin jiru."
            )

        else:

            text = (
                "📜 *HISTORY*\n\n"
            )

            for item in u[
                "history"
            ][-10:]:

                text += (
                    f"🕐 "
                    f"{item['time']}\n"
                    f"💰 "
                    f"{item['stake']:.2f} | "
                    f"📈 "
                    f"{item['odds']:.2f} | "
                    f"{item['status']}\n\n"
                )

        await q.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif q.data == "how":

        await q.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football bani\n"
            "2. 📅 Guyyaa filadhu\n"
            "3. ⚽ Match filadhu\n"
            "4. 📊 Market filadhu\n"
            "5. 🎯 Selection filadhu\n"
            "6. 🎟️ Bet Slip ilaali\n\n"
            "📅 Today irraa kaasee "
            "*guyyaa 7 guutuu* agarsiisa.\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEB ROUTES
# =========================================================

@web_app.route("/", methods=["GET"])
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@web_app.route(
    "/health",
    methods=["GET"],
)
def health():

    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "The Odds API",
        "api_key_configured":
            bool(ODDS_API_KEY),
        "list_markets":
            LIST_MARKETS,
        "detail_markets":
            DETAIL_MARKETS,
        "regions":
            region_list(),
        "days":
            DAYS_AHEAD,
        "cache_seconds":
            CACHE_SECONDS,
        "web_app":
            WEB_APP_URL,
    }), 200


# =========================================================
# API TEST
# =========================================================

@web_app.route(
    "/api/test",
    methods=["GET"],
)
def api_test():

    return jsonify({
        "success": True,
        "message":
            "BEST BET API is working.",
        "status": "online",
    }), 200


# =========================================================
# MATCHES API
# =========================================================

@web_app.route(
    "/api/matches",
    methods=["GET"],
)
def api_matches():

    try:

        force = (
            request.args.get(
                "refresh",
                "0",
            )
            == "1"
        )

        matches = get_matches(
            force=force
        )

        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football odds loaded "
                "for TODAY + NEXT 6 DAYS."
                if matches
                else
                "No football matches "
                "with available odds "
                "found in the 7-day period. "
                "Check ODDS_API_KEY, "
                "API quota and soccer coverage."
            ),
        }), 200

    except Exception as e:

        print(
            "[API MATCHES ERROR]",
            repr(e),
        )

        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(e),
            "message":
                "Football odds loading failed.",
        }), 500


# =========================================================
# SINGLE MATCH DETAILS
# =========================================================

@web_app.route(
    "/api/match/<match_id>",
    methods=["GET"],
)
def api_match(match_id):

    try:

        matches = get_matches()

        match = next(
            (
                x
                for x in matches
                if str(
                    x.get("id")
                )
                == str(match_id)
            ),
            None,
        )

        if not match:

            return jsonify({
                "success": False,
                "error":
                    "Match hin argamne.",
            }), 404

        events = []
        detail_error = None
        used_region = None

        for region in region_list():

            try:

                events = odds_request(
                    f"/sports/"
                    f"{match['sport_key']}"
                    f"/odds",
                    {
                        "regions":
                            region,
                        "markets":
                            DETAIL_MARKETS,
                        "oddsFormat":
                            "decimal",
                        "dateFormat":
                            "iso",
                    },
                )

                used_region = region

                if events:
                    break

            except Exception as e:

                detail_error = e

                print(
                    "[DETAIL ERROR]",
                    region,
                    repr(e),
                )

        event = next(
            (
                x
                for x in events
                if str(
                    x.get("id")
                )
                == str(match_id)
            ),
            None,
        )

        if not event:

            return jsonify({
                "success": True,
                "match": match,
                "markets": [],
                "best_bet":
                    match.get(
                        "best_bet"
                    ),
                "odds_error":
                    (
                        "Current odds "
                        "hin argamne."
                        if not detail_error
                        else str(
                            detail_error
                        )
                    ),
            }), 200

        converted = convert_event(
            event,
            {
                "key":
                    match[
                        "sport_key"
                    ],
                "title":
                    match[
                        "league"
                    ],
            },
        )

        markets = []

        # =================================================
        # 1X2
        # =================================================

        if converted["h2h"]:

            selections = []

            if converted[
                "h2h"
            ].get("home"):

                selections.append({
                    "value": "1",
                    "odd":
                        converted[
                            "h2h"
                        ]["home"],
                })

            if converted[
                "h2h"
            ].get("draw"):

                selections.append({
                    "value": "X",
                    "odd":
                        converted[
                            "h2h"
                        ]["draw"],
                })

            if converted[
                "h2h"
            ].get("away"):

                selections.append({
                    "value": "2",
                    "odd":
                        converted[
                            "h2h"
                        ]["away"],
                })

            if selections:

                markets.append({
                    "id": "h2h",
                    "name": "🎯 1X2",
                    "selections":
                        selections,
                })

        # =================================================
        # TOTALS
        # =================================================

        if converted["totals"]:

            selections = []

            if converted[
                "totals"
            ].get("over"):

                selections.append({
                    "value":
                        "Over 2.5",
                    "odd":
                        converted[
                            "totals"
                        ]["over"],
                })

            if converted[
                "totals"
            ].get("under"):

                selections.append({
                    "value":
                        "Under 2.5",
                    "odd":
                        converted[
                            "totals"
                        ]["under"],
                })

            if selections:

                markets.append({
                    "id": "totals",
                    "name":
                        "⚽ Over / Under",
                    "selections":
                        selections,
                })

        # =================================================
        # BTTS
        # =================================================

        if converted["btts"]:

            selections = []

            if converted[
                "btts"
            ].get("yes"):

                selections.append({
                    "value":
                        "BTTS Yes",
                    "odd":
                        converted[
                            "btts"
                        ]["yes"],
                })

            if converted[
                "btts"
            ].get("no"):

                selections.append({
                    "value":
                        "BTTS No",
                    "odd":
                        converted[
                            "btts"
                        ]["no"],
                })

            if selections:

                markets.append({
                    "id": "btts",
                    "name":
                        "🎯 Both Teams To Score",
                    "selections":
                        selections,
                })

        # =================================================
        # HANDICAP
        # =================================================

        if converted["spreads"]:

            selections = []

            for item in converted[
                "spreads"
            ]:

                point = item.get(
                    "point"
                )

                value = (
                    f"{item['name']} "
                    f"{point}"
                )

                selections.append({
                    "value": value,
                    "odd":
                        item["price"],
                })

            if selections:

                markets.append({
                    "id": "spreads",
                    "name":
                        "📊 Handicap",
                    "selections":
                        selections,
                })

        return jsonify({
            "success": True,
            "match": match,
            "markets": markets,
            "best_bet":
                converted.get(
                    "best_bet"
                ),
            "odds_region":
                used_region,
        }), 200

    except Exception as e:

        print(
            "[MATCH ERROR]",
            repr(e),
        )

        return jsonify({
            "success": False,
            "error": str(e),
            "markets": [],
        }), 500


# =========================================================
# LIVE
# =========================================================

@web_app.route(
    "/api/live",
    methods=["GET"],
)
def api_live():

    try:

        result = []

        sports = soccer_sports()

        for sport in sports[:20]:

            try:

                scores = odds_request(
                    f"/sports/"
                    f"{sport['key']}"
                    f"/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat":
                            "iso",
                    },
                )

            except Exception as e:

                print(
                    "[LIVE SKIP]",
                    sport.get("key"),
                    repr(e),
                )

                continue

            for event in scores:

                if event.get(
                    "completed"
                ):
                    continue

                score_map = {}

                for score in (
                    event.get(
                        "scores"
                    )
                    or []
                ):

                    score_map[
                        score.get(
                            "name"
                        )
                    ] = score.get(
                        "score"
                    )

                result.append({
                    "id":
                        event.get("id"),
                    "league":
                        sport.get(
                            "title"
                        ),
                    "home":
                        event.get(
                            "home_team"
                        ),
                    "away":
                        event.get(
                            "away_team"
                        ),
                    "home_score":
                        score_map.get(
                            event.get(
                                "home_team"
                            )
                        ),
                    "away_score":
                        score_map.get(
                            event.get(
                                "away_team"
                            )
                        ),
                    "minute": "",
                })

        return jsonify({
            "success": True,
            "count": len(result),
            "matches": result,
        }), 200

    except Exception as e:

        print(
            "[LIVE ERROR]",
            repr(e),
        )

        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
        }), 500


# =========================================================
# FLASK ERROR HANDLERS
# =========================================================

@web_app.errorhandler(404)
def handle_404(error):

    if request.path.startswith(
        "/api/"
    ):

        return jsonify({
            "success": False,
            "error":
                f"API endpoint not found: "
                f"{request.path}",
        }), 404

    return (
        "<h1>BEST BET</h1>"
        "<p>Page not found.</p>"
    ), 404


@web_app.errorhandler(500)
def handle_500(error):

    if request.path.startswith(
        "/api/"
    ):

        return jsonify({
            "success": False,
            "error":
                "Internal server error.",
        }), 500

    return (
        "<h1>BEST BET</h1>"
        "<p>Internal server error.</p>"
    ), 500


# =========================================================
# FLASK THREAD
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
            "BOT_TOKEN hin jiru."
        )

    if not ODDS_API_KEY:

        print(
            "WARNING: ODDS_API_KEY "
            "hin jiru."
        )

    threading.Thread(
        target=run_web,
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
            button_handler
        )
    )

    print("====================================")
    print("BEST BET BOT ONLINE")
    print(
        "WEB APP:",
        WEB_APP_URL,
    )
    print(
        "ODDS API:",
        bool(ODDS_API_KEY),
    )
    print(
        "LIST MARKETS:",
        LIST_MARKETS,
    )
    print(
        "DETAIL MARKETS:",
        DETAIL_MARKETS,
    )
    print(
        "DAYS:",
        DAYS_AHEAD,
    )
    print(
        "CACHE:",
        CACHE_SECONDS,
        "seconds",
    )
    print(
        "RANGE:",
        "TODAY + NEXT 6 DAYS",
    )
    print("====================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
