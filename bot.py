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

# SPORTMONKS replaces The Odds API.
SPORTMONKS_API_KEY = os.getenv(
    "SPORTMONKS_API_KEY", ""
).strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip().rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

SPORTMONKS_BASE = (
    "https://api.sportmonks.com/v3/football"
)

DAYS_AHEAD = max(
    1, int(os.getenv("DAYS_AHEAD", "7"))
)

API_TIMEOUT = max(
    5, int(os.getenv("API_TIMEOUT", "15"))
)

CACHE_SECONDS = max(
    30, int(os.getenv("CACHE_SECONDS", "60"))
)

INITIAL_WAIT_SECONDS = max(
    0, int(os.getenv("INITIAL_WAIT_SECONDS", "15"))
)

MAX_WORKERS = max(
    1, int(os.getenv("MAX_WORKERS", "8"))
)

# Number of fixture pages to read.
MAX_FIXTURE_PAGES = max(
    1, int(os.getenv("MAX_FIXTURE_PAGES", "10"))
)

# If true, details are requested separately when a match is opened.
DETAIL_REFRESH_ODDS = (
    os.getenv("DETAIL_REFRESH_ODDS", "1").strip() == "1"
)

# Optional bookmaker filter. Empty = all bookmakers available to the plan.
SPORTMONKS_BOOKMAKERS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_BOOKMAKERS", ""
    ).split(",")
    if x.strip()
]

# Optional market filter. Empty = ALL markets returned by Sportmonks.
SPORTMONKS_MARKETS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_MARKETS", ""
    ).split(",")
    if x.strip()
]

# Keep all odds by default.
KEEP_ALL_ODDS = (
    os.getenv("KEEP_ALL_ODDS", "1").strip() == "1"
)

# Ethiopia UTC+3
ETHIOPIA_TZ = timezone(timedelta(hours=3))


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# USER DATA
# =========================================================

USERS = {}


# =========================================================
# CACHE
# =========================================================

CACHE_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
INITIAL_CACHE_EVENT = threading.Event()

MATCH_CACHE = {
    "time": 0.0,
    "matches": [],
    "error": None,
    "refreshing": False,
}


# =========================================================
# API STATISTICS
# =========================================================

API_STATS = {
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
    "last_response_time": None,
}


# =========================================================
# USER FUNCTIONS
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


def parse_iso_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None


def local_time_text(iso_time):
    dt = parse_iso_datetime(iso_time)
    if not dt:
        return ""
    return dt.astimezone(ETHIOPIA_TZ).strftime(
        "%d/%m/%Y %H:%M"
    )


def date_text_utc(dt):
    return dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%d"
    )


# =========================================================
# SPORTMONKS REQUEST
# =========================================================

def sportmonks_request(path, params=None):
    if not SPORTMONKS_API_KEY:
        raise RuntimeError(
            "SPORTMONKS_API_KEY hin jiru. "
            "Render > Environment keessatti "
            "SPORTMONKS_API_KEY galchi."
        )

    query = dict(params or {})
    query["api_token"] = SPORTMONKS_API_KEY

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
                "User-Agent": "BEST-BET/5.0",
            },
        )
    except requests.RequestException as exc:
        API_STATS["last_status"] = None
        API_STATS["last_error"] = str(exc)
        raise RuntimeError(
            f"Sportmonks connection error: {exc}"
        ) from exc
    finally:
        API_STATS["last_response_time"] = round(
            time.time() - started, 3
        )

    API_STATS["last_status"] = response.status_code

    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = response.text[:2000]

        message = (
            f"Sportmonks HTTP {response.status_code}: "
            f"{body}"
        )
        API_STATS["last_error"] = message
        raise RuntimeError(message)

    try:
        body = response.json()
    except Exception as exc:
        message = "Sportmonks JSON sirrii hin deebifne."
        API_STATS["last_error"] = message
        raise RuntimeError(message) from exc

    # Sportmonks can return an API error object.
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
# GENERIC LIST EXTRACTION
# =========================================================

def as_list(value):
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            return data

    return []


def nested_list(obj, *keys):
    current = obj

    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return []

    return as_list(current)


# =========================================================
# TEAM / LEAGUE NAME HELPERS
# =========================================================

