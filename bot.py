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

# ---------------------------------------------------------
# SPORTMONKS
# ---------------------------------------------------------

SPORTMONKS_API_TOKEN = os.getenv(
    "SPORTMONKS_API_TOKEN",
    os.getenv("SPORTMONKS_API_KEY", "")
).strip()

SPORTMONKS_BASE = "https://api.sportmonks.com/v3/football"

# ---------------------------------------------------------
# WEB APP
# ---------------------------------------------------------

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com"
).strip().rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

# ---------------------------------------------------------
# DATE / CACHE
# ---------------------------------------------------------

DAYS_AHEAD = max(
    1,
    int(os.getenv("DAYS_AHEAD", "7"))
)

API_TIMEOUT = max(
    5,
    int(os.getenv("API_TIMEOUT", "25"))
)

CACHE_SECONDS = max(
    30,
    int(os.getenv("CACHE_SECONDS", "60"))
)

INITIAL_WAIT_SECONDS = max(
    0,
    int(os.getenv("INITIAL_WAIT_SECONDS", "15"))
)

MAX_FIXTURE_PAGES = max(
    1,
    int(os.getenv("MAX_FIXTURE_PAGES", "10"))
)

DETAIL_REFRESH_ODDS = (
    os.getenv("DETAIL_REFRESH_ODDS", "1").strip() == "1"
)

# =========================================================
# MAJOR FOOTBALL LEAGUES
# =========================================================
#
# SportMonks league IDs
#
# Premier League       = 8
# Bundesliga            = 82
# Serie A               = 384
# LaLiga                = 564
# Ligue 1               = 301
# Champions League      = 2
# Europa League         = 3
#
# =========================================================

MAJOR_LEAGUES = {
    "8": {
        "id": 8,
        "name": "Premier League",
        "country": "England",
        "short": "ENG",
    },

    "564": {
        "id": 564,
        "name": "LaLiga",
        "country": "Spain",
        "short": "ESP",
    },

    "384": {
        "id": 384,
        "name": "Serie A",
        "country": "Italy",
        "short": "ITA",
    },

    "82": {
        "id": 82,
        "name": "Bundesliga",
        "country": "Germany",
        "short": "GER",
    },

    "301": {
        "id": 301,
        "name": "Ligue 1",
        "country": "France",
        "short": "FRA",
    },

    "2": {
        "id": 2,
        "name": "UEFA Champions League",
        "country": "Europe",
        "short": "UCL",
    },

    "3": {
        "id": 3,
        "name": "UEFA Europa League",
        "country": "Europe",
        "short": "UEL",
    },
}

# ---------------------------------------------------------
# Optional environment league list
#
# Example:
# SPORTMONKS_LEAGUES=8,564,384,82,301,2,3
#
# ---------------------------------------------------------

_env_leagues = os.getenv(
    "SPORTMONKS_LEAGUES",
    ""
).strip()

if _env_leagues:
    requested_ids = [
        x.strip()
        for x in _env_leagues.split(",")
        if x.strip()
    ]

    SELECTED_LEAGUES = {
        k: v
        for k, v in MAJOR_LEAGUES.items()
        if k in requested_ids
    }

    if not SELECTED_LEAGUES:
        SELECTED_LEAGUES = MAJOR_LEAGUES.copy()
else:
    SELECTED_LEAGUES = MAJOR_LEAGUES.copy()


# ---------------------------------------------------------
# BOOKMAKERS / MARKETS
# ---------------------------------------------------------

SPORTMONKS_BOOKMAKERS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_BOOKMAKERS",
        ""
    ).split(",")
    if x.strip()
]

SPORTMONKS_MARKETS = [
    x.strip()
    for x in os.getenv(
        "SPORTMONKS_MARKETS",
        ""
    ).split(",")
    if x.strip()
]

