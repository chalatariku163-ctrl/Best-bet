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

PRIMARY_REGION = os.getenv("ODDS_REGION", "eu").strip().lower() or "eu"

# Fallback is intentionally limited to reduce quota usage.
FALLBACK_REGIONS = [
    x.strip().lower()
    for x in os.getenv("ODDS_FALLBACK_REGIONS", "uk").split(",")
    if x.strip()
]

LIST_MARKETS = "h2h"

DETAIL_MARKETS = os.getenv(
    "DETAIL_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

DAYS_AHEAD = max(1, int(os.getenv("DAYS_AHEAD", "7")))

# IMPORTANT:
# Keep this small. The old value 20 could make /api/matches
# take too long and cause Render's 502 proxy timeout.
MAX_SOCCER_SPORTS = max(
    1, int(os.getenv("MAX_SOCCER_SPORTS", "8"))
)

API_TIMEOUT = max(
    3, int(os.getenv("API_TIMEOUT", "6"))
)

CACHE_SECONDS = max(
    30, int(os.getenv("CACHE_SECONDS", "120"))
)

MAX_WORKERS = max(
    1, int(os.getenv("MAX_WORKERS", "8"))
)

# Background refresh prevents /api/matches from waiting for
# many Odds API requests.
INITIAL_WAIT_SECONDS = max(
    0, int(os.getenv("INITIAL_WAIT_SECONDS", "4"))
)

# Set to 1 only if you really need fallback regions.
ENABLE_FALLBACK = (
    os.getenv("ODDS_ENABLE_FALLBACK", "0").strip() == "1"
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

USERS = {}

CACHE_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
INITIAL_CACHE_EVENT = threading.Event()

MATCH_CACHE = {
    "time": 0.0,
    "matches": [],
    "error": None,
    "refreshing": False,
}

API_STATS = {
    "remaining": None,
    "used": None,
    "last_cost": None,
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
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
            f"*{i}.* {item.get('home', '')} vs "
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
        return local_dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def region_list():
    result = [PRIMARY_REGION] if PRIMARY_REGION else []

    if ENABLE_FALLBACK:
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
            "ODDS_API_KEY hin jiru. Render > Environment "
            "keessatti ODDS_API_KEY galchi."
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
                "User-Agent": "BEST-BET/3.1",
            },
        )
    except requests.RequestException as exc:
        API_STATS["last_error"] = str(exc)
        API_STATS["last_status"] = None
        raise RuntimeError(
            f"Odds API connection error: {exc}"
        ) from exc

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
            message = f"ODDS_API_KEY sirrii miti: {body}"
        elif response.status_code == 429:
            message = f"Odds API quota/request limit: {body}"
        elif response.status_code == 404:
            message = f"Odds API endpoint not found: {body}"
        else:
            message = (
                f"Odds API HTTP {response.status_code}: {body}"
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
    sports = odds_request("/sports")
    result = []

    for sport in sports:
        key = str(sport.get("key", "")).strip()
        group = str(sport.get("group", "")).lower()

        if (
            sport.get("active")
            and key.startswith("soccer_")
            and group in ("soccer", "football", "")
        ):
            result.append(sport)

    return result


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
    by_key = {str(x.get("key")): x for x in sports}
    selected = []

    for key in PRIORITY_KEYS:
        if key in by_key:
            selected.append(by_key[key])
        if len(selected) >= MAX_SOCCER_SPORTS:
            break

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
    home_team = event.get("home_team", "Home")
    away_team = event.get("away_team", "Away")

    h2h = {}
    totals = {}
    btts = {}
    spreads = []

    for bookmaker in event.get("bookmakers") or []:
        for market in bookmaker.get("markets") or []:
            market_key = str(
                market.get("key", "")
            ).lower()

            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name", "")).strip()
                price = safe_float(outcome.get("price"))

                if price is None or price <= 1:
                    continue

                if market_key == "h2h":
                    if name == home_team:
                        h2h["home"] = max(
                            h2h.get("home", 0), price
                        )
                    elif name == away_team:
                        h2h["away"] = max(
                            h2h.get("away", 0), price
                        )
                    elif name.lower() == "draw":
                        h2h["draw"] = max(
                            h2h.get("draw", 0), price
                        )

                elif market_key == "totals":
                    point = safe_float(outcome.get("point"))
                    if point != 2.5:
                        continue

                    low = name.lower()
                    if low == "over":
                        totals["over"] = max(
                            totals.get("over", 0), price
                        )
                    elif low == "under":
                        totals["under"] = max(
                            totals.get("under", 0), price
                        )

                elif market_key in (
                    "btts",
                    "both_teams_to_score",
                ):
                    low = name.lower()
                    if low in ("yes", "btts yes"):
                        btts["yes"] = max(
                            btts.get("yes", 0), price
                        )
                    elif low in ("no", "btts no"):
                        btts["no"] = max(
                            btts.get("no", 0), price
                        )

                elif market_key == "spreads":
                    spreads.append({
                        "name": name,
                        "point": outcome.get("point"),
                        "price": price,
                    })

    unique_spreads = []
    seen = set()

    for item in spreads:
        key = (item["name"], str(item["point"]))
        if key in seen:
            continue
        seen.add(key)
        unique_spreads.append(item)

    unique_spreads.sort(
        key=lambda x: float(x["price"]),
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

    candidates = [
        item for item in candidates
        if 1.01 < float(item["odd"]) <= 20
    ]

    if not candidates:
        return None

    # NOTE: lowest odds is only the shortest available price,
    # not a guaranteed prediction.
    candidates.sort(
        key=lambda x: float(x["odd"])
    )
    return candidates[0]


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(event, sport):
    parsed = parse_event_markets(event)
    commence = event.get("commence_time", "")

    return {
        "id": event.get("id"),
        "sport_key": sport.get(
            "key",
            event.get("sport_key", ""),
        ),
        "league": sport.get(
            "title",
            event.get("sport_title", "Football"),
        ),
        "home": event.get("home_team", "Home"),
        "away": event.get("away_team", "Away"),
        "time": local_time_text(commence),
        "commence_time": commence,
        "h2h": parsed["h2h"],
        "totals": parsed["totals"],
        "btts": parsed["btts"],
        "spreads": parsed["spreads"],
        "best_bet": calculate_best_bet(parsed),
    }


# =========================================================
# ONE SPORT ODDS
# =========================================================

def get_sport_odds(sport, start_text, end_text, region):
    sport_key = sport.get("key")

    if not sport_key:
        return [], None, "sport_key missing"

    try:
        events = odds_request(
            f"/sports/{sport_key}/odds",
            {
                "regions": region,
                "markets": LIST_MARKETS,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": start_text,
                "commenceTimeTo": end_text,
            },
        )

        print(
            "[ODDS]",
            sport_key,
            region,
            len(events or []),
        )

        return events or [], region, None

    except Exception as exc:
        print(
            "[ODDS ERROR]",
            sport_key,
            region,
            repr(exc),
        )
        return [], region, str(exc)


# =========================================================
# FETCH MATCHES - BACKGROUND SAFE
# =========================================================

def fetch_matches_from_api():
    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru."
        )

    all_sports = soccer_sports()
    sports = select_soccer_sports(all_sports)

    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD)

    start_text = iso_z(now)
    end_text = iso_z(end_time)

    print("====================================")
    print("[SOCCER FOUND]", len(all_sports))
    print("[SOCCER SELECTED]", len(sports))
    print("[REGION]", PRIMARY_REGION)
    print("[MARKETS]", LIST_MARKETS)
    print("[DAYS]", DAYS_AHEAD)
    print("[FROM]", start_text)
    print("[TO]", end_text)
    print("====================================")

    result = []
    errors = []

    # First request only one region. This is the major
    # change that prevents request explosion and 502s.
    region = PRIMARY_REGION

    workers = min(
        MAX_WORKERS,
        max(1, len(sports)),
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(
                get_sport_odds,
                sport,
                start_text,
                end_text,
                region,
            ): sport
            for sport in sports
        }

        for future in as_completed(jobs):
            sport = jobs[future]

            try:
                events, used_region, error = future.result()
            except Exception as exc:
                error = str(exc)
                events = []
                used_region = region

            if error:
                errors.append(
                    f"{sport.get('key')}: {error}"
                )

            for event in events:
                try:
                    commence = event.get("commence_time")
                    if not commence:
                        continue

                    dt = datetime.fromisoformat(
                        commence.replace("Z", "+00:00")
                    )

                    if dt < now or dt > end_time:
                        continue

                    converted = convert_event(event, sport)

                    if not converted["h2h"]:
                        continue

                    converted["odds_region"] = used_region or region
                    result.append(converted)

                except Exception as exc:
                    print(
                        "[EVENT ERROR]",
                        repr(exc),
                    )

    # Optional limited fallback: only if primary returned
    # absolutely nothing, and only one fallback region.
    if not result and ENABLE_FALLBACK:
        for fallback in FALLBACK_REGIONS:
            if fallback == PRIMARY_REGION:
                continue

            print("[FALLBACK REGION]", fallback)

            with ThreadPoolExecutor(
                max_workers=workers
            ) as pool:
                jobs = {
                    pool.submit(
                        get_sport_odds,
                        sport,
                        start_text,
                        end_text,
                        fallback,
                    ): sport
                    for sport in sports
                }

                for future in as_completed(jobs):
                    sport = jobs[future]

                    try:
                        events, used_region, error = future.result()
                    except Exception as exc:
                        events = []
                        used_region = fallback
                        error = str(exc)

                    if error:
                        errors.append(
                            f"{sport.get('key')}: {error}"
                        )

                    for event in events:
                        try:
                            commence = event.get("commence_time")
                            if not commence:
                                continue

                            dt = datetime.fromisoformat(
                                commence.replace("Z", "+00:00")
                            )

                            if dt < now or dt > end_time:
                                continue

                            converted = convert_event(
                                event, sport
                            )

                            if not converted["h2h"]:
                                continue

                            converted["odds_region"] = (
                                used_region or fallback
                            )
                            result.append(converted)

                        except Exception as exc:
                            print(
                                "[FALLBACK EVENT ERROR]",
                                repr(exc),
                            )

            if result:
                break

    unique = {}
    for match in result:
        match_id = str(match.get("id") or "")
        if match_id:
            unique[match_id] = match

    result = list(unique.values())

    result.sort(
        key=lambda x: x.get("commence_time", "")
    )

    return result, errors


def refresh_matches(force=False):
    # Only one refresh can run at a time.
    if not REFRESH_LOCK.acquire(blocking=False):
        return False

    try:
        with CACHE_LOCK:
            MATCH_CACHE["refreshing"] = True

        print("[MATCH REFRESH] started")

        try:
            matches, errors = fetch_matches_from_api()

            with CACHE_LOCK:
                # If new data is non-empty, replace cache.
                # If empty, preserve existing cache so a temporary
                # API problem does not destroy the UI.
                if matches:
                    MATCH_CACHE["matches"] = matches
                    MATCH_CACHE["time"] = time.time()

                MATCH_CACHE["error"] = (
                    errors[-10:] if errors else None
                )

                API_STATS["last_refresh"] = iso_z(
                    datetime.now(timezone.utc)
                )

                INITIAL_CACHE_EVENT.set()

            print(
                "[MATCH REFRESH] done:",
                len(matches),
            )

            return True

        except Exception as exc:
            print(
                "[MATCH REFRESH ERROR]",
                repr(exc),
            )

            with CACHE_LOCK:
                MATCH_CACHE["error"] = str(exc)
                INITIAL_CACHE_EVENT.set()

            return False

        finally:
            with CACHE_LOCK:
                MATCH_CACHE["refreshing"] = False

    finally:
        REFRESH_LOCK.release()


def start_refresh_background(force=False):
    with CACHE_LOCK:
        if MATCH_CACHE["refreshing"]:
            return False

        thread = threading.Thread(
            target=refresh_matches,
            args=(force,),
            daemon=True,
            name="odds-refresh",
        )
        thread.start()

    return True


def get_matches(force=False):
    now_ts = time.time()

    with CACHE_LOCK:
        cached_time = MATCH_CACHE["time"]
        cached_matches = list(MATCH_CACHE["matches"])
        refreshing = MATCH_CACHE["refreshing"]

    fresh = (
        cached_time > 0
        and (now_ts - cached_time) < CACHE_SECONDS
    )

    if not force and fresh:
        return cached_matches

    # Never make the HTTP request wait for all leagues.
    if not refreshing:
        start_refresh_background(force=force)

    with CACHE_LOCK:
        cached_matches = list(MATCH_CACHE["matches"])

    # If this is the first request, give the background fetch
    # a short chance to populate data. Never wait longer than
    # INITIAL_WAIT_SECONDS.
    if not cached_matches and INITIAL_WAIT_SECONDS > 0:
        INITIAL_CACHE_EVENT.wait(
            timeout=INITIAL_WAIT_SECONDS
        )

        with CACHE_LOCK:
            cached_matches = list(
                MATCH_CACHE["matches"]
            )

    return cached_matches


# =========================================================
# TELEGRAM MENU
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 PLAY BEST BET",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ],
        [
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                web_app=WebAppInfo(url=WEB_APP_URL),
            ),
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                web_app=WebAppInfo(url=WEB_APP_URL),
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


async def start(update, context):
    user = update.effective_user

    get_user(
        user.id,
        user.first_name or "User",
    )

    await update.message.reply_text(
        f"👋 Baga nagaan dhuftan *{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n"
        "⚽ Football\n"
        "📅 Today → Next 7 Days\n"
        "📊 Multiple Markets\n"
        "🎟️ Bet Slip\n\n"
        "👇 *⚽ FOOTBALL* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


async def button_handler(update, context):
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

    elif query.data == "how":
        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football bani\n"
            "2. 📅 Guyyaa filadhu\n"
            "3. ⚽ Match filadhu\n"
            "4. 📊 Market filadhu\n"
            "5. 🎯 Selection filadhu\n"
            "6. 🎟️ Bet Slip ilaali\n\n"
            "📅 Today irraa kaasee *guyyaa 7* agarsiisa.\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEB ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    with CACHE_LOCK:
        cache_time = MATCH_CACHE["time"]
        cache_count = len(MATCH_CACHE["matches"])
        refreshing = MATCH_CACHE["refreshing"]
        cache_error = MATCH_CACHE["error"]

    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "The Odds API",
        "api_key_configured": bool(ODDS_API_KEY),
        "web_app": WEB_APP_URL,
        "regions": region_list(),
        "list_markets": LIST_MARKETS,
        "detail_markets": DETAIL_MARKETS,
        "days": DAYS_AHEAD,
        "max_soccer_sports": MAX_SOCCER_SPORTS,
        "cache_seconds": CACHE_SECONDS,
        "api_timeout": API_TIMEOUT,
        "matches_cached": cache_count,
        "cache_age_seconds": (
            round(time.time() - cache_time, 1)
            if cache_time
            else None
        ),
        "refreshing": refreshing,
        "cache_error": cache_error,
        "api_remaining": API_STATS["remaining"],
        "api_used": API_STATS["used"],
        "api_last_cost": API_STATS["last_cost"],
        "api_last_status": API_STATS["last_status"],
        "api_last_error": API_STATS["last_error"],
        "last_refresh": API_STATS["last_refresh"],
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "BEST BET API is working.",
        "api_key_configured": bool(ODDS_API_KEY),
        "time": iso_z(datetime.now(timezone.utc)),
    })


