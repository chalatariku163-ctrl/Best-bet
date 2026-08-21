import os
import time
import threading
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

# SportMonks token. This is the ONLY football/odds provider.
SPORTMONKS_API_TOKEN = os.getenv(
    "SPORTMONKS_API_TOKEN",
    os.getenv("SPORTMONKS_API_KEY", "")
).strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com"
).strip().rstrip("/")

PORT = int(os.getenv("PORT", "10000"))
SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"

DAYS_AHEAD = max(1, int(os.getenv("DAYS_AHEAD", "7")))
API_TIMEOUT = max(5, int(os.getenv("API_TIMEOUT", "20")))
CACHE_SECONDS = max(30, int(os.getenv("CACHE_SECONDS", "60")))
INITIAL_WAIT_SECONDS = max(0, int(os.getenv("INITIAL_WAIT_SECONDS", "15")))
MAX_FIXTURE_PAGES = max(1, int(os.getenv("MAX_FIXTURE_PAGES", "10")))
DETAIL_REFRESH_ODDS = os.getenv("DETAIL_REFRESH_ODDS", "1").strip() == "1"

SPORTMONKS_BOOKMAKERS = [
    x.strip() for x in os.getenv("SPORTMONKS_BOOKMAKERS", "").split(",")
    if x.strip()
]
SPORTMONKS_MARKETS = [
    x.strip() for x in os.getenv("SPORTMONKS_MARKETS", "").split(",")
    if x.strip()
]

ETHIOPIA_TZ = timezone(timedelta(hours=3))

# =========================================================
# FLASK / DATA
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
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
    "last_response_time": None,
}

# =========================================================
# USER / BETSLIP
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
            f"*{i}.* {item.get('home', '')} vs {item.get('away', '')}\n"
            f"🏆 {item.get('league', '')}\n"
            f"📊 {item.get('market', '')}\n"
            f"🎯 *{item.get('selection', '')}*\n"
            f"Odd: *{odd:.2f}*\n\n"
        )
    return text + (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* {total:.2f}\n\n"
        "🧪 Demo/testing qofa."
    )

# =========================================================
# HELPERS
# =========================================================

def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_time_text(value):
    dt = parse_iso_datetime(value)
    if not dt:
        return ""
    return dt.astimezone(ETHIOPIA_TZ).strftime("%d/%m/%Y %H:%M")


def date_text_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            return data
    return []


def participant_name(p):
    if not isinstance(p, dict):
        return ""
    return p.get("name") or p.get("short_code") or p.get("short_name") or ""


def extract_teams(fixture):
    participants = fixture.get("participants") or fixture.get("teams") or []
    if isinstance(participants, dict):
        participants = as_list(participants)

    home = away = ""
    for p in participants:
        meta = p.get("meta") or {}
        loc = str(meta.get("location", "")).lower()
        name = participant_name(p)
        if loc == "home":
            home = name
        elif loc == "away":
            away = name

    if not home and participants:
        home = participant_name(participants[0])
    if not away and len(participants) > 1:
        away = participant_name(participants[1])

    return (
        home or fixture.get("home_team") or fixture.get("localteam_name") or "Home",
        away or fixture.get("away_team") or fixture.get("visitorteam_name") or "Away",
    )


def extract_league(fixture):
    league = fixture.get("league")
    if isinstance(league, dict):
        return league.get("name") or league.get("title") or "Football"
    return fixture.get("league_name") or "Football"

# =========================================================
# SPORTMONKS REQUEST
# =========================================================

