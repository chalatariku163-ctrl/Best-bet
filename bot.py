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

API_TIMEOUT = max(
    5,
    int(os.getenv("API_TIMEOUT", "25"))
)

CACHE_SECONDS = max(
    30,
    int(os.getenv("CACHE_SECONDS", "120"))
)

INITIAL_WAIT_SECONDS = max(
    0,
    int(os.getenv("INITIAL_WAIT_SECONDS", "20"))
)

MAX_FIXTURE_PAGES = max(
    1,
    int(os.getenv("MAX_FIXTURE_PAGES", "20"))
)

# Direct odds endpoint fallback.
# 1 = enabled
DETAIL_REFRESH_ODDS = (
    os.getenv("DETAIL_REFRESH_ODDS", "1").strip() == "1"
)

# Maximum number of direct fixture-odds requests
# during one refresh.
MAX_ODDS_DETAIL_REQUESTS = max(
    0,
    int(os.getenv("MAX_ODDS_DETAIL_REQUESTS", "80"))
)

ODDS_WORKERS = max(
    1,
    int(os.getenv("ODDS_WORKERS", "8"))
)

# Optional bookmaker IDs.
# Leave EMPTY to use ALL available bookmakers.
SPORTMONKS_BOOKMAKERS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_BOOKMAKERS", ""
    ).split(",")
    if x.strip()
]

# Optional market IDs.
# Leave EMPTY to use ALL available markets.
SPORTMONKS_MARKETS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_MARKETS", ""
    ).split(",")
    if x.strip()
]

ETHIOPIA_TZ = timezone(
    timedelta(hours=3)
)

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

# =========================================================
# USERS
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
# API STATS
# =========================================================

API_STATS = {
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
    "last_response_time": None,

    "fixture_count": 0,
    "fixtures_with_odds": 0,
    "odds_count": 0,
    "direct_odds_requests": 0,
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
            odd = float(
                item.get("odd", 1)
            )

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

    for i, item in enumerate(
        slips,
        1
    ):

        try:
            odd = float(
                item.get("odd", 0)
            )
        except Exception:
            odd = 0

        text += (
            f"*{i}.* "
            f"{item.get('home', '')} "
            f"vs "
            f"{item.get('away', '')}\n"

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

        text = str(value).strip()

        text = text.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            text
        )

        # SportMonks can return:
        # 2026-08-22 15:00:00
        #
        # If no timezone is included,
        # treat it as UTC.
        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:

        return None


def iso_z(dt):

    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def local_time_text(value):

    dt = parse_iso_datetime(value)

    if not dt:
        return ""

    return dt.astimezone(
        ETHIOPIA_TZ
    ).strftime(
        "%d/%m/%Y %H:%M"
    )


def date_text_utc(dt):

    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%d"
    )


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

    return (
        p.get("name")
        or p.get("short_code")
        or p.get("short_name")
        or ""
    )


def extract_teams(fixture):

    participants = (
        fixture.get("participants")
        or fixture.get("teams")
        or []
    )

    participants = as_list(
        participants
    )

    home = ""
    away = ""

    for p in participants:

        if not isinstance(p, dict):
            continue

        meta = p.get("meta") or {}

        location = str(
            meta.get("location", "")
        ).lower()

        name = participant_name(p)

        if location == "home":
            home = name

        elif location == "away":
            away = name

    # Fallback
    if not home and participants:

        home = participant_name(
            participants[0]
        )

    if (
        not away
        and len(participants) > 1
    ):

        away = participant_name(
            participants[1]
        )

    return (
        home
        or fixture.get("home_team")
        or fixture.get("localteam_name")
        or "Home",

        away
        or fixture.get("away_team")
        or fixture.get("visitorteam_name")
        or "Away",
    )


def extract_league(fixture):

    league = fixture.get(
        "league"
    )

    if isinstance(
        league,
        dict
    ):

        return (
            league.get("name")
            or league.get("title")
            or "Football"
        )

    return (
        fixture.get("league_name")
        or "Football"
    )


def extract_league_id(fixture):

    league = fixture.get(
        "league"
    )

    if isinstance(
        league,
        dict
    ):

        return league.get("id")

    return fixture.get(
        "league_id"
    )


# =========================================================
# SPORTMONKS REQUEST
# =========================================================

