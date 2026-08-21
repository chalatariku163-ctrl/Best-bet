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

PRIMARY_REGION = (
    os.getenv("ODDS_REGION", "eu")
    .strip()
    .lower()
    or "eu"
)

# If PRIMARY_REGION has no matches, these can be tried.
FALLBACK_REGIONS = [
    x.strip().lower()
    for x in os.getenv(
        "ODDS_FALLBACK_REGIONS",
        "uk,us,au",
    ).split(",")
    if x.strip()
]

# IMPORTANT:
# h2h is enough for the main match list.
# More markets are loaded when a user opens a match.
LIST_MARKETS = os.getenv(
    "LIST_MARKETS",
    "h2h",
).strip()

DETAIL_MARKETS = os.getenv(
    "DETAIL_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

# Today + next 6 days = 7 calendar days.
DAYS_AHEAD = max(
    1,
    int(os.getenv("DAYS_AHEAD", "7")),
)

# Increased from 8.
# This allows more football leagues to be searched.
MAX_SOCCER_SPORTS = max(
    1,
    int(os.getenv("MAX_SOCCER_SPORTS", "30")),
)

API_TIMEOUT = max(
    3,
    int(os.getenv("API_TIMEOUT", "8")),
)

CACHE_SECONDS = max(
    30,
    int(os.getenv("CACHE_SECONDS", "120")),
)

MAX_WORKERS = max(
    1,
    int(os.getenv("MAX_WORKERS", "10")),
)

# Give the first background refresh enough time.
INITIAL_WAIT_SECONDS = max(
    0,
    int(os.getenv("INITIAL_WAIT_SECONDS", "10")),
)

# Fallback enabled by default.
ENABLE_FALLBACK = (
    os.getenv(
        "ODDS_ENABLE_FALLBACK",
        "1",
    ).strip()
    == "1"
)


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
    "remaining": None,
    "used": None,
    "last_cost": None,
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
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


def iso_z(dt):
    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def parse_iso_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).astimezone(timezone.utc)

    except Exception:
        return None


def local_time_text(iso_time):
    if not iso_time:
        return ""

    dt = parse_iso_datetime(iso_time)

    if not dt:
        return ""

    local_dt = dt.astimezone(
        timezone(timedelta(hours=3))
    )

    return local_dt.strftime(
        "%d/%m/%Y %H:%M"
    )


def region_list():
    result = []

    if PRIMARY_REGION:
        result.append(
            PRIMARY_REGION
        )

    if ENABLE_FALLBACK:
        for region in FALLBACK_REGIONS:

            if (
                region
                and region not in result
            ):
                result.append(region)

    return result


# =========================================================
# ODDS API REQUEST
# =========================================================

def odds_request(path, params=None):

    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru. "
            "Render > Environment keessatti "
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
                "User-Agent": "BEST-BET/4.0",
            },
        )

    except requests.RequestException as exc:

        API_STATS["last_error"] = str(exc)

        API_STATS["last_status"] = None

        raise RuntimeError(
            f"Odds API connection error: {exc}"
        ) from exc

    # -----------------------------------------------------
    # API HEADERS
    # -----------------------------------------------------

    API_STATS["remaining"] = (
        response.headers.get(
            "x-requests-remaining"
        )
    )

    API_STATS["used"] = (
        response.headers.get(
            "x-requests-used"
        )
    )

    API_STATS["last_cost"] = (
        response.headers.get(
            "x-requests-last"
        )
    )

    API_STATS["last_status"] = (
        response.status_code
    )

    # -----------------------------------------------------
    # ERROR
    # -----------------------------------------------------

    if response.status_code != 200:

        try:
            body = response.json()
        except Exception:
            body = response.text[:1000]

        if response.status_code == 401:

            message = (
                "ODDS_API_KEY sirrii miti: "
                f"{body}"
            )

        elif response.status_code == 429:

            message = (
                "Odds API quota/request limit: "
                f"{body}"
            )

        elif response.status_code == 404:

            message = (
                "Odds API endpoint hin argamne: "
                f"{body}"
            )

        else:

            message = (
                f"Odds API HTTP "
                f"{response.status_code}: "
                f"{body}"
            )

        API_STATS["last_error"] = message

        raise RuntimeError(message)

    # -----------------------------------------------------
    # JSON
    # -----------------------------------------------------

    try:
        data = response.json()

    except Exception as exc:

        message = (
            "Odds API JSON sirrii hin deebifne."
        )

        API_STATS["last_error"] = message

        raise RuntimeError(
            message
        ) from exc

    API_STATS["last_error"] = None

    return data