def sportmonks_request(path, params=None):
    if not SPORTMONKS_API_TOKEN:
        raise RuntimeError(
            "SPORTMONKS_API_TOKEN hin jiru. "
            "Render > Environment Variables keessatti token galchi."
        )

    query = dict(params or {})
    query["api_token"] = SPORTMONKS_API_TOKEN
    url = SPORTMONKS_BASE + path
    API_STATS["last_request"] = url
    started = time.time()

    try:
        response = requests.get(
            url,
            params=query,
            timeout=API_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "BEST-BET/6.0",
            },
        )
    except requests.RequestException as exc:
        API_STATS["last_status"] = None
        API_STATS["last_error"] = str(exc)
        raise RuntimeError(f"SportMonks connection error: {exc}") from exc
    finally:
        API_STATS["last_response_time"] = round(time.time() - started, 3)

    API_STATS["last_status"] = response.status_code

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:3000]}

    if response.status_code != 200:
        message = f"SportMonks HTTP {response.status_code}: {body}"
        API_STATS["last_error"] = message
        raise RuntimeError(message)

    if isinstance(body, dict):
        if body.get("error"):
            message = str(body.get("error"))
            API_STATS["last_error"] = message
            raise RuntimeError(message)
        if body.get("message") and body.get("data") is None:
            message = str(body.get("message"))
            API_STATS["last_error"] = message
            raise RuntimeError(message)

    API_STATS["last_error"] = None
    return body

# =========================================================
# ODDS
# =========================================================

def extract_odds_from_fixture(fixture):
    raw = fixture.get("odds")
    odds = as_list(raw)
    if not odds and isinstance(raw, dict):
        odds = raw.get("data") or []
    return [x for x in odds if isinstance(x, dict)]


def normalize_odd(odd):
    value = safe_float(odd.get("value"))
    if value is None or value <= 1:
        return None

    market = odd.get("market") or {}
    bookmaker = odd.get("bookmaker") or {}

    market_id = odd.get("market_id") or market.get("id")
    market_name = (
        market.get("name")
        or odd.get("market_description")
        or odd.get("market_name")
        or ""
    )
    bookmaker_id = odd.get("bookmaker_id") or bookmaker.get("id")
    bookmaker_name = bookmaker.get("name") or odd.get("bookmaker_name") or ""
    label = str(odd.get("label") or odd.get("name") or "").strip()

    if SPORTMONKS_BOOKMAKERS and str(bookmaker_id) not in SPORTMONKS_BOOKMAKERS:
        return None
    if SPORTMONKS_MARKETS and str(market_id) not in SPORTMONKS_MARKETS:
        return None

    return {
        "id": odd.get("id"),
        "fixture_id": odd.get("fixture_id"),
        "market_id": market_id,
        "market": market_name,
        "market_description": odd.get("market_description") or market.get("description") or "",
        "bookmaker_id": bookmaker_id,
        "bookmaker": bookmaker_name,
        "label": label,
        "name": str(odd.get("name") or label),
        "odd": value,
        "value": value,
        "probability": safe_float(odd.get("probability")),
        "last_update": odd.get("last_update") or odd.get("updated_at"),
    }


def normalized_odds(fixture):
    result = []
    for raw in extract_odds_from_fixture(fixture):
        item = normalize_odd(raw)
        if item:
            result.append(item)
    return result


def market_key(odd):
    text = (
        str(odd.get("market") or "")
        + " "
        + str(odd.get("market_description") or "")
    ).lower()

    if "both teams" in text or "btts" in text or "both_to_score" in text:
        return "btts"
    if "over/under" in text or "total" in text or "goals" in text:
        return "totals"
    if "handicap" in text or "asian handicap" in text or "spread" in text:
        return "spreads"
    if (
        "match winner" in text
        or "1x2" in text
        or "fulltime result" in text
        or text.strip() in ("match winner", "1x2", "fulltime result")
    ):
        return "h2h"
    return "other"


def choose_best_h2h(odds, home, away):
    result = {}
    for odd in odds:
        if market_key(odd) != "h2h":
            continue
        label = str(odd.get("label") or odd.get("name") or "").strip()
        low = label.lower()
        if label == home or low in ("home", "1"):
            key = "home"
        elif label == away or low in ("away", "2"):
            key = "away"
        elif low in ("draw", "x"):
            key = "draw"
        else:
            continue
        if key not in result or odd["odd"] > result[key]["odd"]:
            result[key] = odd
    return result