def participant_name(participant):
    if not isinstance(participant, dict):
        return ""

    return (
        participant.get("name")
        or participant.get("short_code")
        or participant.get("short_name")
        or ""
    )


def extract_teams(fixture):
    participants = (
        fixture.get("participants")
        or fixture.get("teams")
        or []
    )

    if isinstance(participants, dict):
        participants = as_list(participants)

    home = ""
    away = ""

    for participant in participants:
        meta = participant.get("meta") or {}
        location = str(
            meta.get("location", "")
        ).lower()

        name = participant_name(participant)

        if location == "home":
            home = name
        elif location == "away":
            away = name

    # Fallback for older/alternate responses.
    if not home and participants:
        home = participant_name(participants[0])

    if not away and len(participants) > 1:
        away = participant_name(participants[1])

    # More fallbacks.
    home = (
        home
        or fixture.get("home_team")
        or fixture.get("localteam_name")
        or "Home"
    )

    away = (
        away
        or fixture.get("away_team")
        or fixture.get("visitorteam_name")
        or "Away"
    )

    return home, away


def extract_league(fixture):
    league = fixture.get("league")

    if isinstance(league, dict):
        return (
            league.get("name")
            or league.get("title")
            or "Football"
        )

    return (
        fixture.get("league_name")
        or "Football"
    )


# =========================================================
# ODDS EXTRACTION
# =========================================================

def extract_odds_from_fixture(fixture):
    """
    Sportmonks fixture include=odds can have odds as:
      fixture["odds"] = [...]
    or:
      fixture["odds"]["data"] = [...]
    """

    raw = fixture.get("odds")

    odds = as_list(raw)

    # Some responses can nest under odds.data.
    if not odds and isinstance(raw, dict):
        odds = raw.get("data") or []

    return [
        x for x in odds
        if isinstance(x, dict)
    ]


def normalize_odd(odd):
    value = safe_float(
        odd.get("value")
    )

    if value is None or value <= 1:
        return None

    market = odd.get("market") or {}
    bookmaker = odd.get("bookmaker") or {}

    market_id = (
        odd.get("market_id")
        or market.get("id")
    )

    market_name = (
        market.get("name")
        or odd.get("market_description")
        or odd.get("market_name")
        or ""
    )

    bookmaker_id = (
        odd.get("bookmaker_id")
        or bookmaker.get("id")
    )

    bookmaker_name = (
        bookmaker.get("name")
        or odd.get("bookmaker_name")
        or ""
    )

    label = str(
        odd.get("label")
        or odd.get("name")
        or ""
    ).strip()

    probability = safe_float(
        odd.get("probability")
    )

    return {
        "id": odd.get("id"),
        "fixture_id": odd.get("fixture_id"),
        "market_id": market_id,
        "market": market_name,
        "market_description": (
            odd.get("market_description")
            or market.get("description")
            or ""
        ),
        "bookmaker_id": bookmaker_id,
        "bookmaker": bookmaker_name,
        "label": label,
        "name": str(
            odd.get("name") or label
        ),
        "odd": value,
        "value": value,
        "probability": probability,
        "last_update": (
            odd.get("last_update")
            or odd.get("updated_at")
        ),
        "sort_order": odd.get("sort_order"),
    }


def get_all_normalized_odds(odds):
    result = []

    for odd in odds:
        normalized = normalize_odd(odd)

        if not normalized:
            continue

        # Optional bookmaker filter.
        if (
            SPORTMONKS_BOOKMAKERS
            and str(
                normalized.get("bookmaker_id")
            ) not in SPORTMONKS_BOOKMAKERS
        ):
            continue

        # Optional market filter.
        if (
            SPORTMONKS_MARKETS
            and str(
                normalized.get("market_id")
            ) not in SPORTMONKS_MARKETS
        ):
            continue

        result.append(normalized)

    return result


# =========================================================
# MARKET GROUPING
# =========================================================

def market_key(odd):
    market = str(
        odd.get("market")
        or ""
    ).lower()

    desc = str(
        odd.get("market_description")
        or ""
    ).lower()

    text = f"{market} {desc}"

    if (
        "both teams" in text
        or "btts" in text
        or "both_to_score" in text
    ):
        return "btts"

    if (
        "over/under" in text
        or "total" in text
        or "goals" in text
    ):
        return "totals"

    if (
        "handicap" in text
        or "asian handicap" in text
        or "spread" in text
    ):
        return "spreads"

    if (
        "match winner" in text
        or "1x2" in text
        or "fulltime result" in text
        or market in (
            "match winner",
            "1x2",
            "fulltime result",
        )
    ):
        return "h2h"

    return "other"