@app.route("/api/odds-test", methods=["GET"])
def odds_test():
    try:
        sports = soccer_sports()

        return jsonify({
            "success": True,
            "message": "The Odds API connected.",
            "api_key_configured": bool(ODDS_API_KEY),
            "soccer_count": len(sports),
            "sample_sports": [
                {
                    "key": x.get("key"),
                    "title": x.get("title"),
                    "active": x.get("active"),
                }
                for x in sports[:20]
            ],
            "api_remaining": API_STATS["remaining"],
            "api_used": API_STATS["used"],
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "message": "The Odds API connection failed.",
            "error": str(exc),
            "api_key_configured": bool(ODDS_API_KEY),
            "api_remaining": API_STATS["remaining"],
            "api_used": API_STATS["used"],
        }), 200


@app.route("/api/matches", methods=["GET"])
def api_matches():
    force = request.args.get("refresh", "0") == "1"

    try:
        matches = get_matches(force=force)

        with CACHE_LOCK:
            refreshing = MATCH_CACHE["refreshing"]
            cache_error = MATCH_CACHE["error"]
            cache_time = MATCH_CACHE["time"]

        # IMPORTANT:
        # Always return HTTP 200 JSON to the WebApp.
        # This prevents Render/HTML error pages from being
        # parsed as JSON by index.html.
        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football odds loaded."
                if matches
                else "Matches are loading. Please refresh shortly."
            ),
            "loading": refreshing and not bool(matches),
            "stale": (
                bool(matches)
                and cache_time > 0
                and (time.time() - cache_time) >= CACHE_SECONDS
            ),
            "api_key_configured": bool(ODDS_API_KEY),
            "regions": region_list(),
            "api_remaining": API_STATS["remaining"],
            "api_used": API_STATS["used"],
            "api_last_cost": API_STATS["last_cost"],
            "api_last_status": API_STATS["last_status"],
            "api_error": cache_error,
        }), 200

    except Exception as exc:
        print("[API MATCHES ERROR]", repr(exc))

        # Never expose a 502 HTML page to the WebApp.
        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(exc),
            "message": "Football odds loading failed.",
            "api_key_configured": bool(ODDS_API_KEY),
            "api_remaining": API_STATS["remaining"],
            "api_used": API_STATS["used"],
        }), 200