def choose_main_totals(odds):
    result = {}
    for odd in odds:
        if market_key(odd) != "totals":
            continue
        label = str(odd.get("label") or odd.get("name") or "").lower()
        key = "over" if "over" in label else "under" if "under" in label else None
        if key and (key not in result or odd["odd"] > result[key]["odd"]):
            result[key] = odd
    return result


def choose_btts(odds):
    result = {}
    for odd in odds:
        if market_key(odd) != "btts":
            continue
        label = str(odd.get("label") or odd.get("name") or "").lower()
        key = "yes" if label in ("yes", "btts yes") else "no" if label in ("no", "btts no") else None
        if key and (key not in result or odd["odd"] > result[key]["odd"]):
            result[key] = odd
    return result


def calculate_best_bet(odds, home, away):
    candidates = []
    h2h = choose_best_h2h(odds, home, away)

    for key, selection in (
        ("home", "1"), ("draw", "X"), ("away", "2")
    ):
        if h2h.get(key):
            x = h2h[key]
            candidates.append({
                "selection": selection,
                "odd": x["odd"],
                "market": "1X2",
                "bookmaker": x.get("bookmaker"),
                "bookmaker_id": x.get("bookmaker_id"),
                "market_id": x.get("market_id"),
            })

    totals = choose_main_totals(odds)
    for key, selection in (("over", "Over"), ("under", "Under")):
        if totals.get(key):
            x = totals[key]
            candidates.append({
                "selection": selection,
                "odd": x["odd"],
                "market": "Over/Under",
                "bookmaker": x.get("bookmaker"),
                "bookmaker_id": x.get("bookmaker_id"),
                "market_id": x.get("market_id"),
            })

    btts = choose_btts(odds)
    for key, selection in (("yes", "BTTS Yes"), ("no", "BTTS No")):
        if btts.get(key):
            x = btts[key]
            candidates.append({
                "selection": selection,
                "odd": x["odd"],
                "market": "BTTS",
                "bookmaker": x.get("bookmaker"),
                "bookmaker_id": x.get("bookmaker_id"),
                "market_id": x.get("market_id"),
            })

    candidates = [
        x for x in candidates
        if safe_float(x.get("odd")) and 1.01 < float(x["odd"]) <= 100
    ]
    return min(candidates, key=lambda x: float(x["odd"])) if candidates else None

# =========================================================
# FIXTURE CONVERSION
# =========================================================

def convert_fixture(fixture):
    fixture_id = fixture.get("id")
    home, away = extract_teams(fixture)
    league = extract_league(fixture)

    commence = fixture.get("starting_at") or fixture.get("commence_time")
    if isinstance(commence, (int, float)):
        commence = datetime.fromtimestamp(commence, timezone.utc).isoformat()

    odds = normalized_odds(fixture)
    h2h = choose_best_h2h(odds, home, away)
    totals = choose_main_totals(odds)
    btts = choose_btts(odds)

    return {
        "id": fixture_id,
        "fixture_id": fixture_id,
        "sport_key": "football",
        "league": league,
        "home": home,
        "away": away,
        "time": local_time_text(commence),
        "commence_time": commence,
        "h2h": {
            "home": h2h["home"]["odd"] if h2h.get("home") else None,
            "draw": h2h["draw"]["odd"] if h2h.get("draw") else None,
            "away": h2h["away"]["odd"] if h2h.get("away") else None,
        },
        "totals": {
            "over": totals["over"]["odd"] if totals.get("over") else None,
            "under": totals["under"]["odd"] if totals.get("under") else None,
        },
        "btts": {
            "yes": btts["yes"]["odd"] if btts.get("yes") else None,
            "no": btts["no"]["odd"] if btts.get("no") else None,
        },
        "spreads": [
            {
                "name": o.get("label"),
                "point": o.get("label"),
                "price": o.get("odd"),
                "market_id": o.get("market_id"),
                "bookmaker": o.get("bookmaker"),
            }
            for o in odds if market_key(o) == "spreads"
        ],
        "odds": odds,
        "odds_count": len(odds),
        "best_bet": calculate_best_bet(odds, home, away),
        "state": fixture.get("state"),
    }