def choose_best_h2h(odds, home, away):
    """
    Build 1/X/2 from real Sportmonks odds.
    Prefer the highest available price for each selection
    so the displayed selection is not tied to one bookmaker.
    """

    result = {}

    for odd in odds:
        if market_key(odd) != "h2h":
            continue

        label = str(
            odd.get("label")
            or odd.get("name")
            or ""
        ).strip()

        low = label.lower()

        if label == home or low in ("home", "1"):
            key = "home"
        elif label == away or low in ("away", "2"):
            key = "away"
        elif low in (
            "draw",
            "x",
        ):
            key = "draw"
        else:
            continue

        if (
            key not in result
            or odd["odd"] > result[key]["odd"]
        ):
            result[key] = odd

    return result


def choose_main_totals(odds):
    result = {}

    for odd in odds:
        if market_key(odd) != "totals":
            continue

        label = str(
            odd.get("label")
            or odd.get("name")
            or ""
        ).lower()

        if "over" in label:
            key = "over"
        elif "under" in label:
            key = "under"
        else:
            continue

        if (
            key not in result
            or odd["odd"] > result[key]["odd"]
        ):
            result[key] = odd

    return result


def choose_btts(odds):
    result = {}

    for odd in odds:
        if market_key(odd) != "btts":
            continue

        label = str(
            odd.get("label")
            or odd.get("name")
            or ""
        ).lower()

        if label in ("yes", "btts yes"):
            key = "yes"
        elif label in ("no", "btts no"):
            key = "no"
        else:
            continue

        if (
            key not in result
            or odd["odd"] > result[key]["odd"]
        ):
            result[key] = odd

    return result


# =========================================================
# BEST BET
# =========================================================

def calculate_best_bet(
    normalized_odds,
    home,
    away,
):
    candidates = []

    h2h = choose_best_h2h(
        normalized_odds,
        home,
        away,
    )

    if h2h.get("home"):
        candidates.append({
            "selection": "1",
            "odd": h2h["home"]["odd"],
            "market": "1X2",
            "bookmaker": h2h["home"].get("bookmaker"),
            "bookmaker_id": h2h["home"].get("bookmaker_id"),
            "market_id": h2h["home"].get("market_id"),
        })

    if h2h.get("draw"):
        candidates.append({
            "selection": "X",
            "odd": h2h["draw"]["odd"],
            "market": "1X2",
            "bookmaker": h2h["draw"].get("bookmaker"),
            "bookmaker_id": h2h["draw"].get("bookmaker_id"),
            "market_id": h2h["draw"].get("market_id"),
        })

    if h2h.get("away"):
        candidates.append({
            "selection": "2",
            "odd": h2h["away"]["odd"],
            "market": "1X2",
            "bookmaker": h2h["away"].get("bookmaker"),
            "bookmaker_id": h2h["away"].get("bookmaker_id"),
            "market_id": h2h["away"].get("market_id"),
        })

    totals = choose_main_totals(
        normalized_odds
    )

    if totals.get("over"):
        candidates.append({
            "selection": "Over",
            "odd": totals["over"]["odd"],
            "market": "Over/Under",
            "bookmaker": totals["over"].get("bookmaker"),
            "bookmaker_id": totals["over"].get("bookmaker_id"),
            "market_id": totals["over"].get("market_id"),
        })

    if totals.get("under"):
        candidates.append({
            "selection": "Under",
            "odd": totals["under"]["odd"],
            "market": "Over/Under",
            "bookmaker": totals["under"].get("bookmaker"),
            "bookmaker_id": totals["under"].get("bookmaker_id"),
            "market_id": totals["under"].get("market_id"),
        })

    btts = choose_btts(
        normalized_odds
    )

    if btts.get("yes"):
        candidates.append({
            "selection": "BTTS Yes",
            "odd": btts["yes"]["odd"],
            "market": "BTTS",
            "bookmaker": btts["yes"].get("bookmaker"),
            "bookmaker_id": btts["yes"].get("bookmaker_id"),
            "market_id": btts["yes"].get("market_id"),
        })

    if btts.get("no"):
        candidates.append({
            "selection": "BTTS No",
            "odd": btts["no"]["odd"],
            "market": "BTTS",
            "bookmaker": btts["no"].get("bookmaker"),
            "bookmaker_id": btts["no"].get("bookmaker_id"),
            "market_id": btts["no"].get("market_id"),
        })

    candidates = [
        x for x in candidates
        if safe_float(x.get("odd"))
        and 1.01 < float(x["odd"]) <= 100
    ]

    if not candidates:
        return None

    # Keep the original "strongest/shortest price" idea,
    # but now it is based on real Sportmonks odds.
    candidates.sort(
        key=lambda x: float(x["odd"])
    )

    return candidates[0]