def sportmonks_request(
    path,
    params=None
):

    if not SPORTMONKS_API_TOKEN:

        raise RuntimeError(
            "SPORTMONKS_API_TOKEN hin jiru. "
            "Render > Environment Variables "
            "keessatti token galchi."
        )

    query = dict(
        params or {}
    )

    query["api_token"] = (
        SPORTMONKS_API_TOKEN
    )

    url = (
        SPORTMONKS_BASE
        + path
    )

    API_STATS[
        "last_request"
    ] = url

    started = time.time()

    try:

        response = requests.get(
            url,
            params=query,
            timeout=API_TIMEOUT,
            headers={
                "Accept":
                    "application/json",

                "User-Agent":
                    "BEST-BET/7.0",
            },
        )

    except requests.RequestException as exc:

        API_STATS[
            "last_status"
        ] = None

        API_STATS[
            "last_error"
        ] = str(exc)

        raise RuntimeError(
            f"SportMonks connection error: {exc}"
        ) from exc

    finally:

        API_STATS[
            "last_response_time"
        ] = round(
            time.time() - started,
            3
        )

    API_STATS[
        "last_status"
    ] = response.status_code

    try:

        body = response.json()

    except Exception:

        body = {
            "raw":
                response.text[:5000]
        }

    if response.status_code != 200:

        message = (
            f"SportMonks HTTP "
            f"{response.status_code}: "
            f"{body}"
        )

        API_STATS[
            "last_error"
        ] = message

        raise RuntimeError(
            message
        )

    if isinstance(
        body,
        dict
    ):

        if body.get("error"):

            message = str(
                body.get("error")
            )

            API_STATS[
                "last_error"
            ] = message

            raise RuntimeError(
                message
            )

        if (
            body.get("message")
            and body.get("data") is None
        ):

            message = str(
                body.get("message")
            )

            API_STATS[
                "last_error"
            ] = message

            raise RuntimeError(
                message
            )

    API_STATS[
        "last_error"
    ] = None

    return body


# =========================================================
# ODDS EXTRACTION
# =========================================================

def extract_odds_from_fixture(
    fixture
):

    raw = fixture.get(
        "odds"
    )

    odds = as_list(
        raw
    )

    return [
        x
        for x in odds
        if isinstance(x, dict)
    ]


def normalize_odd(odd):

    value = safe_float(
        odd.get("value")
    )

    if value is None:
        value = safe_float(
            odd.get("odd")
        )

    if (
        value is None
        or value <= 1
    ):
        return None

    market = (
        odd.get("market")
        or {}
    )

    bookmaker = (
        odd.get("bookmaker")
        or {}
    )

    market_id = (
        odd.get("market_id")
        or market.get("id")
    )

    market_name = (
        market.get("name")
        or odd.get(
            "market_description"
        )
        or odd.get(
            "market_name"
        )
        or "Market"
    )

    bookmaker_id = (
        odd.get("bookmaker_id")
        or bookmaker.get("id")
    )

    bookmaker_name = (
        bookmaker.get("name")
        or odd.get(
            "bookmaker_name"
        )
        or ""
    )

    label = str(
        odd.get("label")
        or odd.get("name")
        or ""
    ).strip()

    # Client-side bookmaker filter.
    if (
        SPORTMONKS_BOOKMAKERS
        and str(bookmaker_id)
        not in SPORTMONKS_BOOKMAKERS
    ):
        return None

    # Client-side market filter.
    if (
        SPORTMONKS_MARKETS
        and str(market_id)
        not in SPORTMONKS_MARKETS
    ):
        return None

    return {

        "id":
            odd.get("id"),

        "fixture_id":
            odd.get("fixture_id"),

        "market_id":
            market_id,

        "market":
            market_name,

        "market_description":
            odd.get(
                "market_description"
            )
            or market.get(
                "description"
            )
            or "",

        "bookmaker_id":
            bookmaker_id,

        "bookmaker":
            bookmaker_name,

        "label":
            label,

        "name":
            str(
                odd.get("name")
                or label
            ),

        "odd":
            value,

        "value":
            value,

        "probability":
            safe_float(
                odd.get(
                    "probability"
                )
            ),

        "total":
            odd.get(
                "total"
            ),

        "handicap":
            odd.get(
                "handicap"
            ),

        "participants":
            odd.get(
                "participants"
            ),

        "last_update":
            (
                odd.get(
                    "latest_bookmaker_update"
                )
                or odd.get(
                    "last_update"
                )
                or odd.get(
                    "updated_at"
                )
            ),
    }


def normalized_odds(
    fixture
):

    result = []

    for raw in extract_odds_from_fixture(
        fixture
    ):

        item = normalize_odd(
            raw
        )

        if item:
            result.append(
                item
            )

    return result


# =========================================================
# MARKET DETECTION
# =========================================================

def market_key(odd):

    text = (
        str(
            odd.get("market")
            or ""
        )
        + " "
        + str(
            odd.get(
                "market_description"
            )
            or ""
        )
    ).lower()

    text = text.replace(
        "_",
        " "
    )

    if (
        "both teams" in text
        or "btts" in text
        or "both to score" in text
    ):
        return "btts"

    if (
        "over/under" in text
        or "over under" in text
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
        or "fulltime result" in text
        or "fulltime" in text
        or "1x2" in text
    ):
        return "h2h"

    return "other"


# =========================================================
# H2H
# =========================================================