def fetch_fixture_page(start_date, end_date, page=1):
    params = {
        "include": "participants;league;state;odds",
        "page": page,
    }
    if SPORTMONKS_BOOKMAKERS:
        params["bookmakers"] = ",".join(SPORTMONKS_BOOKMAKERS)
    if SPORTMONKS_MARKETS:
        params["markets"] = ",".join(SPORTMONKS_MARKETS)

    return sportmonks_request(
        f"/fixtures/between/{start_date}/{end_date}",
        params,
    )


def fetch_matches_from_api():
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD)
    start_date = date_text_utc(now)
    end_date = date_text_utc(end_time)

    print("[SPORTMONKS]", start_date, "->", end_date)

    fixtures = []
    page = 1

    while page <= MAX_FIXTURE_PAGES:
        body = fetch_fixture_page(start_date, end_date, page)
        data = as_list(body)
        if not data:
            break
        fixtures.extend(data)

        pagination = body.get("pagination") if isinstance(body, dict) else None
        if not isinstance(pagination, dict):
            break
        if pagination.get("has_more") is False:
            break

        next_page = pagination.get("next_page")
        page = int(next_page) if next_page else page + 1

    result = []
    for fixture in fixtures:
        try:
            commence = fixture.get("starting_at") or fixture.get("commence_time")
            dt = parse_iso_datetime(commence)

            if not dt and fixture.get("starting_at_timestamp"):
                dt = datetime.fromtimestamp(
                    float(fixture["starting_at_timestamp"]),
                    timezone.utc
                )

            if not dt or dt < now or dt > end_time:
                continue

            match = convert_fixture(fixture)

            # IMPORTANT:
            # Do not hide real fixtures just because the fixture response
            # has no odds. The frontend can still display the match and
            # the detail endpoint can try to load odds separately.
            result.append(match)

        except Exception as exc:
            print("[FIXTURE ERROR]", repr(exc))

    unique = {}
    for match in result:
        if match.get("id") is not None:
            unique[str(match["id"])] = match

    result = list(unique.values())
    result.sort(key=lambda x: x.get("commence_time") or "")

    print("[TOTAL FIXTURES]", len(result))
    print("[TOTAL ODDS]", sum(x.get("odds_count", 0) for x in result))

    return result, []


# =========================================================
# CACHE
# =========================================================

def refresh_matches(force=False):
    if not REFRESH_LOCK.acquire(blocking=False):
        return False

    try:
        with CACHE_LOCK:
            MATCH_CACHE["refreshing"] = True

        try:
            matches, errors = fetch_matches_from_api()
            with CACHE_LOCK:
                MATCH_CACHE["matches"] = matches
                MATCH_CACHE["time"] = time.time()
                MATCH_CACHE["error"] = errors[-10:] if errors else None
                MATCH_CACHE["refreshing"] = False
                API_STATS["last_refresh"] = iso_z(datetime.now(timezone.utc))
                INITIAL_CACHE_EVENT.set()
            return True

        except Exception as exc:
            print("[MATCH REFRESH ERROR]", repr(exc))
            with CACHE_LOCK:
                MATCH_CACHE["error"] = str(exc)
                MATCH_CACHE["refreshing"] = False
                INITIAL_CACHE_EVENT.set()
            return False
    finally:
        REFRESH_LOCK.release()


def start_refresh_background(force=False):
    with CACHE_LOCK:
        if MATCH_CACHE["refreshing"]:
            return False
        threading.Thread(
            target=refresh_matches,
            args=(force,),
            daemon=True,
            name="sportmonks-refresh",
        ).start()
    return True