# =========================================================
# CONVERT SPORTMONKS FIXTURE
# =========================================================

def convert_fixture(fixture):
    fixture_id = fixture.get("id")

    home, away = extract_teams(
        fixture
    )

    league = extract_league(
        fixture
    )

    commence = (
        fixture.get("starting_at")
        or fixture.get("starting_at_timestamp")
        or fixture.get("commence_time")
    )

    # starting_at_timestamp may be unix seconds.
    if isinstance(commence, (int, float)):
        commence = datetime.fromtimestamp(
            commence,
            timezone.utc,
        ).isoformat()

    raw_odds = extract_odds_from_fixture(
        fixture
    )

    normalized_odds = get_all_normalized_odds(
        raw_odds
    )

    h2h = choose_best_h2h(
        normalized_odds,
        home,
        away,
    )

    totals = choose_main_totals(
        normalized_odds
    )

    btts = choose_btts(
        normalized_odds
    )

    best_bet = calculate_best_bet(
        normalized_odds,
        home,
        away,
    )

    return {
        "id": fixture_id,
        "fixture_id": fixture_id,
        "sport_key": "football",
        "league": league,
        "home": home,
        "away": away,
        "time": local_time_text(
            commence
        ),
        "commence_time": commence,

        # Compatibility with the old frontend.
        "h2h": {
            "home": h2h["home"]["odd"]
            if h2h.get("home") else None,
            "draw": h2h["draw"]["odd"]
            if h2h.get("draw") else None,
            "away": h2h["away"]["odd"]
            if h2h.get("away") else None,
        },

        "totals": {
            "over": totals["over"]["odd"]
            if totals.get("over") else None,
            "under": totals["under"]["odd"]
            if totals.get("under") else None,
        },

        "btts": {
            "yes": btts["yes"]["odd"]
            if btts.get("yes") else None,
            "no": btts["no"]["odd"]
            if btts.get("no") else None,
        },

        "spreads": [
            {
                "name": odd.get("label"),
                "point": odd.get("label"),
                "price": odd.get("odd"),
                "market_id": odd.get("market_id"),
                "bookmaker": odd.get("bookmaker"),
            }
            for odd in normalized_odds
            if market_key(odd) == "spreads"
        ],

        # ALL real odds, not only the old four markets.
        "odds": normalized_odds,

        "odds_count": len(
            normalized_odds
        ),

        "best_bet": best_bet,

        "state": fixture.get("state"),
    }


# =========================================================
# FETCH FIXTURES + ODDS
# =========================================================

def fetch_fixture_page(
    start_date,
    end_date,
    page=1,
):
    params = {
        # These includes let the same fixture response carry
        # teams, league, state and all odds available to the plan.
        "include": (
            "participants;"
            "league;"
            "state;"
            "odds"
        ),
        "page": page,
    }

    if SPORTMONKS_BOOKMAKERS:
        params["bookmakers"] = ",".join(
            SPORTMONKS_BOOKMAKERS
        )

    if SPORTMONKS_MARKETS:
        params["markets"] = ",".join(
            SPORTMONKS_MARKETS
        )

    return sportmonks_request(
        f"/fixtures/between/"
        f"{start_date}/{end_date}",
        params,
    )