def choose_best_h2h(
    odds,
    home,
    away
):

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

        if (
            label == home
            or low == "home"
            or low == "1"
        ):

            key = "home"

        elif (
            label == away
            or low == "away"
            or low == "2"
        ):

            key = "away"

        elif low in (
            "draw",
            "x"
        ):

            key = "draw"

        else:

            continue

        if (
            key not in result
            or odd["odd"]
            > result[key]["odd"]
        ):

            result[key] = odd

    return result


# =========================================================
# TOTALS
# =========================================================

def choose_main_totals(
    odds
):

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
            or odd["odd"]
            > result[key]["odd"]
        ):

            result[key] = odd

    return result


# =========================================================
# BTTS
# =========================================================

def choose_btts(
    odds
):

    result = {}

    for odd in odds:

        if market_key(odd) != "btts":
            continue

        label = str(
            odd.get("label")
            or odd.get("name")
            or ""
        ).lower().strip()

        if label in (
            "yes",
            "btts yes"
        ):

            key = "yes"

        elif label in (
            "no",
            "btts no"
        ):

            key = "no"

        else:

            continue

        if (
            key not in result
            or odd["odd"]
            > result[key]["odd"]
        ):

            result[key] = odd

    return result


# =========================================================
# BEST BET
# =========================================================

def calculate_best_bet(
    odds,
    home,
    away
):

    candidates = []

    h2h = choose_best_h2h(
        odds,
        home,
        away
    )

    for key, selection in (
        ("home", "1"),
        ("draw", "X"),
        ("away", "2"),
    ):

        if h2h.get(key):

            x = h2h[key]

            candidates.append({
                "selection":
                    selection,

                "odd":
                    x["odd"],

                "market":
                    "1X2",

                "bookmaker":
                    x.get(
                        "bookmaker"
                    ),

                "bookmaker_id":
                    x.get(
                        "bookmaker_id"
                    ),

                "market_id":
                    x.get(
                        "market_id"
                    ),
            })

    totals = choose_main_totals(
        odds
    )

    for key, selection in (
        ("over", "Over"),
        ("under", "Under"),
    ):

        if totals.get(key):

            x = totals[key]

            line = (
                x.get("total")
                or ""
            )

            selection_text = (
                f"{selection} {line}"
                if line
                else selection
            )

            candidates.append({
                "selection":
                    selection_text,

                "odd":
                    x["odd"],

                "market":
                    "Over/Under",

                "bookmaker":
                    x.get(
                        "bookmaker"
                    ),

                "bookmaker_id":
                    x.get(
                        "bookmaker_id"
                    ),

                "market_id":
                    x.get(
                        "market_id"
                    ),
            })

    btts = choose_btts(
        odds
    )

    for key, selection in (
        ("yes", "BTTS Yes"),
        ("no", "BTTS No"),
    ):

        if btts.get(key):

            x = btts[key]

            candidates.append({
                "selection":
                    selection,

                "odd":
                    x["odd"],

                "market":
                    "BTTS",

                "bookmaker":
                    x.get(
                        "bookmaker"
                    ),

                "bookmaker_id":
                    x.get(
                        "bookmaker_id"
                    ),

                "market_id":
                    x.get(
                        "market_id"
                    ),
            })

    candidates = [
        x
        for x in candidates
        if (
            safe_float(
                x.get("odd")
            )
            and
            1.01
            < float(x["odd"])
            <= 100
        )
    ]

    if not candidates:
        return None

    # Lowest odd = strongest implied probability.
    return min(
        candidates,
        key=lambda x:
            float(x["odd"])
    )


# =========================================================
# FIXTURE CONVERSION
# =========================================================

