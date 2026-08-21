import os
import time
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# =========================================================
# ODDS API CONFIG
# =========================================================

# Main list:
# One region + one market = lowest quota usage.
PRIMARY_REGION = os.getenv("ODDS_REGION", "eu").strip().lower()

# If PRIMARY_REGION returns no events, these are tried.
FALLBACK_REGIONS = [
    x.strip().lower()
    for x in os.getenv(
        "ODDS_FALLBACK_REGIONS",
        "uk,us,au",
    ).split(",")
    if x.strip()
]

# Main list only needs 1X2.
LIST_MARKETS = "h2h"

# Details can load more markets after opening a match.
DETAIL_MARKETS = os.getenv(
    "DETAIL_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "7"))

# Maximum number of active soccer leagues queried.
MAX_SOCCER_SPORTS = int(
    os.getenv("MAX_SOCCER_SPORTS", "20")
)

API_TIMEOUT = int(
    os.getenv("API_TIMEOUT", "10")
)

CACHE_SECONDS = int(
    os.getenv("CACHE_SECONDS", "120")
)

MAX_WORKERS = int(
    os.getenv("MAX_WORKERS", "5")
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

USERS = {}

MATCH_CACHE = {
    "time": 0.0,
    "matches": [],
    "error": None,
}

API_STATS = {
    "remaining": None,
    "used": None,
    "last_cost": None,
    "last_status": None,
    "last_error": None,
    "last_request": None,
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
            odd = float(item.get("odd", 1))
            if odd > 1:
                total *= odd
        except Exception:
            pass

    return total


def betslip_text(user_id):
    slips = get_user(user_id)["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa selection filadhu."
        )

    total = total_odds(user_id)

    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(slips, 1):
        try:
            odd = float(item.get("odd", 0))
        except Exception:
            odd = 0

        text += (
            f"*{i}.* "
            f"{item.get('home', '')} vs "
            f"{item.get('away', '')}\n"
            f"🏆 {item.get('league', '')}\n"
            f"📊 {item.get('market', '')}\n"
            f"🎯 *{item.get('selection', '')}*\n"
            f"Odd: *{odd:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* {total:.2f}\n\n"
        "🧪 Demo/testing qofa."
    )

    return text


# =========================================================
# UTILITY
# =========================================================

def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def iso_now():
    return datetime.now(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def local_time_text(iso_time):
    if not iso_time:
        return ""

    try:
        dt = datetime.fromisoformat(
            iso_time.replace("Z", "+00:00")
        )

        local_dt = dt.astimezone(
            timezone(timedelta(hours=3))
        )

        return local_dt.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:
        return ""


def region_list():
    result = []

    if PRIMARY_REGION:
        result.append(PRIMARY_REGION)

    for region in FALLBACK_REGIONS:
        if region and region not in result:
            result.append(region)

    return result


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

    query = dict(params or {})
    query["apiKey"] = ODDS_API_KEY

    url = ODDS_BASE + path

    API_STATS["last_request"] = url

    try:
        response = requests.get(
            url,
            params=query,
            timeout=API_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "BEST-BET/3.0",
            },
        )
    except requests.RequestException as exc:
        API_STATS["last_error"] = str(exc)
        API_STATS["last_status"] = None
        raise RuntimeError(
            f"Odds API connection error: {exc}"
        ) from exc

    # Save quota information.
    API_STATS["remaining"] = response.headers.get(
        "x-requests-remaining"
    )

    API_STATS["used"] = response.headers.get(
        "x-requests-used"
    )

    API_STATS["last_cost"] = response.headers.get(
        "x-requests-last"
    )

    API_STATS["last_status"] = response.status_code

    if response.status_code != 200:

        try:
            body = response.json()
        except Exception:
            body = response.text[:500]

        if response.status_code == 401:
            message = (
                "ODDS_API_KEY sirrii miti ykn "
                "API key hin fudhatamne. "
                f"Details: {body}"
            )

        elif response.status_code == 429:
            message = (
                "Odds API quota xumurame ykn "
                "request baay'ate. "
                f"Details: {body}"
            )

        else:
            message = (
                f"Odds API HTTP {response.status_code}: "
                f"{body}"
            )

        API_STATS["last_error"] = message

        raise RuntimeError(message)

    try:
        data = response.json()
    except Exception as exc:
        message = "Odds API JSON sirrii hin deebifne."
        API_STATS["last_error"] = message
        raise RuntimeError(message) from exc

    API_STATS["last_error"] = None

    return data


# =========================================================
# SOCCER SPORTS
# =========================================================

def soccer_sports():
    """
    /sports endpoint quota hin nyaatu.
    Active soccer leagues qofa deebisa.
    """

    sports = odds_request("/sports")

    result = []

    for sport in sports:
        key = str(
            sport.get("key", "")
        ).strip()

        group = str(
            sport.get("group", "")
        ).lower()

        if (
            sport.get("active")
            and key.startswith("soccer_")
            and group in ("soccer", "football", "")
        ):
            result.append(sport)

    return result


# =========================================================
# PRIORITY LEAGUES
# =========================================================

PRIORITY_KEYS = [
    "soccer_epl",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",

    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",

    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_belgium_first_div",
    "soccer_turkey_super_league",

    "soccer_saudi_arabia_pro_league",
    "soccer_usa_mls",
    "soccer_brazil_serie_a",

    "soccer_argentina_primera_division",
    "soccer_mexico_ligamx",
    "soccer_australia_aleague",
]


def select_soccer_sports(sports):
    by_key = {
        str(x.get("key")): x
        for x in sports
    }

    selected = []

    # Priority first.
    for key in PRIORITY_KEYS:
        if key in by_key:
            selected.append(by_key[key])

        if len(selected) >= MAX_SOCCER_SPORTS:
            break

    # Fill remaining positions.
    if len(selected) < MAX_SOCCER_SPORTS:
        for sport in sports:
            if sport in selected:
                continue

            selected.append(sport)

            if len(selected) >= MAX_SOCCER_SPORTS:
                break

    return selected[:MAX_SOCCER_SPORTS]


# =========================================================
# MARKET PARSER
# =========================================================

def parse_event_markets(event):
    home_team = event.get(
        "home_team",
        "Home"
    )

    away_team = event.get(
        "away_team",
        "Away"
    )

    h2h = {}
    totals = {}
    btts = {}
    spreads = []

    # We keep the best price across available bookmakers.
    for bookmaker in event.get("bookmakers") or []:

        for market in bookmaker.get("markets") or []:

            market_key = str(
                market.get("key", "")
            ).lower()

            outcomes = (
                market.get("outcomes")
                or []
            )

            for outcome in outcomes:

                name = str(
                    outcome.get("name", "")
                ).strip()

                price = safe_float(
                    outcome.get("price")
                )

                if price is None or price <= 1:
                    continue

                # -------------------------
                # 1X2
                # -------------------------

                if market_key == "h2h":

                    if name == home_team:
                        h2h["home"] = max(
                            h2h.get("home", 0),
                            price,
                        )

                    elif name == away_team:
                        h2h["away"] = max(
                            h2h.get("away", 0),
                            price,
                        )

                    elif name.lower() == "draw":
                        h2h["draw"] = max(
                            h2h.get("draw", 0),
                            price,
                        )

                # -------------------------
                # TOTALS
                # -------------------------

                elif market_key == "totals":

                    point = safe_float(
                        outcome.get("point")
                    )

                    # Main O/U 2.5
                    if point != 2.5:
                        continue

                    low = name.lower()

                    if low == "over":
                        totals["over"] = max(
                            totals.get("over", 0),
                            price,
                        )

                    elif low == "under":
                        totals["under"] = max(
                            totals.get("under", 0),
                            price,
                        )

                # -------------------------
                # BTTS
                # -------------------------

                elif market_key in (
                    "btts",
                    "both_teams_to_score",
                ):

                    low = name.lower()

                    if low in (
                        "yes",
                        "btts yes",
                    ):
                        btts["yes"] = max(
                            btts.get("yes", 0),
                            price,
                        )

                    elif low in (
                        "no",
                        "btts no",
                    ):
                        btts["no"] = max(
                            btts.get("no", 0),
                            price,
                        )

                # -------------------------
                # SPREADS / HANDICAP
                # -------------------------

                elif market_key == "spreads":

                    point = outcome.get(
                        "point"
                    )

                    spreads.append({
                        "name": name,
                        "point": point,
                        "price": price,
                    })

    # Remove duplicate spreads.
    unique_spreads = []
    seen = set()

    for item in spreads:

        key = (
            item["name"],
            str(item["point"]),
        )

        if key in seen:
            continue

        seen.add(key)

        unique_spreads.append(item)

    unique_spreads.sort(
        key=lambda x: float(
            x["price"]
        ),
        reverse=True,
    )

    return {
        "h2h": h2h,
        "totals": totals,
        "btts": btts,
        "spreads": unique_spreads,
    }


# =========================================================
# BEST BET
# =========================================================

def calculate_best_bet(parsed):
    candidates = []

    h2h = parsed.get("h2h") or {}
    totals = parsed.get("totals") or {}
    btts = parsed.get("btts") or {}

    if h2h.get("home"):
        candidates.append({
            "selection": "1",
            "odd": float(h2h["home"]),
            "market": "1X2",
        })

    if h2h.get("draw"):
        candidates.append({
            "selection": "X",
            "odd": float(h2h["draw"]),
            "market": "1X2",
        })

    if h2h.get("away"):
        candidates.append({
            "selection": "2",
            "odd": float(h2h["away"]),
            "market": "1X2",
        })

    if totals.get("over"):
        candidates.append({
            "selection": "Over 2.5",
            "odd": float(totals["over"]),
            "market": "Over/Under",
        })

    if totals.get("under"):
        candidates.append({
            "selection": "Under 2.5",
            "odd": float(totals["under"]),
            "market": "Over/Under",
        })

    if btts.get("yes"):
        candidates.append({
            "selection": "BTTS Yes",
            "odd": float(btts["yes"]),
            "market": "BTTS",
        })

    if btts.get("no"):
        candidates.append({
            "selection": "BTTS No",
            "odd": float(btts["no"]),
            "market": "BTTS",
        })

    # Avoid impossible/extreme values.
    candidates = [
        item
        for item in candidates
        if (
            1.01
            < float(item["odd"])
            <= 20
        )
    ]

    if not candidates:
        return None

    # This is NOT a guaranteed prediction.
    # It simply selects the lowest valid available price.
    candidates.sort(
        key=lambda x: float(
            x["odd"]
        )
    )

    return candidates[0]


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(event, sport):
    parsed = parse_event_markets(event)

    commence = event.get(
        "commence_time",
        ""
    )

    return {
        "id": event.get("id"),

        "sport_key": sport.get(
            "key",
            event.get("sport_key", ""),
        ),

        "league": sport.get(
            "title",
            event.get(
                "sport_title",
                "Football",
            ),
        ),

        "home": event.get(
            "home_team",
            "Home",
        ),

        "away": event.get(
            "away_team",
            "Away",
        ),

        "time": local_time_text(
            commence
        ),

        "commence_time": commence,

        "h2h": parsed["h2h"],
        "totals": parsed["totals"],
        "btts": parsed["btts"],
        "spreads": parsed["spreads"],

        "best_bet": calculate_best_bet(
            parsed
        ),
    }


# =========================================================
# GET ONE SPORT ODDS
# =========================================================

def get_sport_odds(
    sport,
    start_text,
    end_text,
):
    sport_key = sport.get("key")

    if not sport_key:
        return [], None, None

    last_error = None

    # IMPORTANT:
    # We do NOT send all regions together.
    # This keeps quota low.
    for region in region_list():

        try:

            events = odds_request(
                f"/sports/{sport_key}/odds",
                {
                    "regions": region,

                    # MAIN LIST ONLY.
                    "markets": LIST_MARKETS,

                    "oddsFormat": "decimal",
                    "dateFormat": "iso",

                    "commenceTimeFrom": start_text,
                    "commenceTimeTo": end_text,
                },
            )

            if events:

                print(
                    "[ODDS OK]",
                    sport_key,
                    "region=",
                    region,
                    "events=",
                    len(events),
                )

                return (
                    events,
                    region,
                    None,
                )

            print(
                "[NO EVENTS]",
                sport_key,
                "region=",
                region,
            )

        except Exception as exc:

            last_error = str(exc)

            print(
                "[REGION ERROR]",
                sport_key,
                region,
                repr(exc),
            )

            # Do NOT try fallback for authentication/quota errors.
            text = str(exc)

            if (
                "401" in text
                or "429" in text
            ):
                break

    return (
        [],
        None,
        last_error,
    )


# =========================================================
# GET MATCHES
# =========================================================

def get_matches(force=False):

    now_ts = time.time()

    # -------------------------
    # CACHE
    # -------------------------

    if (
        not force
        and MATCH_CACHE["time"]
        and (
            now_ts
            - MATCH_CACHE["time"]
            < CACHE_SECONDS
        )
    ):
        return MATCH_CACHE["matches"]

    # -------------------------
    # SPORTS
    # -------------------------

    try:
        all_sports = soccer_sports()

    except Exception as exc:

        print(
            "[SPORTS ERROR]",
            repr(exc),
        )

        MATCH_CACHE["error"] = str(exc)

        if MATCH_CACHE["matches"]:
            return MATCH_CACHE["matches"]

        raise

    sports = select_soccer_sports(
        all_sports
    )

    # -------------------------
    # DATE RANGE
    # -------------------------

    now = datetime.now(timezone.utc)

    end_time = (
        now
        + timedelta(days=DAYS_AHEAD)
    )

    start_text = iso_z(now)
    end_text = iso_z(end_time)

    print(
        "===================================="
    )

    print(
        "[SOCCER SPORTS FOUND]",
        len(all_sports),
    )

    print(
        "[SOCCER SPORTS SELECTED]",
        len(sports),
    )

    print(
        "[REGIONS]",
        region_list(),
    )

    print(
        "[MARKETS]",
        LIST_MARKETS,
    )

    print(
        "[FROM]",
        start_text,
    )

    print(
        "[TO]",
        end_text,
    )

    print(
        "===================================="
    )

    result = []
    errors = []

    # -------------------------
    # PARALLEL REQUESTS
    # -------------------------

    workers = min(
        max(1, MAX_WORKERS),
        max(1, len(sports)),
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

        jobs = {}

        for sport in sports:

            key = sport.get("key")

            if not key:
                continue

            future = pool.submit(
                get_sport_odds,
                sport,
                start_text,
                end_text,
            )

            jobs[future] = sport

        for future in as_completed(jobs):

            sport = jobs[future]

            try:

                (
                    events,
                    used_region,
                    error,
                ) = future.result()

            except Exception as exc:

                print(
                    "[SPORT FUTURE ERROR]",
                    sport.get("key"),
                    repr(exc),
                )

                errors.append(
                    f"{sport.get('key')}: {exc}"
                )

                continue

            if error:

                errors.append(
                    f"{sport.get('key')}: {error}"
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

                    # Strict 7-day range.
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
                    ] = used_region or ""

                    # Main list needs at least 1X2.
                    if converted["h2h"]:
                        result.append(
                            converted
                        )

                except Exception as exc:

                    print(
                        "[EVENT ERROR]",
                        repr(exc),
                    )

    # -------------------------
    # REMOVE DUPLICATES
    # -------------------------

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

    # -------------------------
    # SORT BY KICKOFF
    # -------------------------

    result.sort(
        key=lambda x:
        x.get(
            "commence_time",
            "",
        )
    )

    # -------------------------
    # CACHE
    # -------------------------

    MATCH_CACHE["time"] = time.time()

    MATCH_CACHE["matches"] = result

    MATCH_CACHE["error"] = (
        errors[-10:]
        if errors
        else None
    )

    print(
        "[TOTAL MATCHES]",
        len(result),
    )

    if errors:
        print(
            "[ERRORS]",
            errors[-10:],
        )

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


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    u = get_user(
        user.id,
        user.first_name or "User",
    )

    if query.data == "profile":

        await query.edit_message_text(
            f"👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: *{u['balance']:.2f}*\n"
            f"Bet Slip: *{len(u['betslip'])}*",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )

    elif query.data == "balance":

        await query.edit_message_text(
            f"💳 *BALANCE*\n\n"
            f"Balance: *{u['balance']:.2f}*\n\n"
            "🧪 Demo system qofa.",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )

    elif query.data == "history":

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

    elif query.data == "how":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"

            "1. ⚽ Football bani\n"
            "2. 📅 Guyyaa filadhu\n"
            "3. ⚽ Match filadhu\n"
            "4. 📊 Market filadhu\n"
            "5. 🎯 Selection filadhu\n"
            "6. 🎟️ Bet Slip ilaali\n\n"

            "📅 Today irraa kaasee "
            "*guyyaa 7* agarsiisa.\n\n"

            "🧪 Demo/testing qofa.",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )


# =========================================================
# WEB ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify({

        "status": "online",

        "bot": "Best Bet",

        "api": "The Odds API",

        "api_key_configured":
            bool(ODDS_API_KEY),

        "web_app":
            WEB_APP_URL,

        "regions":
            region_list(),

        "list_markets":
            LIST_MARKETS,

        "detail_markets":
            DETAIL_MARKETS,

        "days":
            DAYS_AHEAD,

        "max_soccer_sports":
            MAX_SOCCER_SPORTS,

        "cache_seconds":
            CACHE_SECONDS,

        "api_timeout":
            API_TIMEOUT,

        "matches_cached":
            len(
                MATCH_CACHE["matches"]
            ),

        "api_remaining":
            API_STATS["remaining"],

        "api_used":
            API_STATS["used"],

        "api_last_cost":
            API_STATS["last_cost"],

        "api_last_status":
            API_STATS["last_status"],

        "api_last_error":
            API_STATS["last_error"],
    })


# =========================================================
# API TEST
# =========================================================

@app.route("/api/test", methods=["GET"])
def api_test():

    return jsonify({
        "success": True,
        "message":
            "BEST BET API is working.",
        "api_key_configured":
            bool(ODDS_API_KEY),
        "time":
            iso_z(
                datetime.now(
                    timezone.utc
                )
            ),
    })


# =========================================================
# ODDS API TEST
# =========================================================

@app.route("/api/odds-test", methods=["GET"])
def odds_test():

    try:

        sports = soccer_sports()

        return jsonify({

            "success": True,

            "message":
                "The Odds API connected.",

            "api_key_configured":
                bool(ODDS_API_KEY),

            "soccer_count":
                len(sports),

            "sample_sports": [
                {
                    "key": x.get("key"),
                    "title": x.get("title"),
                    "active": x.get("active"),
                }
                for x in sports[:20]
            ],

            "api_remaining":
                API_STATS["remaining"],

            "api_used":
                API_STATS["used"],

        })

    except Exception as exc:

        return jsonify({

            "success": False,

            "message":
                "The Odds API connection failed.",

            "error":
                str(exc),

            "api_key_configured":
                bool(ODDS_API_KEY),

            "api_remaining":
                API_STATS["remaining"],

            "api_used":
                API_STATS["used"],

        }), 502


# =========================================================
# MATCHES
# =========================================================

@app.route("/api/matches", methods=["GET"])
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

        if matches:

            return jsonify({

                "success": True,

                "count":
                    len(matches),

                "matches":
                    matches,

                "message":
                    "Football odds loaded.",

                "api_remaining":
                    API_STATS["remaining"],

                "api_used":
                    API_STATS["used"],

                "api_last_cost":
                    API_STATS["last_cost"],
            })

        return jsonify({

            "success": True,

            "count": 0,

            "matches": [],

            "message":
                "Football matches with "
                "1X2 odds were not found "
                "for the next 7 days.",

            "api_key_configured":
                bool(ODDS_API_KEY),

            "regions":
                region_list(),

            "api_remaining":
                API_STATS["remaining"],

            "api_used":
                API_STATS["used"],

            "api_error":
                MATCH_CACHE["error"],

        })

    except Exception as exc:

        print(
            "[API MATCHES ERROR]",
            repr(exc),
        )

        return jsonify({

            "success": False,

            "count": 0,

            "matches": [],

            "error":
                str(exc),

            "message":
                "Football odds loading failed.",

            "api_key_configured":
                bool(ODDS_API_KEY),

            "api_remaining":
                API_STATS["remaining"],

            "api_used":
                API_STATS["used"],

        }), 502


# =========================================================
# SINGLE MATCH DETAILS
# =========================================================

@app.route(
    "/api/match/<match_id>",
    methods=["GET"],
)
def api_match(match_id):

    try:

        # First find match from cached list.
        matches = get_matches()

        match = next(
            (
                item
                for item in matches
                if str(
                    item.get("id")
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

        sport_key = match.get(
            "sport_key"
        )

        if not sport_key:

            return jsonify({

                "success": False,

                "error":
                    "Sport key hin jiru.",

            }), 400

        events = []
        used_region = None
        last_error = None

        # -----------------------------------------
        # EVENT-SPECIFIC ODDS
        # -----------------------------------------

        for region in region_list():

            try:

                event_path = (
                    f"/sports/{sport_key}"
                    f"/events/{match_id}/odds"
                )

                events_data = odds_request(
                    event_path,
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

                # Event odds endpoint returns
                # one object, not necessarily a list.
                if isinstance(
                    events_data,
                    dict,
                ):

                    events = [
                        events_data
                    ]

                elif isinstance(
                    events_data,
                    list,
                ):

                    events = events_data

                else:

                    events = []

                if events:

                    used_region = region
                    break

            except Exception as exc:

                last_error = str(exc)

                print(
                    "[DETAIL ERROR]",
                    sport_key,
                    match_id,
                    region,
                    repr(exc),
                )

                text = str(exc)

                if (
                    "401" in text
                    or "429" in text
                ):
                    break

        # -----------------------------------------
        # FIND EVENT
        # -----------------------------------------

        event = next(
            (
                item
                for item in events
                if str(
                    item.get("id")
                )
                == str(match_id)
            ),
            None,
        )

        if not event:

            return jsonify({

                "success": True,

                "match":
                    match,

                "markets": [],

                "best_bet":
                    match.get(
                        "best_bet"
                    ),

                "odds_error":
                    (
                        last_error
                        or
                        "Current odds hin argamne."
                    ),

                "odds_region":
                    used_region,

            })

        # -----------------------------------------
        # PARSE
        # -----------------------------------------

        converted = convert_event(
            event,
            {
                "key":
                    sport_key,

                "title":
                    match.get(
                        "league",
                        "Football",
                    ),
            },
        )

        markets = []

        # -----------------------------------------
        # 1X2
        # -----------------------------------------

        h2h = (
            converted.get("h2h")
            or {}
        )

        if h2h:

            selections = []

            if h2h.get("home"):

                selections.append({
                    "value": "1",
                    "odd":
                        h2h["home"],
                })

            if h2h.get("draw"):

                selections.append({
                    "value": "X",
                    "odd":
                        h2h["draw"],
                })

            if h2h.get("away"):

                selections.append({
                    "value": "2",
                    "odd":
                        h2h["away"],
                })

            if selections:

                markets.append({

                    "id":
                        "h2h",

                    "name":
                        "🎯 1X2",

                    "selections":
                        selections,
                })

        # -----------------------------------------
        # TOTALS
        # -----------------------------------------

        totals = (
            converted.get("totals")
            or {}
        )

        if totals:

            selections = []

            if totals.get("over"):

                selections.append({

                    "value":
                        "Over 2.5",

                    "odd":
                        totals["over"],
                })

            if totals.get("under"):

                selections.append({

                    "value":
                        "Under 2.5",

                    "odd":
                        totals["under"],
                })

            if selections:

                markets.append({

                    "id":
                        "totals",

                    "name":
                        "⚽ Over / Under",

                    "selections":
                        selections,
                })

        # -----------------------------------------
        # BTTS
        # -----------------------------------------

        btts = (
            converted.get("btts")
            or {}
        )

        if btts:

            selections = []

            if btts.get("yes"):

                selections.append({

                    "value":
                        "BTTS Yes",

                    "odd":
                        btts["yes"],
                })

            if btts.get("no"):

                selections.append({

                    "value":
                        "BTTS No",

                    "odd":
                        btts["no"],
                })

            if selections:

                markets.append({

                    "id":
                        "btts",

                    "name":
                        "🎯 Both Teams To Score",

                    "selections":
                        selections,
                })

        # -----------------------------------------
        # HANDICAP
        # -----------------------------------------

        spreads = (
            converted.get("spreads")
            or []
        )

        if spreads:

            selections = []

            for item in spreads:

                point = item.get(
                    "point"
                )

                value = (
                    f"{item.get('name')} "
                    f"{point}"
                )

                selections.append({

                    "value":
                        value,

                    "odd":
                        item.get(
                            "price"
                        ),
                })

            if selections:

                markets.append({

                    "id":
                        "spreads",

                    "name":
                        "📊 Handicap",

                    "selections":
                        selections,
                })

        return jsonify({

            "success":
                True,

            "match":
                match,

            "markets":
                markets,

            "best_bet":
                converted.get(
                    "best_bet"
                ),

            "odds_region":
                used_region,

            "api_remaining":
                API_STATS["remaining"],

            "api_used":
                API_STATS["used"],

        })

    except Exception as exc:

        print(
            "[MATCH ERROR]",
            repr(exc),
        )

        return jsonify({

            "success":
                False,

            "error":
                str(exc),

            "markets": [],

        }), 502


# =========================================================
# LIVE
# =========================================================

@app.route("/api/live", methods=["GET"])
def api_live():

    try:

        result = []

        sports = select_soccer_sports(
            soccer_sports()
        )

        # Keep live requests limited.
        for sport in sports[:10]:

            sport_key = sport.get(
                "key"
            )

            if not sport_key:
                continue

            try:

                scores = odds_request(
                    f"/sports/{sport_key}/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat": "iso",
                    },
                )

            except Exception as exc:

                print(
                    "[LIVE SKIP]",
                    sport_key,
                    repr(exc),
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
                        score.get("name")
                    ] = score.get(
                        "score"
                    )

                result.append({

                    "id":
                        event.get(
                            "id"
                        ),

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

                    "minute":
                        "",

                })

        return jsonify({

            "success":
                True,

            "count":
                len(result),

            "matches":
                result,

        })

    except Exception as exc:

        return jsonify({

            "success":
                False,

            "error":
                str(exc),

            "matches": [],

        }), 502


# =========================================================
# 404
# =========================================================

@app.errorhandler(404)
def handle_404(error):

    if request.path.startswith(
        "/api/"
    ):

        return jsonify({

            "success":
                False,

            "error":
                (
                    "API endpoint not found: "
                    f"{request.path}"
                ),

        }), 404

    return (
        "<h1>BEST BET</h1>"
        "<p>Page not found.</p>"
    ), 404


# =========================================================
# 500
# =========================================================

@app.errorhandler(500)
def handle_500(error):

    if request.path.startswith(
        "/api/"
    ):

        return jsonify({

            "success":
                False,

            "error":
                "Internal server error.",

        }), 500

    return (
        "<h1>BEST BET</h1>"
        "<p>Internal server error.</p>"
    ), 500


# =========================================================
# TELEGRAM BOT
# =========================================================

def run_telegram_bot():

    if not BOT_TOKEN:

        print(
            "[BOT WARNING] "
            "BOT_TOKEN hin jiru."
        )

        return

    try:

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

        print(
            "===================================="
        )

        print(
            "BEST BET TELEGRAM BOT ONLINE"
        )

        print(
            "WEB APP:",
            WEB_APP_URL,
        )

        print(
            "===================================="
        )

        application.run_polling(
            allowed_updates=
                Update.ALL_TYPES
        )

    except Exception as exc:

        print(
            "[BOT ERROR]",
            repr(exc),
        )


# =========================================================
# START
# =========================================================

def main():

    print(
        "===================================="
    )

    print(
        "        BEST BET 3.0"
    )

    print(
        "===================================="
    )

    print(
        "WEB APP:",
        WEB_APP_URL,
    )

    print(
        "ODDS API KEY:",
        bool(ODDS_API_KEY),
    )

    print(
        "PRIMARY REGION:",
        PRIMARY_REGION,
    )

    print(
        "FALLBACK REGIONS:",
        FALLBACK_REGIONS,
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
        "MAX SPORTS:",
        MAX_SOCCER_SPORTS,
    )

    print(
        "CACHE:",
        CACHE_SECONDS,
    )

    print(
        "TIMEOUT:",
        API_TIMEOUT,
    )

    print(
        "PORT:",
        PORT,
    )

    print(
        "===================================="
    )

    # Start Telegram bot in background.
    if BOT_TOKEN:

        bot_thread = threading.Thread(
            target=run_telegram_bot,
            daemon=True,
        )

        bot_thread.start()

    # Flask web server.
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