def fetch_matches_from_api():
    if not SPORTMONKS_API_KEY:
        raise RuntimeError(
            "SPORTMONKS_API_KEY hin jiru."
        )

    now = datetime.now(
        timezone.utc
    )

    end_time = (
        now + timedelta(days=DAYS_AHEAD)
    )

    start_date = date_text_utc(now)
    end_date = date_text_utc(end_time)

    print(
        "===================================="
    )
    print(
        "[SPORTMONKS]"
    )
    print(
        "[FROM]", start_date
    )
    print(
        "[TO]", end_date
    )
    print(
        "[MARKETS]",
        SPORTMONKS_MARKETS or "ALL"
    )
    print(
        "[BOOKMAKERS]",
        SPORTMONKS_BOOKMAKERS or "ALL"
    )
    print(
        "===================================="
    )

    fixtures = []
    page = 1

    while page <= MAX_FIXTURE_PAGES:
        body = fetch_fixture_page(
            start_date,
            end_date,
            page,
        )

        page_data = as_list(body)

        if not page_data:
            break

        fixtures.extend(
            page_data
        )

        print(
            "[FIXTURE PAGE]",
            page,
            len(page_data)
        )

        # Stop when pagination says there is no next page.
        pagination = (
            body.get("pagination")
            if isinstance(body, dict)
            else None
        )

        if not isinstance(
            pagination,
            dict,
        ):
            break

        has_more = pagination.get(
            "has_more"
        )

        if has_more is False:
            break

        # Some versions expose next_page.
        next_page = pagination.get(
            "next_page"
        )

        if next_page:
            page = int(next_page)
        else:
            page += 1

    result = []

    for fixture in fixtures:
        try:
            commence = (
                fixture.get("starting_at")
                or fixture.get("commence_time")
            )

            dt = parse_iso_datetime(
                commence
            )

            if not dt:
                # If Sportmonks gave timestamp.
                ts = fixture.get(
                    "starting_at_timestamp"
                )
                if ts:
                    dt = datetime.fromtimestamp(
                        float(ts),
                        timezone.utc,
                    )

            if not dt:
                continue

            if dt < now or dt > end_time:
                continue

            converted = convert_fixture(
                fixture
            )

            # Only expose fixtures with real odds.
            if converted["odds_count"] <= 0:
                continue

            result.append(
                converted
            )

        except Exception as exc:
            print(
                "[FIXTURE ERROR]",
                repr(exc)
            )

    unique = {}

    for match in result:
        key = str(
            match.get("id") or ""
        )

        if key and key not in unique:
            unique[key] = match

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            x.get(
                "commence_time",
                ""
            )
        )
    )

    print(
        "[TOTAL REAL MATCHES]",
        len(result)
    )

    print(
        "[TOTAL REAL ODDS]",
        sum(
            int(x.get("odds_count", 0))
            for x in result
        )
    )

    return result, []


# =========================================================
# REFRESH CACHE
# =========================================================

def refresh_matches(force=False):
    if not REFRESH_LOCK.acquire(
        blocking=False
    ):
        return False

    try:
        with CACHE_LOCK:
            MATCH_CACHE[
                "refreshing"
            ] = True

        print(
            "[MATCH REFRESH] started"
        )

        try:
            matches, errors = (
                fetch_matches_from_api()
            )

            with CACHE_LOCK:
                if matches:
                    MATCH_CACHE[
                        "matches"
                    ] = matches

                    MATCH_CACHE[
                        "time"
                    ] = time.time()

                MATCH_CACHE[
                    "error"
                ] = (
                    errors[-10:]
                    if errors
                    else (
                        None
                        if matches
                        else
                        "No football fixtures with "
                        "available Sportmonks odds."
                    )
                )

                API_STATS[
                    "last_refresh"
                ] = iso_z(
                    datetime.now(
                        timezone.utc
                    )
                )

                INITIAL_CACHE_EVENT.set()

            print(
                "[MATCH REFRESH] done:",
                len(matches)
            )
            return True

        except Exception as exc:
            print(
                "[MATCH REFRESH ERROR]",
                repr(exc)
            )

            with CACHE_LOCK:
                MATCH_CACHE[
                    "error"
                ] = str(exc)

                INITIAL_CACHE_EVENT.set()

            return False

        finally:
            with CACHE_LOCK:
                MATCH_CACHE[
                    "refreshing"
                ] = False

    finally:
        REFRESH_LOCK.release()