def convert_fixture(
    fixture,
    external_odds=None
):

    fixture_id = fixture.get(
        "id"
    )

    home, away = extract_teams(
        fixture
    )

    league = extract_league(
        fixture
    )

    league_id = extract_league_id(
        fixture
    )

    commence = (
        fixture.get(
            "starting_at"
        )
        or fixture.get(
            "commence_time"
        )
    )

    if isinstance(
        commence,
        (int, float)
    ):

        commence = datetime.fromtimestamp(
            commence,
            timezone.utc
        ).isoformat()

    odds = normalized_odds(
        fixture
    )

    # Direct odds endpoint can override
    # incomplete fixture include.
    if external_odds is not None:

        odds = []

        for raw in external_odds:

            item = normalize_odd(
                raw
            )

            if item:
                odds.append(
                    item
                )

    h2h = choose_best_h2h(
        odds,
        home,
        away
    )

    totals = choose_main_totals(
        odds
    )

    btts = choose_btts(
        odds
    )

    return {

        "id":
            fixture_id,

        "fixture_id":
            fixture_id,

        "sport_key":
            "football",

        "league":
            league,

        "league_id":
            league_id,

        "home":
            home,

        "away":
            away,

        "time":
            local_time_text(
                commence
            ),

        "commence_time":
            commence,

        "has_odds":
            bool(
                fixture.get(
                    "has_odds"
                )
            ),

        "h2h": {

            "home":
                h2h["home"]["odd"]
                if h2h.get("home")
                else None,

            "draw":
                h2h["draw"]["odd"]
                if h2h.get("draw")
                else None,

            "away":
                h2h["away"]["odd"]
                if h2h.get("away")
                else None,
        },

        "totals": {

            "over":
                totals["over"]["odd"]
                if totals.get("over")
                else None,

            "under":
                totals["under"]["odd"]
                if totals.get("under")
                else None,
        },

        "btts": {

            "yes":
                btts["yes"]["odd"]
                if btts.get("yes")
                else None,

            "no":
                btts["no"]["odd"]
                if btts.get("no")
                else None,
        },

        "spreads": [

            {
                "name":
                    o.get("label"),

                "point":
                    o.get(
                        "handicap"
                    ),

                "price":
                    o.get("odd"),

                "market_id":
                    o.get(
                        "market_id"
                    ),

                "bookmaker":
                    o.get(
                        "bookmaker"
                    ),
            }

            for o in odds
            if market_key(o)
            == "spreads"
        ],

        "odds":
            odds,

        "odds_count":
            len(odds),

        "best_bet":
            calculate_best_bet(
                odds,
                home,
                away
            ),

        "state":
            fixture.get(
                "state"
            ),

        "starting_at_timestamp":
            fixture.get(
                "starting_at_timestamp"
            ),
    }


# =========================================================
# FIXTURE REQUEST
# =========================================================

def fetch_fixture_page(
    start_date,
    end_date,
    page=1
):

    params = {

        "include":
            "participants;league;state;odds;odds.market;odds.bookmaker",

        "page":
            page,

        "per_page":
            50,

        "order":
            "asc",
    }

    # IMPORTANT:
    # We DO NOT send bookmakers/markets
    # as invalid top-level parameters.
    #
    # We fetch broad coverage and filter
    # odds locally.
    #
    # SportMonks documents bookmakers/markets
    # as filters on odds. The local filter here
    # is safer when environment variables are empty.
    return sportmonks_request(
        f"/fixtures/between/{start_date}/{end_date}",
        params
    )


# =========================================================
# DIRECT PRE-MATCH ODDS
# =========================================================

def fetch_fixture_odds(
    fixture_id
):

    try:

        body = sportmonks_request(
            f"/odds/pre-match/fixtures/{fixture_id}",
            {
                "include":
                    "bookmaker;market"
            }
        )

        return as_list(
            body
        )

    except Exception as exc:

        print(
            "[ODDS DETAIL ERROR]",
            fixture_id,
            repr(exc)
        )

        return []


# =========================================================
# FETCH ALL MATCHES
# =========================================================