# =========================================================
# SOCCER SPORTS
# =========================================================

def soccer_sports():

    sports = odds_request(
        "/sports"
    )

    result = []

    for sport in sports:

        key = str(
            sport.get("key", "")
        ).strip()

        group = str(
            sport.get("group", "")
        ).lower()

        active = sport.get(
            "active",
            False,
        )

        if not active:
            continue

        if not key.startswith(
            "soccer_"
        ):
            continue

        # Some API versions may return
        # group differently, therefore
        # do not reject soccer_* unnecessarily.
        if group not in (
            "soccer",
            "football",
            "",
        ):
            continue

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

    "soccer_japan_j_league",

    "soccer_korea_kleague_1",

    "soccer_china_superleague",

    "soccer_south_africa_premiership",

    "soccer_egypt_premiership",

    "soccer_spl",

    "soccer_denmark_superliga",

    "soccer_sweden_allsvenskan",

    "soccer_norway_eliteserien",

    "soccer_switzerland_superleague",

]


def select_soccer_sports(sports):

    """
    Priority leagues first.

    Then all remaining active soccer leagues.

    This prevents the old problem where only
    8 leagues were checked.
    """

    by_key = {
        str(
            x.get("key", "")
        ).strip(): x
        for x in sports
        if x.get("key")
    }

    selected = []

    # -----------------------------------------------------
    # PRIORITY
    # -----------------------------------------------------

    for key in PRIORITY_KEYS:

        sport = by_key.get(key)

        if (
            sport
            and sport not in selected
        ):
            selected.append(sport)

        if len(selected) >= MAX_SOCCER_SPORTS:
            return selected[
                :MAX_SOCCER_SPORTS
            ]

    # -----------------------------------------------------
    # ALL OTHER SOCCER
    # -----------------------------------------------------

    for sport in sports:

        if sport not in selected:
            selected.append(sport)

        if len(selected) >= MAX_SOCCER_SPORTS:
            break

    return selected[
        :MAX_SOCCER_SPORTS
    ]


# =========================================================
# MARKET PARSER
# =========================================================