def start_refresh_background(
    force=False
):
    with CACHE_LOCK:
        if MATCH_CACHE[
            "refreshing"
        ]:
            return False

        thread = threading.Thread(
            target=refresh_matches,
            args=(force,),
            daemon=True,
            name="sportmonks-refresh",
        )
        thread.start()

    return True


def get_matches(force=False):
    now_ts = time.time()

    with CACHE_LOCK:
        cached_time = MATCH_CACHE[
            "time"
        ]

        cached_matches = list(
            MATCH_CACHE["matches"]
        )

        refreshing = MATCH_CACHE[
            "refreshing"
        ]

    fresh = (
        cached_time > 0
        and (
            now_ts - cached_time
        ) < CACHE_SECONDS
    )

    if not force and fresh:
        return cached_matches

    if not refreshing:
        start_refresh_background(
            force=force
        )

    with CACHE_LOCK:
        cached_matches = list(
            MATCH_CACHE["matches"]
        )

    if (
        not cached_matches
        and INITIAL_WAIT_SECONDS > 0
    ):
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
                )
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                )
            ),
            InlineKeyboardButton(
                "🎟️ BET SLIP",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                )
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
# TELEGRAM START
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
        "📊 Real Sportmonks Odds\n"
        "🎟️ Bet Slip\n\n"
        "👇 *⚽ FOOTBALL* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# =========================================================
# TELEGRAM BUTTON HANDLER
# =========================================================

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
            text = "📜 *HISTORY*\n\n"

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
            "💰 Odds dhugaa Sportmonks irraa.\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# HOME
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
    with CACHE_LOCK:
        cache_time = MATCH_CACHE[
            "time"
        ]
        cache_count = len(
            MATCH_CACHE["matches"]
        )
        refreshing = MATCH_CACHE[
            "refreshing"
        ]
        cache_error = MATCH_CACHE[
            "error"
        ]

    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "Sportmonks Football API",
        "api_key_configured": bool(
            SPORTMONKS_API_KEY
        ),
        "web_app": WEB_APP_URL,
        "days": DAYS_AHEAD,
        "markets": (
            SPORTMONKS_MARKETS
            or "ALL_AVAILABLE"
        ),
        "bookmakers": (
            SPORTMONKS_BOOKMAKERS
            or "ALL_AVAILABLE"
        ),
        "matches_cached": cache_count,
        "cache_age_seconds": (
            round(
                time.time() - cache_time,
                1,
            )
            if cache_time else None
        ),
        "refreshing": refreshing,
        "cache_error": cache_error,
        "api_last_status": API_STATS[
            "last_status"
        ],
        "api_last_error": API_STATS[
            "last_error"
        ],
        "last_refresh": API_STATS[
            "last_refresh"
        ],
    })


# =========================================================
# BASIC API TEST
# =========================================================

@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message":
            "BEST BET API is working.",
        "api": "Sportmonks",
        "api_key_configured":
            bool(SPORTMONKS_API_KEY),
        "time":
            iso_z(
                datetime.now(
                    timezone.utc
                )
            ),
    })


# =========================================================
# SPORTMONKS ODDS TEST
# =========================================================

@app.route("/api/odds-test", methods=["GET"])
def odds_test():
    try:
        now = datetime.now(
            timezone.utc
        )

        end = (
            now + timedelta(days=1)
        )

        body = fetch_fixture_page(
            date_text_utc(now),
            date_text_utc(end),
            1,
        )

        fixtures = as_list(body)

        sample = []

        for fixture in fixtures[:10]:
            converted = convert_fixture(
                fixture
            )

            sample.append({
                "fixture_id":
                    converted["id"],
                "home":
                    converted["home"],
                "away":
                    converted["away"],
                "league":
                    converted["league"],
                "odds_count":
                    converted["odds_count"],
                "sample_odds":
                    converted["odds"][:20],
            })

        return jsonify({
            "success": True,
            "message":
                "Sportmonks connected.",
            "api_key_configured":
                bool(SPORTMONKS_API_KEY),
            "fixture_count":
                len(fixtures),
            "samples":
                sample,
            "api_last_status":
                API_STATS[
                    "last_status"
                ],
            "api_last_error":
                API_STATS[
                    "last_error"
                ],
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "message":
                "Sportmonks connection failed.",
            "error":
                str(exc),
            "api_key_configured":
                bool(SPORTMONKS_API_KEY),
            "api_last_status":
                API_STATS[
                    "last_status"
                ],
            "api_last_error":
                API_STATS[
                    "last_error"
                ],
        }), 200