def fetch_matches_from_api():

    now = datetime.now(
        timezone.utc
    )

    # Use Ethiopia date for UI.
    local_now = now.astimezone(
        ETHIOPIA_TZ
    )

    local_end = (
        local_now
        + timedelta(
            days=DAYS_AHEAD - 1
        )
    )

    start_date = (
        local_now.strftime(
            "%Y-%m-%d"
        )
    )

    end_date = (
        local_end.strftime(
            "%Y-%m-%d"
        )
    )

    print(
        "[SPORTMONKS DATE]",
        start_date,
        "->",
        end_date
    )

    fixtures = []

    page = 1

    while page <= MAX_FIXTURE_PAGES:

        body = fetch_fixture_page(
            start_date,
            end_date,
            page
        )

        data = as_list(
            body
        )

        print(
            "[FIXTURE PAGE]",
            page,
            "COUNT:",
            len(data)
        )

        if not data:
            break

        fixtures.extend(
            data
        )

        pagination = (
            body.get(
                "pagination"
            )
            if isinstance(
                body,
                dict
            )
            else None
        )

        if not isinstance(
            pagination,
            dict
        ):
            break

        has_more = pagination.get(
            "has_more"
        )

        if has_more is False:
            break

        next_page = pagination.get(
            "next_page"
        )

        if next_page:

            try:
                page = int(
                    next_page
                )
            except Exception:
                page += 1

        else:

            page += 1

    # =====================================================
    # FILTER VALID UPCOMING FIXTURES
    # =====================================================

    valid = []

    for fixture in fixtures:

        try:

            commence = (
                fixture.get(
                    "starting_at"
                )
                or fixture.get(
                    "commence_time"
                )
            )

            dt = parse_iso_datetime(
                commence
            )

            if (
                not dt
                and fixture.get(
                    "starting_at_timestamp"
                )
            ):

                dt = datetime.fromtimestamp(
                    float(
                        fixture[
                            "starting_at_timestamp"
                        ]
                    ),
                    timezone.utc
                )

            if not dt:
                continue

            if dt < now:
                continue

            # End inclusive.
            if (
                dt
                > now
                + timedelta(
                    days=DAYS_AHEAD
                )
            ):
                continue

            valid.append(
                fixture
            )

        except Exception as exc:

            print(
                "[FIXTURE FILTER ERROR]",
                repr(exc)
            )

    # =====================================================
    # UNIQUE
    # =====================================================

    unique = {}

    for fixture in valid:

        fixture_id = fixture.get(
            "id"
        )

        if fixture_id is not None:

            unique[
                str(fixture_id)
            ] = fixture

    fixtures = list(
        unique.values()
    )

    API_STATS[
        "fixture_count"
    ] = len(fixtures)

    # =====================================================
    # CONVERT INITIAL FIXTURES
    # =====================================================

    result = []

    for fixture in fixtures:

        try:

            match = convert_fixture(
                fixture
            )

            result.append(
                match
            )

        except Exception as exc:

            print(
                "[CONVERT ERROR]",
                repr(exc)
            )

    # =====================================================
    # DIRECT ODDS FALLBACK
    #
    # If fixture include does not contain odds,
    # use official pre-match odds endpoint.
    # =====================================================

    missing_odds = [
        m
        for m in result
        if (
            m.get("id") is not None
            and m.get("odds_count", 0) == 0
            and (
                m.get("has_odds")
                or True
            )
        )
    ]

    if (
        DETAIL_REFRESH_ODDS
        and MAX_ODDS_DETAIL_REQUESTS > 0
        and missing_odds
    ):

        targets = missing_odds[
            :MAX_ODDS_DETAIL_REQUESTS
        ]

        print(
            "[DIRECT ODDS FALLBACK]",
            len(targets)
        )

        odds_map = {}

        with ThreadPoolExecutor(
            max_workers=ODDS_WORKERS
        ) as executor:

            futures = {
                executor.submit(
                    fetch_fixture_odds,
                    m["id"]
                ):
                    m["id"]

                for m in targets
            }

            for future in as_completed(
                futures
            ):

                fixture_id = futures[
                    future
                ]

                try:

                    odds_map[
                        str(fixture_id)
                    ] = future.result()

                except Exception as exc:

                    print(
                        "[ODDS FUTURE ERROR]",
                        fixture_id,
                        repr(exc)
                    )

        API_STATS[
            "direct_odds_requests"
        ] += len(targets)

        # Rebuild matches with external odds.
        rebuilt = []

        for m in result:

            fixture_id = m.get(
                "id"
            )

            if str(fixture_id) in odds_map:

                # Find original fixture.
                original = next(
                    (
                        f
                        for f in fixtures
                        if str(
                            f.get("id")
                        )
                        ==
                        str(fixture_id)
                    ),
                    None
                )

                if original:

                    m = convert_fixture(
                        original,
                        odds_map[
                            str(fixture_id)
                        ]
                    )

            rebuilt.append(
                m
            )

        result = rebuilt

    # =====================================================
    # SORT
    # =====================================================

    result.sort(
        key=lambda x:
            x.get(
                "commence_time"
            )
            or ""
    )

    API_STATS[
        "fixtures_with_odds"
    ] = sum(
        1
        for x in result
        if x.get(
            "odds_count",
            0
        ) > 0
    )

    API_STATS[
        "odds_count"
    ] = sum(
        x.get(
            "odds_count",
            0
        )
        for x in result
    )

    print(
        "[TOTAL FIXTURES]",
        len(result)
    )

    print(
        "[FIXTURES WITH ODDS]",
        API_STATS[
            "fixtures_with_odds"
        ]
    )

    print(
        "[TOTAL ODDS]",
        API_STATS[
            "odds_count"
        ]
    )

    return result, []


# =========================================================
# CACHE
# =========================================================

def refresh_matches(
    force=False
):

    if not REFRESH_LOCK.acquire(
        blocking=False
    ):
        return False

    try:

        with CACHE_LOCK:

            MATCH_CACHE[
                "refreshing"
            ] = True

        try:

            matches, errors = (
                fetch_matches_from_api()
            )

            with CACHE_LOCK:

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
                    else None
                )

                MATCH_CACHE[
                    "refreshing"
                ] = False

                API_STATS[
                    "last_refresh"
                ] = iso_z(
                    datetime.now(
                        timezone.utc
                    )
                )

                INITIAL_CACHE_EVENT.set()

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

                MATCH_CACHE[
                    "refreshing"
                ] = False

                INITIAL_CACHE_EVENT.set()

            return False

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

        threading.Thread(
            target=refresh_matches,
            args=(force,),
            daemon=True,
            name="sportmonks-refresh",
        ).start()

    return True