def parse_event_markets(event):

    home_team = event.get(
        "home_team",
        "Home",
    )

    away_team = event.get(
        "away_team",
        "Away",
    )

    h2h = {}

    totals = {}

    btts = {}

    spreads = []

    # -----------------------------------------------------
    # BOOKMAKERS
    # -----------------------------------------------------

    for bookmaker in (
        event.get("bookmakers")
        or []
    ):

        for market in (
            bookmaker.get("markets")
            or []
        ):

            market_key = str(
                market.get("key", "")
            ).lower()

            for outcome in (
                market.get("outcomes")
                or []
            ):

                name = str(
                    outcome.get(
                        "name",
                        "",
                    )
                ).strip()

                price = safe_float(
                    outcome.get(
                        "price"
                    )
                )

                if (
                    price is None
                    or price <= 1
                ):
                    continue

                # =================================================
                # H2H
                # =================================================

                if market_key == "h2h":

                    if name == home_team:

                        h2h["home"] = max(
                            h2h.get(
                                "home",
                                0,
                            ),
                            price,
                        )

                    elif name == away_team:

                        h2h["away"] = max(
                            h2h.get(
                                "away",
                                0,
                            ),
                            price,
                        )

                    elif name.lower() in (
                        "draw",
                        "x",
                    ):

                        h2h["draw"] = max(
                            h2h.get(
                                "draw",
                                0,
                            ),
                            price,
                        )

                # =================================================
                # TOTALS
                # =================================================

                elif market_key == "totals":

                    point = safe_float(
                        outcome.get(
                            "point"
                        )
                    )

                    # Keep 2.5 main market.
                    if (
                        point is not None
                        and abs(
                            point - 2.5
                        ) > 0.01
                    ):
                        continue

                    low = name.lower()

                    if low == "over":

                        totals["over"] = max(
                            totals.get(
                                "over",
                                0,
                            ),
                            price,
                        )

                    elif low == "under":

                        totals["under"] = max(
                            totals.get(
                                "under",
                                0,
                            ),
                            price,
                        )

                # =================================================
                # BTTS
                # =================================================

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
                            btts.get(
                                "yes",
                                0,
                            ),
                            price,
                        )

                    elif low in (
                        "no",
                        "btts no",
                    ):

                        btts["no"] = max(
                            btts.get(
                                "no",
                                0,
                            ),
                            price,
                        )

                # =================================================
                # SPREADS
                # =================================================

                elif market_key == "spreads":

                    spreads.append({
                        "name": name,
                        "point": outcome.get(
                            "point"
                        ),
                        "price": price,
                    })

    # -----------------------------------------------------
    # UNIQUE SPREADS
    # -----------------------------------------------------

    unique_spreads = []

    seen = set()

    for item in spreads:

        key = (
            item.get("name"),
            str(
                item.get("point")
            ),
        )

        if key in seen:
            continue

        seen.add(key)

        unique_spreads.append(
            item
        )

    unique_spreads.sort(
        key=lambda x: float(
            x.get("price", 0)
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

    h2h = parsed.get(
        "h2h"
    ) or {}

    totals = parsed.get(
        "totals"
    ) or {}

    btts = parsed.get(
        "btts"
    ) or {}

    # -----------------------------------------------------
    # 1X2
    # -----------------------------------------------------

    if h2h.get("home"):

        candidates.append({
            "selection": "1",
            "odd": float(
                h2h["home"]
            ),
            "market": "1X2",
        })

    if h2h.get("draw"):

        candidates.append({
            "selection": "X",
            "odd": float(
                h2h["draw"]
            ),
            "market": "1X2",
        })

    if h2h.get("away"):

        candidates.append({
            "selection": "2",
            "odd": float(
                h2h["away"]
            ),
            "market": "1X2",
        })

    # -----------------------------------------------------
    # TOTALS
    # -----------------------------------------------------

    if totals.get("over"):

        candidates.append({
            "selection": "Over 2.5",
            "odd": float(
                totals["over"]
            ),
            "market": "Over/Under",
        })

    if totals.get("under"):

        candidates.append({
            "selection": "Under 2.5",
            "odd": float(
                totals["under"]
            ),
            "market": "Over/Under",
        })

    # -----------------------------------------------------
    # BTTS
    # -----------------------------------------------------

    if btts.get("yes"):

        candidates.append({
            "selection": "BTTS Yes",
            "odd": float(
                btts["yes"]
            ),
            "market": "BTTS",
        })

    if btts.get("no"):

        candidates.append({
            "selection": "BTTS No",
            "odd": float(
                btts["no"]
            ),
            "market": "BTTS",
        })

    # -----------------------------------------------------
    # FILTER
    # -----------------------------------------------------

    candidates = [
        item
        for item in candidates
        if (
            1.01
            < float(
                item["odd"]
            )
            <= 20
        )
    ]

    if not candidates:
        return None

    # Lowest odds = shortest available price.
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

    parsed = parse_event_markets(
        event
    )

    commence = event.get(
        "commence_time",
        "",
    )

    return {

        "id": event.get(
            "id"
        ),

        "sport_key": sport.get(
            "key",
            event.get(
                "sport_key",
                "",
            ),
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

        "h2h": parsed[
            "h2h"
        ],

        "totals": parsed[
            "totals"
        ],

        "btts": parsed[
            "btts"
        ],

        "spreads": parsed[
            "spreads"
        ],

        "best_bet": calculate_best_bet(
            parsed
        ),
    }


# =========================================================
# ONE SPORT ODDS
# =========================================================

def get_sport_odds(
    sport,
    start_text,
    end_text,
    region,
):

    sport_key = sport.get(
        "key"
    )

    if not sport_key:

        return (
            [],
            region,
            "sport_key missing",
        )

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

        return (
            events or [],
            region,
            None,
        )

    except Exception as exc:

        print(
            "[ODDS ERROR]",
            sport_key,
            region,
            repr(exc),
        )

        return (
            [],
            region,
            str(exc),
        )


# =========================================================
# PROCESS EVENTS
# =========================================================

def process_events(
    events,
    sport,
    now,
    end_time,
    used_region,
):

    result = []

    for event in events:

        try:

            commence = event.get(
                "commence_time"
            )

            if not commence:
                continue

            dt = parse_iso_datetime(
                commence
            )

            if not dt:
                continue

            if dt < now:
                continue

            if dt > end_time:
                continue

            converted = convert_event(
                event,
                sport,
            )

            # Main list requires 1X2.
            if not converted.get(
                "h2h"
            ):
                continue

            converted[
                "odds_region"
            ] = used_region

            result.append(
                converted
            )

        except Exception as exc:

            print(
                "[EVENT ERROR]",
                repr(exc),
            )

    return result


# =========================================================
# FETCH ONE REGION
# =========================================================

def fetch_region_matches(
    sports,
    start_text,
    end_text,
    now,
    end_time,
    region,
):

    result = []

    errors = []

    workers = min(
        MAX_WORKERS,
        max(
            1,
            len(sports),
        ),
    )

    print(
        "[REGION START]",
        region,
        "SPORTS:",
        len(sports),
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as pool:

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

        for future in as_completed(
            jobs
        ):

            sport = jobs[
                future
            ]

            try:

                (
                    events,
                    used_region,
                    error,
                ) = future.result()

            except Exception as exc:

                events = []

                used_region = region

                error = str(exc)

            if error:

                errors.append(
                    f"{sport.get('key')}: {error}"
                )

            if events:

                result.extend(
                    process_events(
                        events,
                        sport,
                        now,
                        end_time,
                        used_region,
                    )
                )

    return (
        result,
        errors,
    )


# =========================================================
# FETCH MATCHES FROM API
# =========================================================

def fetch_matches_from_api():

    if not ODDS_API_KEY:

        raise RuntimeError(
            "ODDS_API_KEY hin jiru."
        )

    # -----------------------------------------------------
    # GET SOCCER LEAGUES
    # -----------------------------------------------------

    all_sports = soccer_sports()

    if not all_sports:

        raise RuntimeError(
            "The Odds API irraa soccer leagues "
            "homaa hin argamne."
        )

    sports = select_soccer_sports(
        all_sports
    )

    # -----------------------------------------------------
    # TIME
    # -----------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    end_time = (
        now
        + timedelta(
            days=DAYS_AHEAD
        )
    )

    start_text = iso_z(
        now
    )

    end_text = iso_z(
        end_time
    )

    # -----------------------------------------------------
    # LOG
    # -----------------------------------------------------

    print(
        "===================================="
    )

    print(
        "[SOCCER FOUND]",
        len(all_sports),
    )

    print(
        "[SOCCER SELECTED]",
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
        "[DAYS]",
        DAYS_AHEAD,
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

    all_result = []

    all_errors = []

    # -----------------------------------------------------
    # PRIMARY REGION
    # -----------------------------------------------------

    primary_result, primary_errors = (
        fetch_region_matches(
            sports,
            start_text,
            end_text,
            now,
            end_time,
            PRIMARY_REGION,
        )
    )

    all_result.extend(
        primary_result
    )

    all_errors.extend(
        primary_errors
    )

    print(
        "[PRIMARY RESULT]",
        PRIMARY_REGION,
        len(primary_result),
    )

    # -----------------------------------------------------
    # FALLBACK REGIONS
    #
    # Only try fallback if primary has ZERO matches.
    # This prevents unnecessary quota usage.
    # -----------------------------------------------------

    if (
        not all_result
        and ENABLE_FALLBACK
    ):

        for fallback in FALLBACK_REGIONS:

            if fallback == PRIMARY_REGION:
                continue

            print(
                "[FALLBACK REGION]",
                fallback,
            )

            fallback_result, fallback_errors = (
                fetch_region_matches(
                    sports,
                    start_text,
                    end_text,
                    now,
                    end_time,
                    fallback,
                )
            )

            all_result.extend(
                fallback_result
            )

            all_errors.extend(
                fallback_errors
            )

            print(
                "[FALLBACK RESULT]",
                fallback,
                len(fallback_result),
            )

            if all_result:
                break

    # -----------------------------------------------------
    # UNIQUE MATCHES
    # -----------------------------------------------------

    unique = {}

    for match in all_result:

        match_id = str(
            match.get("id")
            or ""
        )

        if not match_id:
            continue

        if match_id not in unique:

            unique[
                match_id
            ] = match

    result = list(
        unique.values()
    )

    # -----------------------------------------------------
    # SORT BY DATE
    # -----------------------------------------------------

    result.sort(
        key=lambda x: (
            x.get(
                "commence_time",
                "",
            )
        )
    )

    print(
        "[TOTAL MATCHES]",
        len(result),
    )

    if all_errors:

        print(
            "[ERROR COUNT]",
            len(all_errors),
        )

        for error in all_errors[-10:]:

            print(
                "[API ERROR]",
                error,
            )

    return (
        result,
        all_errors,
    )


# =========================================================
# REFRESH CACHE
# =========================================================

def refresh_matches(force=False):

    # Only one refresh at a time.
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

                # IMPORTANT:
                # If API returns matches, replace cache.
                if matches:

                    MATCH_CACHE[
                        "matches"
                    ] = matches

                    MATCH_CACHE[
                        "time"
                    ] = time.time()

                # If API returns zero matches,
                # do NOT destroy existing cache.
                MATCH_CACHE[
                    "error"
                ] = (
                    errors[-10:]
                    if errors
                    else (
                        None
                        if matches
                        else "No football matches "
                             "with available odds "
                             "were returned."
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
                len(matches),
            )

            return True

        except Exception as exc:

            print(
                "[MATCH REFRESH ERROR]",
                repr(exc),
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


# =========================================================
# START BACKGROUND REFRESH
# =========================================================

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
            name="odds-refresh",
        )

        thread.start()

    return True


# =========================================================
# GET MATCHES
# =========================================================

def get_matches(force=False):

    now_ts = time.time()

    with CACHE_LOCK:

        cached_time = (
            MATCH_CACHE[
                "time"
            ]
        )

        cached_matches = list(
            MATCH_CACHE[
                "matches"
            ]
        )

        refreshing = (
            MATCH_CACHE[
                "refreshing"
            ]
        )

    fresh = (
        cached_time > 0
        and (
            now_ts
            - cached_time
        ) < CACHE_SECONDS
    )

    # -----------------------------------------------------
    # CACHE OK
    # -----------------------------------------------------

    if (
        not force
        and fresh
    ):

        return cached_matches

    # -----------------------------------------------------
    # START REFRESH
    # -----------------------------------------------------

    if not refreshing:

        start_refresh_background(
            force=force
        )

    # -----------------------------------------------------
    # GET CURRENT CACHE
    # -----------------------------------------------------

    with CACHE_LOCK:

        cached_matches = list(
            MATCH_CACHE[
                "matches"
            ]
        )

    # -----------------------------------------------------
    # FIRST LOAD
    #
    # Wait for background request.
    # -----------------------------------------------------

    if (
        not cached_matches
        and INITIAL_WAIT_SECONDS > 0
    ):

        INITIAL_CACHE_EVENT.wait(
            timeout=INITIAL_WAIT_SECONDS
        )

        with CACHE_LOCK:

            cached_matches = list(
                MATCH_CACHE[
                    "matches"
                ]
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
        "📊 Multiple Markets\n"
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

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if query.data == "profile":

        await query.edit_message_text(

            f"👤 *PROFILE*\n\n"

            f"Name: *{u['name']}*\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n"

            f"Bet Slip: "
            f"*{len(u['betslip'])}*",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # BALANCE
    # -----------------------------------------------------

    elif query.data == "balance":

        await query.edit_message_text(

            f"💳 *BALANCE*\n\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n\n"

            "🧪 Demo system qofa.",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

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
                    f"{item['time']}\n"

                    f"💰 "
                    f"{item['stake']:.2f} | "

                    f"📈 "
                    f"{item['odds']:.2f} | "

                    f"{item['status']}\n\n"

                )

        await query.edit_message_text(

            text,

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # HOW TO
    # -----------------------------------------------------

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
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"],
)
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"],
)
def health():

    with CACHE_LOCK:

        cache_time = (
            MATCH_CACHE[
                "time"
            ]
        )

        cache_count = len(
            MATCH_CACHE[
                "matches"
            ]
        )

        refreshing = (
            MATCH_CACHE[
                "refreshing"
            ]
        )

        cache_error = (
            MATCH_CACHE[
                "error"
            ]
        )

    return jsonify({

        "status": "online",

        "bot": "Best Bet",

        "api": "The Odds API",

        "api_key_configured": bool(
            ODDS_API_KEY
        ),

        "web_app": WEB_APP_URL,

        "regions": region_list(),

        "primary_region": PRIMARY_REGION,

        "fallback_enabled": (
            ENABLE_FALLBACK
        ),

        "list_markets": LIST_MARKETS,

        "detail_markets": DETAIL_MARKETS,

        "days": DAYS_AHEAD,

        "max_soccer_sports":
            MAX_SOCCER_SPORTS,

        "cache_seconds":
            CACHE_SECONDS,

        "api_timeout":
            API_TIMEOUT,

        "matches_cached":
            cache_count,

        "cache_age_seconds": (

            round(
                time.time()
                - cache_time,
                1,
            )

            if cache_time
            else None

        ),

        "refreshing":
            refreshing,

        "cache_error":
            cache_error,

        "api_remaining":
            API_STATS[
                "remaining"
            ],

        "api_used":
            API_STATS[
                "used"
            ],

        "api_last_cost":
            API_STATS[
                "last_cost"
            ],

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


# =========================================================
# BASIC API TEST
# =========================================================

@app.route(
    "/api/test",
    methods=["GET"],
)
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

@app.route(
    "/api/odds-test",
    methods=["GET"],
)
def odds_test():

    try:

        sports = soccer_sports()

        selected = select_soccer_sports(
            sports
        )

        return jsonify({

            "success": True,

            "message":
                "The Odds API connected.",

            "api_key_configured":
                bool(ODDS_API_KEY),

            "soccer_count":
                len(sports),

            "selected_count":
                len(selected),

            "regions":
                region_list(),

            "sample_sports": [

                {
                    "key":
                        x.get("key"),

                    "title":
                        x.get("title"),

                    "active":
                        x.get("active"),

                    "group":
                        x.get("group"),

                }

                for x in sports[:30]

            ],

            "api_remaining":
                API_STATS[
                    "remaining"
                ],

            "api_used":
                API_STATS[
                    "used"
                ],

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
                "The Odds API connection failed.",

            "error":
                str(exc),

            "api_key_configured":
                bool(ODDS_API_KEY),

            "api_remaining":
                API_STATS[
                    "remaining"
                ],

            "api_used":
                API_STATS[
                    "used"
                ],

            "api_last_status":
                API_STATS[
                    "last_status"
                ],

        }), 200


# =========================================================
# MATCHES API
# =========================================================

@app.route(
    "/api/matches",
    methods=["GET"],
)
def api_matches():

    force = (
        request.args.get(
            "refresh",
            "0",
        )
        == "1"
    )

    try:

        matches = get_matches(
            force=force
        )

        with CACHE_LOCK:

            refreshing = (
                MATCH_CACHE[
                    "refreshing"
                ]
            )

            cache_error = (
                MATCH_CACHE[
                    "error"
                ]
            )

            cache_time = (
                MATCH_CACHE[
                    "time"
                ]
            )

        stale = (

            bool(matches)

            and cache_time > 0

            and (
                time.time()
                - cache_time
            ) >= CACHE_SECONDS

        )

        return jsonify({

            "success": True,

            "count":
                len(matches),

            "matches":
                matches,

            "message": (

                "Football odds loaded."

                if matches

                else (
                    "Matches are loading. "
                    "Please refresh shortly."
                )

            ),

            "loading": (
                refreshing
                and not bool(matches)
            ),

            "stale":
                stale,

            "api_key_configured":
                bool(ODDS_API_KEY),

            "regions":
                region_list(),

            "primary_region":
                PRIMARY_REGION,

            "fallback_enabled":
                ENABLE_FALLBACK,

            "api_remaining":
                API_STATS[
                    "remaining"
                ],

            "api_used":
                API_STATS[
                    "used"
                ],

            "api_last_cost":
                API_STATS[
                    "last_cost"
                ],

            "api_last_status":
                API_STATS[
                    "last_status"
                ],

            "api_error":
                cache_error,

        }), 200

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
                API_STATS[
                    "remaining"
                ],

            "api_used":
                API_STATS[
                    "used"
                ],

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
                )
                == str(match_id)

            ),

            None,

        )

        if not match:

            return jsonify({

                "success":
                    False,

                "error":
                    "Match hin argamne.",

                "markets":
                    [],

            }), 200

        sport_key = match.get(
            "sport_key"
        )

        if not sport_key:

            return jsonify({

                "success":
                    False,

                "error":
                    "Sport key hin jiru.",

                "markets":
                    [],

            }), 200

        event = None

        used_region = None

        last_error = None

        # -------------------------------------------------
        # DETAILS REGIONS
        # -------------------------------------------------

        detail_regions = [
            PRIMARY_REGION
        ]

        if ENABLE_FALLBACK:

            detail_regions += [

                x
                for x in FALLBACK_REGIONS

                if x
                not in detail_regions

            ]

        # -------------------------------------------------
        # FETCH DETAILS
        # -------------------------------------------------

        for region in detail_regions:

            try:

                event_path = (
                    f"/sports/"
                    f"{sport_key}"
                    f"/events/"
                    f"{match_id}"
                    f"/odds"
                )

                data = odds_request(

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

                if isinstance(
                    data,
                    dict,
                ):

                    event = data

                elif isinstance(
                    data,
                    list,
                ):

                    event = next(

                        (

                            x
                            for x in data

                            if str(
                                x.get("id")
                            )
                            == str(
                                match_id
                            )

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

                if (
                    "401"
                    in last_error
                    or
                    "429"
                    in last_error
                ):
                    break

        if not event:

            return jsonify({

                "success":
                    True,

                "match":
                    match,

                "markets":
                    [],

                "best_bet":
                    match.get(
                        "best_bet"
                    ),

                "odds_error": (

                    last_error
                    or
                    "Current odds hin argamne."

                ),

                "odds_region":
                    used_region,

            }), 200

        # -------------------------------------------------
        # CONVERT
        # -------------------------------------------------

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

        # -------------------------------------------------
        # H2H
        # -------------------------------------------------

        h2h = (
            converted.get(
                "h2h"
            )
            or {}
        )

        if h2h:

            selections = []

            if h2h.get("home"):

                selections.append({

                    "value":
                        "1",

                    "odd":
                        h2h[
                            "home"
                        ],

                })

            if h2h.get("draw"):

                selections.append({

                    "value":
                        "X",

                    "odd":
                        h2h[
                            "draw"
                        ],

                })

            if h2h.get("away"):

                selections.append({

                    "value":
                        "2",

                    "odd":
                        h2h[
                            "away"
                        ],

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

        # -------------------------------------------------
        # TOTALS
        # -------------------------------------------------

        totals = (
            converted.get(
                "totals"
            )
            or {}
        )

        if totals:

            selections = []

            if totals.get("over"):

                selections.append({

                    "value":
                        "Over 2.5",

                    "odd":
                        totals[
                            "over"
                        ],

                })

            if totals.get("under"):

                selections.append({

                    "value":
                        "Under 2.5",

                    "odd":
                        totals[
                            "under"
                        ],

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

        # -------------------------------------------------
        # BTTS
        # -------------------------------------------------

        btts = (
            converted.get(
                "btts"
            )
            or {}
        )

        if btts:

            selections = []

            if btts.get("yes"):

                selections.append({

                    "value":
                        "BTTS Yes",

                    "odd":
                        btts[
                            "yes"
                        ],

                })

            if btts.get("no"):

                selections.append({

                    "value":
                        "BTTS No",

                    "odd":
                        btts[
                            "no"
                        ],

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

        # -------------------------------------------------
        # SPREADS
        # -------------------------------------------------

        spreads = (
            converted.get(
                "spreads"
            )
            or []
        )

        if spreads:

            selections = []

            for item in spreads:

                selections.append({

                    "value":
                        (
                            f"{item.get('name')} "
                            f"{item.get('point')}"
                        ),

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
                API_STATS[
                    "remaining"
                ],

            "api_used":
                API_STATS[
                    "used"
                ],

        }), 200

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

            "markets":
                [],

        }), 200


# =========================================================
# LIVE
# =========================================================

@app.route(
    "/api/live",
    methods=["GET"],
)
def api_live():

    try:

        result = []

        sports = select_soccer_sports(
            soccer_sports()
        )

        # Keep live requests limited.
        for sport in sports[:5]:

            sport_key = sport.get(
                "key"
            )

            if not sport_key:
                continue

            try:

                scores = odds_request(

                    f"/sports/"
                    f"{sport_key}"
                    f"/scores",

                    {

                        "daysFrom":
                            1,

                        "dateFormat":
                            "iso",

                    },

                )

            except Exception as exc:

                print(
                    "[LIVE SKIP]",
                    sport_key,
                    repr(exc),
                )

                continue

            for event in (
                scores or []
            ):

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

        }), 200

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
# ERROR HANDLERS
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
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )

    except Exception as exc:

        print(
            "[BOT ERROR]",
            repr(exc),
        )


# =========================================================
# BACKGROUND CACHE LOOP
# =========================================================

def cache_refresh_loop():

    # Give Flask time to start.
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

                or (

                    time.time()
                    - cache_time

                ) >= CACHE_SECONDS

            )

            if (
                expired
                and not refreshing
            ):

                start_refresh_background()

        except Exception as exc:

            print(
                "[CACHE LOOP ERROR]",
                repr(exc),
            )

        # Check every 20 seconds.
        time.sleep(20)


# =========================================================
# START
# =========================================================

def main():

    print(
        "===================================="
    )

    print(
        "        BEST BET 4.0"
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
        "FALLBACK ENABLED:",
        ENABLE_FALLBACK,
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
        "MAX WORKERS:",
        MAX_WORKERS,
    )

    print(
        "INITIAL WAIT:",
        INITIAL_WAIT_SECONDS,
    )

    print(
        "PORT:",
        PORT,
    )

    print(
        "===================================="
    )

    # -----------------------------------------------------
    # CACHE THREAD
    # -----------------------------------------------------

    cache_thread = threading.Thread(
        target=cache_refresh_loop,
        daemon=True,
        name="cache-refresh-loop",
    )

    cache_thread.start()

    # -----------------------------------------------------
    # TELEGRAM
    # -----------------------------------------------------

    if BOT_TOKEN:

        bot_thread = threading.Thread(
            target=run_telegram_bot,
            daemon=True,
            name="telegram-bot",
        )

        bot_thread.start()

    # -----------------------------------------------------
    # FLASK
    # -----------------------------------------------------

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