# =========================================================
# MATCHES API
# =========================================================

@app.route("/api/matches", methods=["GET"])
def api_matches():
    force = (
        request.args.get(
            "refresh",
            "0",
        ) == "1"
    )

    try:
        matches = get_matches(
            force=force
        )

        with CACHE_LOCK:
            refreshing = MATCH_CACHE[
                "refreshing"
            ]
            cache_error = MATCH_CACHE[
                "error"
            ]
            cache_time = MATCH_CACHE[
                "time"
            ]

        stale = (
            bool(matches)
            and cache_time > 0
            and (
                time.time() - cache_time
            ) >= CACHE_SECONDS
        )

        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football real odds loaded."
                if matches
                else
                "Matches are loading. "
                "Please refresh shortly."
            ),
            "loading": (
                refreshing
                and not bool(matches)
            ),
            "stale": stale,
            "api":
                "Sportmonks",
            "api_key_configured":
                bool(SPORTMONKS_API_KEY),
            "api_status":
                API_STATS[
                    "last_status"
                ],
            "api_error":
                cache_error,
        }), 200

    except Exception as exc:
        print(
            "[API MATCHES ERROR]",
            repr(exc)
        )

        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(exc),
            "message":
                "Football odds loading failed.",
            "api_key_configured":
                bool(SPORTMONKS_API_KEY),
            "api_status":
                API_STATS[
                    "last_status"
                ],
            "api_last_error":
                API_STATS[
                    "last_error"
                ],
        }), 200


# =========================================================
# SINGLE MATCH DETAILS
# =========================================================