def get_matches(
    force=False
):

    now_ts = time.time()

    with CACHE_LOCK:

        cache_time = MATCH_CACHE[
            "time"
        ]

        matches = list(
            MATCH_CACHE[
                "matches"
            ]
        )

        refreshing = MATCH_CACHE[
            "refreshing"
        ]

    fresh = (
        cache_time > 0
        and
        now_ts - cache_time
        < CACHE_SECONDS
    )

    if not force and fresh:

        return matches

    if not refreshing:

        start_refresh_background(
            force
        )

    if (
        not matches
        and
        INITIAL_WAIT_SECONDS > 0
    ):

        INITIAL_CACHE_EVENT.wait(
            INITIAL_WAIT_SECONDS
        )

        with CACHE_LOCK:

            matches = list(
                MATCH_CACHE[
                    "matches"
                ]
            )

    return matches


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
                )
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
                "ℹ️ HOW TO PLAY",
                callback_data="how"
            ),
        ],
    ])


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    get_user(
        user.id,
        user.first_name or "User"
    )

    await update.message.reply_text(

        f"👋 Baga nagaan dhuftan "
        f"*{user.first_name}*!\n\n"

        "🎯 *BEST BET*\n"
        "⚽ Football\n"
        "🏆 Major Leagues\n"
        "📅 Today → Next 7 Days\n"
        "📊 SportMonks Real Odds\n"
        "🎟️ Bet Slip\n\n"

        "👇 *⚽ FOOTBALL* cuqaasi.",

        reply_markup=main_menu(),

        parse_mode="Markdown",
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    u = get_user(
        user.id,
        user.first_name or "User"
    )

    if query.data == "profile":

        text = (
            "👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: *{u['balance']:.2f}*\n"
            f"Bet Slip: *{len(u['betslip'])}*"
        )

    elif query.data == "balance":

        text = (
            "💳 *BALANCE*\n\n"
            f"Balance: *{u['balance']:.2f}*\n\n"
            "🧪 Demo system qofa."
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
                    f"🕐 "
                    f"{item.get('time','')}\n"

                    f"💰 "
                    f"{item.get('stake',0):.2f} | "

                    f"📈 "
                    f"{item.get('odds',0):.2f} | "

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

            "🏆 Premier League, LaLiga, "
            "Serie A, Bundesliga, Ligue 1 "
            "fi liigota biroo subscription "
            "kee keessatti jiran agarsiisa.\n\n"

            "🧪 Demo/testing qofa."
        )

    await query.edit_message_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# =========================================================
# FLASK ROUTES
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def index():

    return render_template(
        "index.html"
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    with CACHE_LOCK:

        cache_time = MATCH_CACHE[
            "time"
        ]

        count = len(
            MATCH_CACHE[
                "matches"
            ]
        )

        refreshing = MATCH_CACHE[
            "refreshing"
        ]

        error = MATCH_CACHE[
            "error"
        ]

    return jsonify({

        "status":
            "online",

        "bot":
            "Best Bet",

        "api":
            "SportMonks Football API",

        "api_key_configured":
            bool(
                SPORTMONKS_API_TOKEN
            ),

        "web_app":
            WEB_APP_URL,

        "days":
            DAYS_AHEAD,

        "markets":
            SPORTMONKS_MARKETS
            or "ALL_AVAILABLE",

        "bookmakers":
            SPORTMONKS_BOOKMAKERS
            or "ALL_AVAILABLE",

        "matches_cached":
            count,

        "cache_age_seconds":
            (
                round(
                    time.time()
                    - cache_time,
                    1
                )
                if cache_time
                else None
            ),

        "refreshing":
            refreshing,

        "cache_error":
            error,

        "api_last_status":
            API_STATS[
                "last_status"
            ],

        "api_last_error":
            API_STATS[
                "last_error"
            ],

        "last_refresh":
            API_STATS[
                "last_refresh"
            ],

        "fixture_count":
            API_STATS[
                "fixture_count"
            ],

        "fixtures_with_odds":
            API_STATS[
                "fixtures_with_odds"
            ],

        "odds_count":
            API_STATS[
                "odds_count"
            ],

        "direct_odds_requests":
            API_STATS[
                "direct_odds_requests"
            ],
    })


# =========================================================
# DEBUG API
# =========================================================

@app.route(
    "/api/test",
    methods=["GET"]
)
def api_test():

    return jsonify({

        "success":
            True,

        "message":
            "BEST BET API is working.",

        "api":
            "SportMonks",

        "api_key_configured":
            bool(
                SPORTMONKS_API_TOKEN
            ),

        "time":
            iso_z(
                datetime.now(
                    timezone.utc
                )
            ),
    })


@app.route(
    "/api/odds-test",
    methods=["GET"]
)
def odds_test():

    try:

        now = datetime.now(
            timezone.utc
        )

        body = fetch_fixture_page(
            date_text_utc(now),
            date_text_utc(
                now + timedelta(days=1)
            ),
            1
        )

        fixtures = as_list(
            body
        )

        samples = []

        for fixture in fixtures[:10]:

            converted = convert_fixture(
                fixture
            )

            samples.append({

                "fixture_id":
                    converted["id"],

                "home":
                    converted["home"],

                "away":
                    converted["away"],

                "league":
                    converted["league"],

                "has_odds":
                    converted[
                        "has_odds"
                    ],

                "odds_count":
                    converted[
                        "odds_count"
                    ],

                "sample_odds":
                    converted[
                        "odds"
                    ][:20],
            })

        return jsonify({

            "success":
                True,

            "api":
                "SportMonks",

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "fixture_count":
                len(fixtures),

            "samples":
                samples,

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

            "success":
                False,

            "error":
                str(exc),

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "api_last_status":
                API_STATS[
                    "last_status"
                ],

            "api_last_error":
                API_STATS[
                    "last_error"
                ],
        })


# =========================================================
# MATCHES
# =========================================================

@app.route(
    "/api/matches",
    methods=["GET"]
)
def api_matches():

    force = (
        request.args.get(
            "refresh",
            "0"
        )
        == "1"
    )

    try:

        matches = get_matches(
            force
        )

        with CACHE_LOCK:

            refreshing = MATCH_CACHE[
                "refreshing"
            ]

            error = MATCH_CACHE[
                "error"
            ]

            cache_time = MATCH_CACHE[
                "time"
            ]

        return jsonify({

            "success":
                True,

            "count":
                len(matches),

            "matches":
                matches,

            "message":
                (
                    "Football matches loaded."
                    if matches
                    else
                    "No fixtures returned by SportMonks."
                ),

            "loading":
                refreshing
                and not bool(matches),

            "stale":
                (
                    bool(matches)
                    and
                    cache_time > 0
                    and
                    time.time()
                    - cache_time
                    >= CACHE_SECONDS
                ),

            "api":
                "SportMonks",

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "api_status":
                API_STATS[
                    "last_status"
                ],

            "api_error":
                error,

            "fixture_count":
                API_STATS[
                    "fixture_count"
                ],

            "fixtures_with_odds":
                API_STATS[
                    "fixtures_with_odds"
                ],

            "odds_count":
                API_STATS[
                    "odds_count"
                ],
        })

    except Exception as exc:

        return jsonify({

            "success":
                False,

            "count":
                0,

            "matches":
                [],

            "error":
                str(exc),

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "api_status":
                API_STATS[
                    "last_status"
                ],

            "api_last_error":
                API_STATS[
                    "last_error"
                ],
        })


# =========================================================
# MATCH DETAIL
# =========================================================

@app.route(
    "/api/match/<match_id>",
    methods=["GET"]
)
def api_match(
    match_id
):

    try:

        matches = get_matches()

        match = next(
            (
                m
                for m in matches
                if str(
                    m.get("id")
                )
                ==
                str(match_id)
            ),
            None
        )

        converted = None

        # Always try the direct fixture endpoint
        # for a selected match.
        if DETAIL_REFRESH_ODDS:

            body = sportmonks_request(

                f"/fixtures/{match_id}",

                {
                    "include":
                        "participants;league;state;"
                        "odds;odds.market;odds.bookmaker"
                }
            )

            if isinstance(
                body.get("data"),
                dict
            ):

                fixture = body[
                    "data"
                ]

                converted = convert_fixture(
                    fixture
                )

                # If fixture include has no odds,
                # use official pre-match endpoint.
                if (
                    converted.get(
                        "odds_count",
                        0
                    ) == 0
                ):

                    external_odds = (
                        fetch_fixture_odds(
                            match_id
                        )
                    )

                    if external_odds:

                        converted = convert_fixture(
                            fixture,
                            external_odds
                        )

        if converted is None:

            converted = match

        if converted is None:

            return jsonify({

                "success":
                    False,

                "error":
                    "Match hin argamne.",

                "markets":
                    [],
            })

        # =================================================
        # GROUP MARKETS
        # =================================================

        grouped = {}

        for odd in converted.get(
            "odds",
            []
        ):

            key = (

                odd.get(
                    "market_id"
                ),

                odd.get(
                    "market"
                )
                or odd.get(
                    "market_description"
                )
                or "Market",
            )

            grouped.setdefault(
                key,
                []
            ).append(
                odd
            )

        markets = []

        for (
            market_id,
            market_name
        ), odds in grouped.items():

            selections = []

            for odd in odds:

                label = (
                    odd.get(
                        "label"
                    )
                    or odd.get(
                        "name"
                    )
                )

                # Add total / handicap line.
                if (
                    odd.get("total")
                    and
                    str(
                        odd.get("market")
                        or ""
                    ).lower()
                    != "match winner"
                ):

                    label = (
                        f"{label} "
                        f"{odd.get('total')}"
                    )

                if (
                    odd.get(
                        "handicap"
                    ) is not None
                    and
                    str(
                        odd.get("market")
                        or ""
                    ).lower()
                    != "match winner"
                ):

                    label = (
                        f"{label} "
                        f"({odd.get('handicap')})"
                    )

                selections.append({

                    "value":
                        label,

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

                key=lambda x:
                    safe_float(
                        x.get("odd")
                    )
                    or 0,

                reverse=True
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
            key=lambda x:
                str(
                    x["name"]
                ).lower()
        )

        return jsonify({

            "success":
                True,

            "match":
                converted,

            "markets":
                markets,

            "best_bet":
                converted.get(
                    "best_bet"
                ),

            "odds_count":
                len(
                    converted.get(
                        "odds",
                        []
                    )
                ),

            "api":
                "SportMonks",

            "api_status":
                API_STATS[
                    "last_status"
                ],
        })

    except Exception as exc:

        print(
            "[MATCH ERROR]",
            repr(exc)
        )

        return jsonify({

            "success":
                False,

            "error":
                str(exc),

            "markets":
                [],
        })


# =========================================================
# LIVE
# =========================================================

@app.route(
    "/api/live",
    methods=["GET"]
)
def api_live():

    try:

        # ALL livescores, not "latest updated".
        body = sportmonks_request(
            "/livescores",
            {
                "include":
                    "participants;league;state;scores"
            }
        )

        scores = as_list(
            body
        )

        result = []

        for event in scores:

            home, away = extract_teams(
                event
            )

            home_score = None
            away_score = None

            for score in as_list(
                event.get("scores")
            ):

                if not isinstance(
                    score,
                    dict
                ):
                    continue

                value = score.get(
                    "score"
                )

                if isinstance(
                    value,
                    dict
                ):

                    value = (
                        value.get(
                            "goals"
                        )
                        or value.get(
                            "current"
                        )
                    )

                participant = str(
                    score.get(
                        "participant"
                    )
                    or score.get(
                        "participant_id"
                    )
                    or score.get(
                        "participant_name"
                    )
                    or ""
                ).lower()

                if (
                    participant
                    in (
                        "home",
                        str(
                            home
                        ).lower()
                    )
                ):

                    home_score = value

                elif (
                    participant
                    in (
                        "away",
                        str(
                            away
                        ).lower()
                    )
                ):

                    away_score = value

            result.append({

                "id":
                    event.get(
                        "id"
                    ),

                "league":
                    extract_league(
                        event
                    ),

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
                    event.get(
                        "state"
                    ),
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

            "matches":
                [],
        })


# =========================================================
# ERRORS
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
            Application
            .builder()
            .token(
                BOT_TOKEN
            )
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
            "BEST BET TELEGRAM BOT ONLINE"
        )

        print(
            "WEB APP:",
            WEB_APP_URL
        )

        application.run_polling(
            allowed_updates=
                Update.ALL_TYPES,
            close_loop=False,
        )

    except Exception as exc:

        print(
            "[BOT ERROR]",
            repr(exc)
        )


# =========================================================
# CACHE LOOP
# =========================================================

def cache_refresh_loop():

    time.sleep(3)

    while True:

        try:

            with CACHE_LOCK:

                cache_time = (
                    MATCH_CACHE[
                        "time"
                    ]
                )

                refreshing = (
                    MATCH_CACHE[
                        "refreshing"
                    ]
                )

            expired = (

                cache_time == 0

                or

                time.time()
                - cache_time
                >= CACHE_SECONDS
            )

            if (
                expired
                and
                not refreshing
            ):

                start_refresh_background()

        except Exception as exc:

            print(
                "[CACHE LOOP ERROR]",
                repr(exc)
            )

        time.sleep(20)


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 55)

    print(
        "       BEST BET - SPORTMONKS V7"
    )

    print("=" * 55)

    print(
        "WEB APP:",
        WEB_APP_URL
    )

    print(
        "SPORTMONKS TOKEN:",
        bool(
            SPORTMONKS_API_TOKEN
        )
    )

    print(
        "DAYS:",
        DAYS_AHEAD
    )

    print(
        "MARKETS:",
        SPORTMONKS_MARKETS
        or "ALL AVAILABLE"
    )

    print(
        "BOOKMAKERS:",
        SPORTMONKS_BOOKMAKERS
        or "ALL AVAILABLE"
    )

    print(
        "CACHE:",
        CACHE_SECONDS
    )

    print(
        "ODDS DETAIL:",
        DETAIL_REFRESH_ODDS
    )

    print(
        "MAX ODDS DETAIL:",
        MAX_ODDS_DETAIL_REQUESTS
    )

    print(
        "PORT:",
        PORT
    )

    print("=" * 55)

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