# =========================================================
# SINGLE MATCH DETAILS
# =========================================================

@app.route("/api/match/<match_id>", methods=["GET"])
def api_match(match_id):
    try:
        matches = get_matches()

        match = next(
            (
                item for item in matches
                if str(item.get("id")) == str(match_id)
            ),
            None,
        )

        if not match:
            return jsonify({
                "success": False,
                "error": "Match hin argamne.",
                "markets": [],
            }), 200

        sport_key = match.get("sport_key")

        if not sport_key:
            return jsonify({
                "success": False,
                "error": "Sport key hin jiru.",
                "markets": [],
            }), 200

        event = None
        used_region = None
        last_error = None

        # Details are fetched only when the user opens a match.
        detail_regions = [PRIMARY_REGION]
        if ENABLE_FALLBACK:
            detail_regions += [
                x for x in FALLBACK_REGIONS
                if x not in detail_regions
            ]

        for region in detail_regions:
            try:
                event_path = (
                    f"/sports/{sport_key}"
                    f"/events/{match_id}/odds"
                )

                data = odds_request(
                    event_path,
                    {
                        "regions": region,
                        "markets": DETAIL_MARKETS,
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                )

                if isinstance(data, dict):
                    event = data
                elif isinstance(data, list):
                    event = next(
                        (
                            x for x in data
                            if str(x.get("id")) == str(match_id)
                        ),
                        None,
                    )

                if event:
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

                if "401" in last_error or "429" in last_error:
                    break

        if not event:
            return jsonify({
                "success": True,
                "match": match,
                "markets": [],
                "best_bet": match.get("best_bet"),
                "odds_error": (
                    last_error
                    or "Current odds hin argamne."
                ),
                "odds_region": used_region,
            }), 200

        converted = convert_event(
            event,
            {
                "key": sport_key,
                "title": match.get("league", "Football"),
            },
        )

        markets = []

        h2h = converted.get("h2h") or {}
        if h2h:
            selections = []

            if h2h.get("home"):
                selections.append({
                    "value": "1",
                    "odd": h2h["home"],
                })

            if h2h.get("draw"):
                selections.append({
                    "value": "X",
                    "odd": h2h["draw"],
                })

            if h2h.get("away"):
                selections.append({
                    "value": "2",
                    "odd": h2h["away"],
                })

            if selections:
                markets.append({
                    "id": "h2h",
                    "name": "🎯 1X2",
                    "selections": selections,
                })

        totals = converted.get("totals") or {}
        if totals:
            selections = []

            if totals.get("over"):
                selections.append({
                    "value": "Over 2.5",
                    "odd": totals["over"],
                })

            if totals.get("under"):
                selections.append({
                    "value": "Under 2.5",
                    "odd": totals["under"],
                })

            if selections:
                markets.append({
                    "id": "totals",
                    "name": "⚽ Over / Under",
                    "selections": selections,
                })

        btts = converted.get("btts") or {}
        if btts:
            selections = []

            if btts.get("yes"):
                selections.append({
                    "value": "BTTS Yes",
                    "odd": btts["yes"],
                })

            if btts.get("no"):
                selections.append({
                    "value": "BTTS No",
                    "odd": btts["no"],
                })

            if selections:
                markets.append({
                    "id": "btts",
                    "name": "🎯 Both Teams To Score",
                    "selections": selections,
                })

        spreads = converted.get("spreads") or []
        if spreads:
            selections = []

            for item in spreads:
                selections.append({
                    "value": (
                        f"{item.get('name')} "
                        f"{item.get('point')}"
                    ),
                    "odd": item.get("price"),
                })

            if selections:
                markets.append({
                    "id": "spreads",
                    "name": "📊 Handicap",
                    "selections": selections,
                })

        return jsonify({
            "success": True,
            "match": match,
            "markets": markets,
            "best_bet": converted.get("best_bet"),
            "odds_region": used_region,
            "api_remaining": API_STATS["remaining"],
            "api_used": API_STATS["used"],
        }), 200

    except Exception as exc:
        print("[MATCH ERROR]", repr(exc))

        return jsonify({
            "success": False,
            "error": str(exc),
            "markets": [],
        }), 200


# =========================================================
# LIVE
# =========================================================

@app.route("/api/live", methods=["GET"])
def api_live():
    try:
        result = []
        sports = select_soccer_sports(soccer_sports())

        # Limit live requests to reduce quota.
        for sport in sports[:5]:
            sport_key = sport.get("key")
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

            for event in scores or []:
                if event.get("completed"):
                    continue

                score_map = {}

                for score in event.get("scores") or []:
                    score_map[score.get("name")] = score.get("score")

                result.append({
                    "id": event.get("id"),
                    "league": sport.get("title"),
                    "home": event.get("home_team"),
                    "away": event.get("away_team"),
                    "home_score": score_map.get(
                        event.get("home_team")
                    ),
                    "away_score": score_map.get(
                        event.get("away_team")
                    ),
                    "minute": "",
                })

        return jsonify({
            "success": True,
            "count": len(result),
            "matches": result,
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "matches": [],
        }), 200


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def handle_404(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": f"API endpoint not found: {request.path}",
        }), 404

    return (
        "<h1>BEST BET</h1>"
        "<p>Page not found.</p>"
    ), 404