def get_matches(force=False):
    now_ts = time.time()
    with CACHE_LOCK:
        cache_time = MATCH_CACHE["time"]
        matches = list(MATCH_CACHE["matches"])
        refreshing = MATCH_CACHE["refreshing"]

    fresh = cache_time > 0 and now_ts - cache_time < CACHE_SECONDS

    if not force and fresh:
        return matches

    if not refreshing:
        start_refresh_background(force)

    if not matches and INITIAL_WAIT_SECONDS > 0:
        INITIAL_CACHE_EVENT.wait(INITIAL_WAIT_SECONDS)
        with CACHE_LOCK:
            matches = list(MATCH_CACHE["matches"])

    return matches

# =========================================================
# TELEGRAM
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🎮 PLAY BEST BET",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )],
        [InlineKeyboardButton(
            "⚽ FOOTBALL",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )],
        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                web_app=WebAppInfo(url=WEB_APP_URL)
            ),
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                web_app=WebAppInfo(url=WEB_APP_URL)
            ),
        ],
        [
            InlineKeyboardButton("👤 PROFILE", callback_data="profile"),
            InlineKeyboardButton("💳 BALANCE", callback_data="balance"),
        ],
        [
            InlineKeyboardButton("📜 HISTORY", callback_data="history"),
            InlineKeyboardButton("ℹ️ HOW TO PLAY", callback_data="how"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.first_name or "User")

    await update.message.reply_text(
        f"👋 Baga nagaan dhuftan *{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n"
        "⚽ Football\n"
        "📅 Today → Next 7 Days\n"
        "📊 SportMonks Odds\n"
        "🎟️ Bet Slip\n\n"
        "👇 *⚽ FOOTBALL* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    u = get_user(user.id, user.first_name or "User")

    if query.data == "profile":
        text = (
            f"👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: *{u['balance']:.2f}*\n"
            f"Bet Slip: *{len(u['betslip'])}*"
        )
    elif query.data == "balance":
        text = f"💳 *BALANCE*\n\nBalance: *{u['balance']:.2f}*\n\n🧪 Demo system qofa."
    elif query.data == "history":
        if not u["history"]:
            text = "📜 *HISTORY*\n\nHistory hin jiru."
        else:
            text = "📜 *HISTORY*\n\n"
            for item in u["history"][-10:]:
                text += (
                    f"🕐 {item.get('time','')}\n"
                    f"💰 {item.get('stake',0):.2f} | "
                    f"📈 {item.get('odds',0):.2f} | "
                    f"{item.get('status','')}\n\n"
                )
    else:
        text = (
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football bani\n"
            "2. 📅 Guyyaa filadhu\n"
            "3. ⚽ Match filadhu\n"
            "4. 📊 Market filadhu\n"
            "5. 🎯 Selection filadhu\n"
            "6. 🎟️ Bet Slip ilaali\n\n"
            "📅 Today irraa kaasee guyyaa 7 agarsiisa.\n"
            "💰 Odds SportMonks irraa yoo coverage jiraate.\n"
            "🧪 Demo/testing qofa."
        )

    await query.edit_message_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )

# =========================================================
# FLASK
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    with CACHE_LOCK:
        cache_time = MATCH_CACHE["time"]
        count = len(MATCH_CACHE["matches"])
        refreshing = MATCH_CACHE["refreshing"]
        error = MATCH_CACHE["error"]

    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "Sportmonks Football API",
        "api_key_configured": bool(SPORTMONKS_API_TOKEN),
        "web_app": WEB_APP_URL,
        "days": DAYS_AHEAD,
        "markets": SPORTMONKS_MARKETS or "ALL_AVAILABLE",
        "bookmakers": SPORTMONKS_BOOKMAKERS or "ALL_AVAILABLE",
        "matches_cached": count,
        "cache_age_seconds": round(time.time() - cache_time, 1) if cache_time else None,
        "refreshing": refreshing,
        "cache_error": error,
        "api_last_status": API_STATS["last_status"],
        "api_last_error": API_STATS["last_error"],
        "last_refresh": API_STATS["last_refresh"],
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "BEST BET API is working.",
        "api": "Sportmonks",
        "api_key_configured": bool(SPORTMONKS_API_TOKEN),
        "time": iso_z(datetime.now(timezone.utc)),
    })