@app.route(
    "/api/match/<match_id>",
    methods=["GET"],
)
def api_match(match_id):
    try:
        matches = get_matches()

        match = next(
            (
                item
                for item in matches
                if str(
                    item.get("id")
                ) == str(match_id)
            ),
            None,
        )

        if not match:
            return jsonify({
                "success": False,
                "error":
                    "Match hin argamne.",
                "markets": [],
            }), 200

        # Optional fresh odds request.
        fixture = None

        if DETAIL_REFRESH_ODDS:
            try:
                body = sportmonks_request(
                    f"/fixtures/{match_id}",
                    {
                        "include":
                            "participants;"
                            "league;"
                            "state;"
                            "odds"
                    },
                )

                data = body.get("data")

                if isinstance(data, dict):
                    fixture = data

            except Exception as exc:
                print(
                    "[DETAIL ODDS ERROR]",
                    match_id,
                    repr(exc)
                )

        if fixture:
            converted = convert_fixture(
                fixture
            )
        else:
            converted = match

        all_odds = converted.get(
            "odds"
        ) or []

        # Group every market returned by Sportmonks.
        grouped = {}

        for odd in all_odds:
            key = (
                odd.get("market_id"),
                odd.get("market")
                or odd.get(
                    "market_description"
                )
                or "Market",
            )

            grouped.setdefault(
                key,
                []
            ).append(odd)

        markets = []

        for (
            (market_id, market_name),
            odds
        ) in grouped.items():

            selections = []

            for odd in odds:
                selections.append({
                    "value":
                        odd.get(
                            "label"
                        )
                        or odd.get(
                            "name"
                        ),
                    "odd":
                        odd.get(
                            "odd"
                        ),
                    "bookmaker":
                        odd.get(
                            "bookmaker"
                        ),
                    "bookmaker_id":
                        odd.get(
                            "bookmaker_id"
                        ),
                    "market_id":
                        odd.get(
                            "market_id"
                        ),
                    "odd_id":
                        odd.get(
                            "id"
                        ),
                    "probability":
                        odd.get(
                            "probability"
                        ),
                    "last_update":
                        odd.get(
                            "last_update"
                        ),
                })

            selections.sort(
                key=lambda x: (
                    safe_float(
                        x.get("odd")
                    ) or 0
                ),
                reverse=True,
            )

            markets.append({
                "id":
                    str(
                        market_id
                        or ""
                    ),
                "name":
                    market_name
                    or "Market",
                "selections":
                    selections,
            })

        markets.sort(
            key=lambda x: str(
                x.get("name")
            ).lower()
        )

        return jsonify({
            "success": True,
            "match": converted,
            "markets": markets,
            "best_bet":
                converted.get(
                    "best_bet"
                ),
            "odds_count":
                len(all_odds),
            "api":
                "Sportmonks",
            "api_status":
                API_STATS[
                    "last_status"
                ],
        }), 200

    except Exception as exc:
        print(
            "[MATCH ERROR]",
            repr(exc)
        )

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
        body = sportmonks_request(
            "/livescores/latest",
            {
                "include":
                    "participants;league;state"
            },
        )

        scores = as_list(body)
        result = []

        for event in scores:
            fixture_id = event.get(
                "id"
            )

            home, away = extract_teams(
                event
            )

            # Try common score structures.
            home_score = None
            away_score = None

            scores_data = (
                event.get("scores")
                or []
            )

            if isinstance(
                scores_data,
                dict
            ):
                scores_data = (
                    scores_data.get(
                        "data"
                    )
                    or []
                )

            for score in scores_data:
                if not isinstance(
                    score,
                    dict,
                ):
                    continue

                participant = str(
                    score.get(
                        "participant"
                    )
                    or score.get(
                        "participant_id"
                    )
                    or ""
                ).lower()

                score_value = (
                    score.get("score")
                    if not isinstance(
                        score.get("score"),
                        dict,
                    )
                    else (
                        score.get(
                            "score"
                        ).get("goals")
                        or score.get(
                            "score"
                        ).get("current")
                    )
                )

                name = str(
                    score.get("participant_name")
                    or ""
                ).lower()

                if (
                    participant == "home"
                    or name == home.lower()
                ):
                    home_score = score_value

                elif (
                    participant == "away"
                    or name == away.lower()
                ):
                    away_score = score_value

            result.append({
                "id":
                    fixture_id,
                "league":
                    extract_league(event),
                "home":
                    home,
                "away":
                    away,
                "home_score":
                    home_score,
                "away_score":
                    away_score,
                "minute":
                    "",
                "state":
                    event.get("state"),
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


@app.errorhandler(500)
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
# TELEGRAM BOT
# =========================================================

def run_telegram_bot():
    if not BOT_TOKEN:
        print(
            "[BOT WARNING] BOT_TOKEN hin jiru."
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
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )

    except Exception as exc:
        print(
            "[BOT ERROR]",
            repr(exc)
        )


# =========================================================
# BACKGROUND CACHE LOOP
# =========================================================

def cache_refresh_loop():
    time.sleep(3)

    while True:
        try:
            with CACHE_LOCK:
                cache_time = MATCH_CACHE[
                    "time"
                ]
                refreshing = MATCH_CACHE[
                    "refreshing"
                ]

            expired = (
                cache_time == 0
                or (
                    time.time()
                    - cache_time
                ) >= CACHE_SECONDS
            )

            if expired and not refreshing:
                start_refresh_background()

        except Exception as exc:
            print(
                "[CACHE LOOP ERROR]",
                repr(exc)
            )

        time.sleep(20)


# =========================================================
# START
# =========================================================

def main():
    print(
        "===================================="
    )
    print(
        "        BEST BET - SPORTMONKS"
    )
    print(
        "===================================="
    )
    print(
        "WEB APP:",
        WEB_APP_URL,
    )
    print(
        "SPORTMONKS KEY:",
        bool(SPORTMONKS_API_KEY),
    )
    print(
        "DAYS:",
        DAYS_AHEAD,
    )
    print(
        "MARKETS:",
        SPORTMONKS_MARKETS or "ALL AVAILABLE",
    )
    print(
        "BOOKMAKERS:",
        SPORTMONKS_BOOKMAKERS or "ALL AVAILABLE",
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

    cache_thread = threading.Thread(
        target=cache_refresh_loop,
        daemon=True,
        name="cache-refresh-loop",
    )
    cache_thread.start()

    if BOT_TOKEN:
        bot_thread = threading.Thread(
            target=run_telegram_bot,
            daemon=True,
            name="telegram-bot",
        )
        bot_thread.start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