@app.errorhandler(500)
def handle_500(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": "Internal server error.",
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
        print("[BOT WARNING] BOT_TOKEN hin jiru.")
        return

    try:
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .build()
        )

        application.add_handler(
            CommandHandler("start", start)
        )

        application.add_handler(
            CallbackQueryHandler(button_handler)
        )

        print("====================================")
        print("BEST BET TELEGRAM BOT ONLINE")
        print("WEB APP:", WEB_APP_URL)
        print("====================================")

        # run_polling is synchronous in python-telegram-bot 22.x.
        # It owns its event loop; do not await start_polling here.
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )

    except Exception as exc:
        print("[BOT ERROR]", repr(exc))


# =========================================================
# BACKGROUND CACHE REFRESH
# =========================================================

def cache_refresh_loop():
    # Wait briefly for Flask to become available.
    time.sleep(3)

    while True:
        try:
            with CACHE_LOCK:
                cache_time = MATCH_CACHE["time"]
                refreshing = MATCH_CACHE["refreshing"]

            expired = (
                cache_time == 0
                or (time.time() - cache_time) >= CACHE_SECONDS
            )

            if expired and not refreshing:
                start_refresh_background()

        except Exception as exc:
            print(
                "[CACHE LOOP ERROR]",
                repr(exc),
            )

        # Check every 20 seconds, but only refresh when expired.
        time.sleep(20)