ETHIOPIA_TZ = timezone(
    timedelta(hours=3)
)

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

    for i, item in enumerate(slips, 1):

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

        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        ).astimezone(
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

    if isinstance(participants, dict):
        participants = as_list(
            participants
        )

    home = ""
    away = ""

    for p in participants:

        meta = p.get("meta") or {}

        loc = str(
            meta.get("location", "")
        ).lower()

        name = participant_name(p)

        if loc == "home":
            home = name

        elif loc == "away":
            away = name

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


def extract_league_id(fixture):

    league = fixture.get("league")

    if isinstance(league, dict):

        return (
            league.get("id")
            or fixture.get("league_id")
        )

    return fixture.get(
        "league_id"
    )


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


def get_league_info(league_id, name=""):

    if league_id is not None:

        info = SELECTED_LEAGUES.get(
            str(league_id)
        )

        if info:
            return info

    lower = str(name).lower()

    for info in SELECTED_LEAGUES.values():

        if (
            info["name"].lower()
            == lower
        ):
            return info

    return {
        "id": league_id,
        "name": name or "Football",
        "country": "",
        "short": "",
    }

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
            "Render > Environment Variables keessatti "
            "SportMonks token galchi."
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
                response.text[:3000]
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

    if isinstance(body, dict):

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
# ODDS
# =========================================================

def extract_odds_from_fixture(
    fixture
):

    raw = fixture.get("odds")

    odds = as_list(raw)

    if (
        not odds
        and isinstance(raw, dict)
    ):

        odds = raw.get(
            "data"
        ) or []

    return [
        x
        for x in odds
        if isinstance(x, dict)
    ]


def normalize_odd(odd):

    value = safe_float(
        odd.get("value")
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
        or ""
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

    # -----------------------------------------------------
    # Optional bookmaker filtering
    # -----------------------------------------------------

    if (
        SPORTMONKS_BOOKMAKERS
        and str(bookmaker_id)
        not in SPORTMONKS_BOOKMAKERS
    ):
        return None

    # -----------------------------------------------------
    # Optional market filtering
    # -----------------------------------------------------

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
            (
                odd.get(
                    "market_description"
                )
                or market.get(
                    "description"
                )
                or ""
            ),

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

        "last_update":
            (
                odd.get(
                    "last_update"
                )
                or odd.get(
                    "updated_at"
                )
            ),
    }


def normalized_odds(fixture):

    result = []

    for raw in extract_odds_from_fixture(
        fixture
    ):

        item = normalize_odd(
            raw
        )

        if item:
            result.append(item)

    return result


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
        or text.strip()
        in (
            "match winner",
            "1x2",
            "fulltime result",
        )
    ):
        return "h2h"

    # SportMonks markets can sometimes
    # have generic names. Try labels.
    label = str(
        odd.get("label")
        or odd.get("name")
        or ""
    ).lower()

    if label in (
        "home",
        "draw",
        "away",
        "1",
        "x",
        "2",
    ):
        return "h2h"

    return "other"


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
            or low == str(
                home
            ).lower()
            or low == "home"
            or low == "1"
        ):
            key = "home"

        elif (
            label == away
            or low == str(
                away
            ).lower()
            or low == "away"
            or low == "2"
        ):
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
            or odd["odd"]
            > result[key]["odd"]
        ):
            result[key] = odd

    return result


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

        key = (
            "over"
            if "over" in label
            else
            "under"
            if "under" in label
            else None
        )

        if (
            key
            and (
                key not in result
                or odd["odd"]
                > result[key]["odd"]
            )
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

        key = (
            "yes"
            if label in (
                "yes",
                "btts yes",
            )
            else
            "no"
            if label in (
                "no",
                "btts no",
            )
            else None
        )

        if (
            key
            and (
                key not in result
                or odd["odd"]
                > result[key]["odd"]
            )
        ):
            result[key] = odd

    return result


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

            candidates.append({
                "selection":
                    selection,

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

    return min(
        candidates,
        key=lambda x:
            float(x["odd"])
    )

# =========================================================
# FIXTURE CONVERSION
# =========================================================

def convert_fixture(
    fixture
):

    fixture_id = fixture.get(
        "id"
    )

    home, away = extract_teams(
        fixture
    )

    league_id = extract_league_id(
        fixture
    )

    league_name = extract_league(
        fixture
    )

    league_info = get_league_info(
        league_id,
        league_name
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

        "league_id":
            league_info.get(
                "id"
            ),

        "league":
            league_info.get(
                "name"
            ),

        "country":
            league_info.get(
                "country"
            ),

        "league_short":
            league_info.get(
                "short"
            ),

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

        "h2h": {
            "home":
                (
                    h2h["home"]["odd"]
                    if h2h.get("home")
                    else None
                ),

            "draw":
                (
                    h2h["draw"]["odd"]
                    if h2h.get("draw")
                    else None
                ),

            "away":
                (
                    h2h["away"]["odd"]
                    if h2h.get("away")
                    else None
                ),
        },

        "totals": {
            "over":
                (
                    totals["over"]["odd"]
                    if totals.get("over")
                    else None
                ),

            "under":
                (
                    totals["under"]["odd"]
                    if totals.get("under")
                    else None
                ),
        },

        "btts": {
            "yes":
                (
                    btts["yes"]["odd"]
                    if btts.get("yes")
                    else None
                ),

            "no":
                (
                    btts["no"]["odd"]
                    if btts.get("no")
                    else None
                ),
        },

        "spreads": [

            {
                "name":
                    o.get("label"),

                "point":
                    o.get("label"),

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
    }

# =========================================================
# FIXTURE API
# =========================================================

def fetch_fixture_page(
    start_date,
    end_date,
    page=1,
    league_id=None
):

    params = {

        "include":
            "participants;league;state;odds",

        "page":
            page,
    }

    # -----------------------------------------------------
    # IMPORTANT
    # Ask SportMonks for one league at a time.
    # -----------------------------------------------------

    if league_id is not None:

        params["filters"] = (
            f"fixtureLeagues:{league_id}"
        )

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
        f"{start_date}/"
        f"{end_date}",
        params,
    )

# =========================================================
# FETCH ONE LEAGUE
# =========================================================

def fetch_one_league(
    league_id,
    league_info,
    start_date,
    end_date
):

    fixtures = []

    page = 1

    print(
        "[LEAGUE START]",
        league_info["name"],
        league_id
    )

    while (
        page
        <= MAX_FIXTURE_PAGES
    ):

        try:

            body = fetch_fixture_page(
                start_date,
                end_date,
                page,
                league_id
            )

        except Exception as exc:

            print(
                "[LEAGUE ERROR]",
                league_info["name"],
                repr(exc)
            )

            # ------------------------------------------------
            # FALLBACK:
            # If league filter is rejected by the API,
            # fetch the date range without the filter.
            # Then local filtering will be done below.
            # ------------------------------------------------

            try:

                print(
                    "[LEAGUE FALLBACK]",
                    league_info["name"]
                )

                body = fetch_fixture_page(
                    start_date,
                    end_date,
                    page,
                    None
                )

            except Exception as fallback_exc:

                print(
                    "[FALLBACK ERROR]",
                    league_info["name"],
                    repr(fallback_exc)
                )

                break

        data = as_list(
            body
        )

        print(
            "[LEAGUE PAGE]",
            league_info["name"],
            page,
            "fixtures:",
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

        if (
            pagination.get(
                "has_more"
            )
            is False
        ):
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

    return fixtures

# =========================================================
# FETCH ALL MAJOR LEAGUES
# =========================================================

def fetch_matches_from_api():

    now = datetime.now(
        timezone.utc
    )

    end_time = (
        now
        + timedelta(
            days=DAYS_AHEAD
        )
    )

    start_date = date_text_utc(
        now
    )

    end_date = date_text_utc(
        end_time
    )

    print("=" * 65)

    print(
        "SPORTMONKS MAJOR LEAGUES"
    )

    print(
        "DATE:",
        start_date,
        "->",
        end_date
    )

    print(
        "DAYS:",
        DAYS_AHEAD
    )

    print(
        "LEAGUES:",
        ", ".join(
            x["name"]
            for x in SELECTED_LEAGUES.values()
        )
    )

    print("=" * 65)

    all_fixtures = []

    errors = []

    # -----------------------------------------------------
    # League by league
    # -----------------------------------------------------

    for league_id, league_info in (
        SELECTED_LEAGUES.items()
    ):

        try:

            fixtures = fetch_one_league(
                league_id,
                league_info,
                start_date,
                end_date
            )

            all_fixtures.extend(
                fixtures
            )

        except Exception as exc:

            error_text = (
                f"{league_info['name']}: "
                f"{exc}"
            )

            print(
                "[LEAGUE FAILED]",
                error_text
            )

            errors.append(
                error_text
            )

    # -----------------------------------------------------
    # Convert + date + league verification
    # -----------------------------------------------------

    result = []

    for fixture in all_fixtures:

        try:

            fixture_league_id = (
                extract_league_id(
                    fixture
                )
            )

            # ----------------------------------------------
            # Only selected leagues
            # ----------------------------------------------

            if (
                fixture_league_id is not None
                and
                str(
                    fixture_league_id
                )
                not in SELECTED_LEAGUES
            ):
                continue

            # ----------------------------------------------
            # Date
            # ----------------------------------------------

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

            if (
                dt < now
                or dt > end_time
            ):
                continue

            # ----------------------------------------------
            # If API did not give league ID,
            # use league name when possible.
            # ----------------------------------------------

            league_name = extract_league(
                fixture
            )

            league_info = get_league_info(
                fixture_league_id,
                league_name
            )

            # ----------------------------------------------
            # Protect against unknown league
            # ----------------------------------------------

            if (
                fixture_league_id is not None
                and
                str(
                    fixture_league_id
                )
                not in SELECTED_LEAGUES
            ):
                continue

            match = convert_fixture(
                fixture
            )

            match[
                "league_id"
            ] = (
                fixture_league_id
                or league_info.get("id")
            )

            match[
                "league"
            ] = (
                league_info.get(
                    "name"
                )
                or league_name
            )

            match[
                "country"
            ] = league_info.get(
                "country",
                ""
            )

            match[
                "league_short"
            ] = league_info.get(
                "short",
                ""
            )

            result.append(
                match
            )

            print(
                "[MATCH]",
                match["league"],
                "|",
                match["home"],
                "vs",
                match["away"],
                "|",
                match["time"],
                "| ODDS:",
                match["odds_count"]
            )

        except Exception as exc:

            print(
                "[FIXTURE ERROR]",
                repr(exc)
            )

    # -----------------------------------------------------
    # Remove duplicates
    # -----------------------------------------------------

    unique = {}

    for match in result:

        match_id = match.get(
            "id"
        )

        if match_id is not None:

            unique[
                str(match_id)
            ] = match

    result = list(
        unique.values()
    )

    # -----------------------------------------------------
    # Sort by date/time
    # -----------------------------------------------------

    result.sort(
        key=lambda x:
            x.get(
                "commence_time"
            )
            or ""
    )

    # -----------------------------------------------------
    # Statistics
    # -----------------------------------------------------

    print("=" * 65)

    print(
        "[TOTAL MATCHES]",
        len(result)
    )

    print(
        "[TOTAL ODDS]",
        sum(
            x.get(
                "odds_count",
                0
            )
            for x in result
        )
    )

    for league_id, info in (
        SELECTED_LEAGUES.items()
    ):

        count = sum(
            1
            for x in result
            if str(
                x.get(
                    "league_id"
                )
            )
            == str(league_id)
        )

        print(
            "[LEAGUE]",
            info["name"],
            ":",
            count
        )

    print("=" * 65)

    return result, errors

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

    if (
        not force
        and fresh
    ):
        return matches

    if not refreshing:

        start_refresh_background(
            force
        )

    if (
        not matches
        and INITIAL_WAIT_SECONDS > 0
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
# TELEGRAM
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

        "🏆 Premier League\n"
        "🇪🇸 LaLiga\n"
        "🇮🇹 Serie A\n"
        "🇩🇪 Bundesliga\n"
        "🇫🇷 Ligue 1\n"
        "🏆 Champions League\n\n"

        "📅 Today → Next 7 Days\n"
        "📊 SportMonks Odds\n"
        "🎟️ Bet Slip\n\n"

        "👇 *PLAY BEST BET* cuqaasi.",

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

            f"Balance: "
            f"*{u['balance']:.2f}*\n"

            f"Bet Slip: "
            f"*{len(u['betslip'])}*"

        )

    elif query.data == "balance":

        text = (
            "💳 *BALANCE*\n\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n\n"

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

            "3. 🏆 League filadhu\n"

            "4. ⚽ Match filadhu\n"

            "5. 📊 Market filadhu\n"

            "6. 🎯 Selection filadhu\n"

            "7. 🎟️ Bet Slip ilaali\n\n"

            "📅 Today → Next 7 Days\n"

            "📊 SportMonks irraa odds\n"

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

    league_counts = {}

    for match in MATCH_CACHE[
        "matches"
    ]:

        name = (
            match.get(
                "league"
            )
            or "Unknown"
        )

        league_counts[name] = (
            league_counts.get(
                name,
                0
            ) + 1
        )

    return jsonify({

        "status":
            "online",

        "bot":
            "Best Bet",

        "api":
            "Sportmonks Football API",

        "api_key_configured":
            bool(
                SPORTMONKS_API_TOKEN
            ),

        "web_app":
            WEB_APP_URL,

        "days":
            DAYS_AHEAD,

        "selected_leagues":
            list(
                SELECTED_LEAGUES.values()
            ),

        "markets":
            SPORTMONKS_MARKETS
            or "ALL_AVAILABLE",

        "bookmakers":
            SPORTMONKS_BOOKMAKERS
            or "ALL_AVAILABLE",

        "matches_cached":
            count,

        "league_counts":
            league_counts,

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

    })


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
            "Sportmonks",

        "api_key_configured":
            bool(
                SPORTMONKS_API_TOKEN
            ),

        "leagues":
            list(
                SELECTED_LEAGUES.values()
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

        end = now + timedelta(
            days=1
        )

        samples = []

        # ------------------------------------------------
        # Test every selected league
        # ------------------------------------------------

        for league_id, info in (
            SELECTED_LEAGUES.items()
        ):

            try:

                body = fetch_fixture_page(
                    date_text_utc(now),
                    date_text_utc(end),
                    1,
                    int(league_id)
                )

                fixtures = as_list(
                    body
                )

                for fixture in fixtures[:5]:

                    match = convert_fixture(
                        fixture
                    )

                    samples.append({

                        "league":
                            info["name"],

                        "league_id":
                            info["id"],

                        "fixture_id":
                            match["id"],

                        "home":
                            match["home"],

                        "away":
                            match["away"],

                        "time":
                            match["time"],

                        "odds_count":
                            match[
                                "odds_count"
                            ],

                        "sample_odds":
                            match[
                                "odds"
                            ][:20],
                    })

            except Exception as exc:

                samples.append({

                    "league":
                        info["name"],

                    "league_id":
                        info["id"],

                    "error":
                        str(exc),

                })

        return jsonify({

            "success":
                True,

            "api":
                "Sportmonks",

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

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

        }), 200


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

    league_filter = (
        request.args.get(
            "league",
            ""
        ).strip()
    )

    try:

        matches = get_matches(
            force
        )

        # ------------------------------------------------
        # Optional league filter
        # ------------------------------------------------

        if league_filter:

            filtered = []

            for match in matches:

                if (
                    str(
                        match.get(
                            "league_id"
                        )
                    )
                    == league_filter
                    or
                    str(
                        match.get(
                            "league"
                        )
                    ).lower()
                    ==
                    league_filter.lower()
                ):
                    filtered.append(
                        match
                    )

            matches = filtered

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

        # ------------------------------------------------
        # League summary
        # ------------------------------------------------

        league_counts = {}

        for match in matches:

            league = (
                match.get(
                    "league"
                )
                or "Football"
            )

            league_counts[
                league
            ] = (
                league_counts.get(
                    league,
                    0
                )
                + 1
            )

        return jsonify({

            "success":
                True,

            "count":
                len(matches),

            "matches":
                matches,

            "leagues":
                league_counts,

            "message":
                (
                    "Football matches loaded."
                    if matches
                    else
                    "Football fixtures hin argamne."
                ),

            "loading":
                (
                    refreshing
                    and not bool(
                        matches
                    )
                ),

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
                "Sportmonks",

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

        }), 200

# =========================================================
# SINGLE MATCH
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

        fixture = None

        # ------------------------------------------------
        # Always try direct detail when enabled
        # ------------------------------------------------

        if DETAIL_REFRESH_ODDS:

            body = sportmonks_request(

                f"/fixtures/"
                f"{match_id}",

                {
                    "include":
                        (
                            "participants;"
                            "league;"
                            "state;"
                            "odds"
                        )
                },
            )

            if isinstance(
                body.get("data"),
                dict
            ):

                fixture = body[
                    "data"
                ]

        if fixture:

            converted = convert_fixture(
                fixture
            )

        elif match:

            converted = match

        else:

            return jsonify({

                "success":
                    False,

                "error":
                    "Match hin argamne.",

                "markets":
                    [],
            })

        # ------------------------------------------------
        # Group odds by market
        # ------------------------------------------------

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
                or
                odd.get(
                    "market_description"
                )
                or
                "Market",

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

                selections.append({

                    "value":
                        (
                            odd.get(
                                "label"
                            )
                            or
                            odd.get(
                                "name"
                            )
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

                key=lambda x:
                    safe_float(
                        x.get(
                            "odd"
                        )
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
                "Sportmonks",

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

        }), 200

# =========================================================
# LIVE
# =========================================================

@app.route(
    "/api/live",
    methods=["GET"]
)
def api_live():

    try:

        body = sportmonks_request(

            "/livescores/latest",

            {
                "include":
                    (
                        "participants;"
                        "league;"
                        "state;"
                        "scores"
                    )
            },
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
                event.get(
                    "scores"
                )
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
                        or
                        value.get(
                            "current"
                        )
                    )

                who = str(

                    score.get(
                        "participant"
                    )
                    or
                    score.get(
                        "participant_id"
                    )
                    or
                    score.get(
                        "participant_name"
                    )
                    or ""

                ).lower()

                if who in (
                    "home",
                    str(
                        home
                    ).lower()
                ):

                    home_score = value

                elif who in (
                    "away",
                    str(
                        away
                    ).lower()
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

        }), 200

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
                (
                    "API endpoint not found: "
                    + request.path
                ),

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
                and not refreshing
            ):

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

    print("=" * 65)

    print(
        "       BEST BET - SPORTMONKS"
    )

    print("=" * 65)

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
        "LEAGUES:"
    )

    for info in (
        SELECTED_LEAGUES.values()
    ):

        print(
            " ",
            info["id"],
            "-",
            info["name"],
            "-",
            info["country"]
        )

    print(
        "CACHE:",
        CACHE_SECONDS
    )

    print(
        "TIMEOUT:",
        API_TIMEOUT
    )

    print(
        "PORT:",
        PORT
    )

    print("=" * 65)

    threading.Thread(

        target=
            cache_refresh_loop,

        daemon=True,

        name=
            "cache-refresh-loop",

    ).start()

    if BOT_TOKEN:

        threading.Thread(

            target=
                run_telegram_bot,

            daemon=True,

            name=
                "telegram-bot",

        ).start()

    app.run(

        host=
            "0.0.0.0",

        port=
            PORT,

        debug=
            False,

        use_reloader=
            False,

        threaded=
            True,
    )


if __name__ == "__main__":

    main()