@app.route("/api/odds-test", methods=["GET"])
def odds_test():
    try:
        now = datetime.now(timezone.utc)
        body = fetch_fixture_page(
            date_text_utc(now),
            date_text_utc(now + timedelta(days=1)),
            1,
        )
        fixtures = as_list(body)
        samples = []

        for fixture in fixtures[:10]:
            m = convert_fixture(fixture)
            samples.append({
                "fixture_id": m["id"],
                "home": m["home"],
                "away": m["away"],
                "league": m["league"],
                "odds_count": m["odds_count"],
                "sample_odds": m["odds"][:20],
            })

        return jsonify({
            "success": True,
            "api": "Sportmonks",
            "api_key_configured": bool(SPORTMONKS_API_TOKEN),
            "fixture_count": len(fixtures),
            "samples": samples,
            "api_last_status": API_STATS["last_status"],
            "api_last_error": API_STATS["last_error"],
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "api_key_configured": bool(SPORTMONKS_API_TOKEN),
            "api_last_status": API_STATS["last_status"],
            "api_last_error": API_STATS["last_error"],
        }), 200


@app.route("/api/matches", methods=["GET"])
def api_matches():
    force = request.args.get("refresh", "0") == "1"

    try:
        matches = get_matches(force)
        with CACHE_LOCK:
            refreshing = MATCH_CACHE["refreshing"]
            error = MATCH_CACHE["error"]
            cache_time = MATCH_CACHE["time"]

        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football matches loaded."
                if matches else
                "No football fixtures loaded yet. Check /health and /api/odds-test."
            ),
            "loading": refreshing and not bool(matches),
            "stale": bool(matches) and cache_time > 0 and time.time() - cache_time >= CACHE_SECONDS,
            "api": "Sportmonks",
            "api_key_configured": bool(SPORTMONKS_API_TOKEN),
            "api_status": API_STATS["last_status"],
            "api_error": error,
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(exc),
            "api_key_configured": bool(SPORTMONKS_API_TOKEN),
            "api_status": API_STATS["last_status"],
            "api_last_error": API_STATS["last_error"],
        }), 200


@app.route("/api/match/<match_id>", methods=["GET"])
def api_match(match_id):
    try:
        matches = get_matches()
        match = next(
            (m for m in matches if str(m.get("id")) == str(match_id)),
            None
        )

        # If cache does not contain it, fetch the fixture directly.
        fixture = None
        if DETAIL_REFRESH_ODDS:
            body = sportmonks_request(
                f"/fixtures/{match_id}",
                {
                    "include": "participants;league;state;odds"
                },
            )
            if isinstance(body.get("data"), dict):
                fixture = body["data"]

        if fixture:
            converted = convert_fixture(fixture)
        elif match:
            converted = match
        else:
            return jsonify({
                "success": False,
                "error": "Match hin argamne.",
                "markets": [],
            })

        grouped = {}
        for odd in converted.get("odds", []):
            key = (
                odd.get("market_id"),
                odd.get("market") or odd.get("market_description") or "Market"
            )
            grouped.setdefault(key, []).append(odd)

        markets = []
        for (market_id, market_name), odds in grouped.items():
            selections = []
            for odd in odds:
                selections.append({
                    "value": odd.get("label") or odd.get("name"),
                    "odd": odd.get("odd"),
                    "bookmaker": odd.get("bookmaker"),
                    "bookmaker_id": odd.get("bookmaker_id"),
                    "market_id": odd.get("market_id"),
                    "odd_id": odd.get("id"),
                    "probability": odd.get("probability"),
                    "last_update": odd.get("last_update"),
                })
            selections.sort(
                key=lambda x: safe_float(x.get("odd")) or 0,
                reverse=True
            )
            markets.append({
                "id": str(market_id or ""),
                "name": market_name or "Market",
                "selections": selections,
            })

        markets.sort(key=lambda x: str(x["name"]).lower())

        return jsonify({
            "success": True,
            "match": converted,
            "markets": markets,
            "best_bet": converted.get("best_bet"),
            "odds_count": len(converted.get("odds", [])),
            "api": "Sportmonks",
            "api_status": API_STATS["last_status"],
        })
    except Exception as exc:
        print("[MATCH ERROR]", repr(exc))
        return jsonify({
            "success": False,
            "error": str(exc),
            "markets": [],
        }), 200