# =========================================================
# START
# =========================================================

def main():
    print("====================================")
    print("        BEST BET 3.1")
    print("====================================")
    print("WEB APP:", WEB_APP_URL)
    print("ODDS API KEY:", bool(ODDS_API_KEY))
    print("PRIMARY REGION:", PRIMARY_REGION)
    print("FALLBACK REGIONS:", FALLBACK_REGIONS)
    print("FALLBACK ENABLED:", ENABLE_FALLBACK)
    print("LIST MARKETS:", LIST_MARKETS)
    print("DETAIL MARKETS:", DETAIL_MARKETS)
    print("DAYS:", DAYS_AHEAD)
    print("MAX SPORTS:", MAX_SOCCER_SPORTS)
    print("CACHE:", CACHE_SECONDS)
    print("TIMEOUT:", API_TIMEOUT)
    print("MAX WORKERS:", MAX_WORKERS)
    print("INITIAL WAIT:", INITIAL_WAIT_SECONDS)
    print("PORT:", PORT)
    print("====================================")

    # Start background Odds cache before/alongside the web app.
    cache_thread = threading.Thread(
        target=cache_refresh_loop,
        daemon=True,
        name="cache-refresh-loop",
    )
    cache_thread.start()

    # Telegram bot.
    if BOT_TOKEN:
        bot_thread = threading.Thread(
            target=run_telegram_bot,
            daemon=True,
            name="telegram-bot",
        )
        bot_thread.start()

    # Flask.
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