@app.route("/api/live", methods=["GET"])
def api_live():
    try:
        body = sportmonks_request(
            "/livescores/latest",
            {"include": "participants;league;state;scores"}
        )
        scores = as_list(body)
        result = []

        for event in scores:
            home, away = extract_teams(event)
            home_score = away_score = None

            for score in as_list(event.get("scores")):
                if not isinstance(score, dict):
                    continue
                value = score.get("score")
                if isinstance(value, dict):
                    value = value.get("goals", value.get("current"))

                who = str(
                    score.get("participant")
                    or score.get("participant_id")
                    or score.get("participant_name")
                    or ""
                ).lower()

                if who in ("home", str(home).lower()):
                    home_score = value
                elif who in ("away", str(away).lower()):
                    away_score = value

            result.append({
                "id": event.get("id"),
                "league": extract_league(event),
                "home": home,
                "away": away,
                "home_score": home_score,
                "away_score": away_score,
                "minute": "",
                "state": event.get("state"),
            })

        return jsonify({
            "success": True,
            "count": len(result),
            "matches": result,
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "matches": [],
        }), 200

# =========================================================
# ERRORS
# =========================================================

@app.errorhandler(404)
def handle_404(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": f"API endpoint not found: {request.path}",
        }), 404
    return "<h1>BEST BET</h1><p>Page not found.</p>", 404


@app.errorhandler(500)
def handle_500(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "error": "Internal server error.",
        }), 500
    return "<h1>BEST BET</h1><p>Internal server error.</p>", 500

# =========================================================
# BOT
# =========================================================

def run_telegram_bot():
    if not BOT_TOKEN:
        print("[BOT WARNING] BOT_TOKEN hin jiru.")
        return

    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))

        print("BEST BET TELEGRAM BOT ONLINE")
        print("WEB APP:", WEB_APP_URL)

        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )
    except Exception as exc:
        print("[BOT ERROR]", repr(exc))


def cache_refresh_loop():
    time.sleep(3)
    while True:
        try:
            with CACHE_LOCK:
                cache_time = MATCH_CACHE["time"]
                refreshing = MATCH_CACHE["refreshing"]

            expired = (
                cache_time == 0
                or time.time() - cache_time >= CACHE_SECONDS
            )

            if expired and not refreshing:
                start_refresh_background()
        except Exception as exc:
            print("[CACHE LOOP ERROR]", repr(exc))

        time.sleep(20)

# =========================================================
# START
# =========================================================

def main():
    print("=" * 45)
    print("        BEST BET - SPORTMONKS")
    print("=" * 45)
    print("WEB APP:", WEB_APP_URL)
    print("SPORTMONKS TOKEN:", bool(SPORTMONKS_API_TOKEN))
    print("DAYS:", DAYS_AHEAD)
    print("MARKETS:", SPORTMONKS_MARKETS or "ALL AVAILABLE")
    print("BOOKMAKERS:", SPORTMONKS_BOOKMAKERS or "ALL AVAILABLE")
    print("CACHE:", CACHE_SECONDS)
    print("TIMEOUT:", API_TIMEOUT)
    print("PORT:", PORT)
    print("=" * 45)

    threading.Thread(
        target=cache_refresh_loop,
        daemon=True,
        name="cache-refresh-loop",
    ).start()

    if BOT_TOKEN:
        threading.Thread(
            target=run_telegram_bot,
            daemon=True,
            name="telegram-bot",
        ).start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
