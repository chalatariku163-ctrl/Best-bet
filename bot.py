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

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

# =========================================================
# SPORTMONKS TOKEN
#
# Render > Environment:
#
# SPORTMONKS_API_TOKEN=your_token_here
# =========================================================

SPORTMONKS_API_TOKEN = os.getenv(
    "SPORTMONKS_API_TOKEN",
    ""
).strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip().rstrip("/")

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

SPORTMONKS_BASE = (
    "https://api.sportmonks.com/v3/football"
)

# Ethiopia UTC+3
LOCAL_TZ = timezone(
    timedelta(hours=3)
)

LOCAL_TIMEZONE_NAME = (
    "Africa/Addis_Ababa"
)

# Today + next 6 days
DAYS_AHEAD = max(
    1,
    int(
        os.getenv(
            "DAYS_AHEAD",
            "7",
        )
    ),
)

API_TIMEOUT = max(
    5,
    int(
        os.getenv(
            "API_TIMEOUT",
            "15",
        )
    ),
)

CACHE_SECONDS = max(
    30,
    int(
        os.getenv(
            "CACHE_SECONDS",
            "120",
        )
    ),
)

INITIAL_WAIT_SECONDS = max(
    0,
    int(
        os.getenv(
            "INITIAL_WAIT_SECONDS",
            "15",
        )
    ),
)

SPORTMONKS_MAX_PAGES = max(
    1,
    int(
        os.getenv(
            "SPORTMONKS_MAX_PAGES",
            "20",
        )
    ),
)

SPORTMONKS_PER_PAGE = min(
    50,
    max(
        10,
        int(
            os.getenv(
                "SPORTMONKS_PER_PAGE",
                "50",
            )
        ),
    ),
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
    "last_status": None,
    "last_error": None,
    "last_request": None,
    "last_refresh": None,
    "meta": None,
}


# =========================================================
# USER FUNCTIONS
# =========================================================

def get_user(
    user_id,
    name="User",
):

    if user_id not in USERS:

        USERS[user_id] = {
            "name": name,
            "balance": 0.0,
            "history": [],
            "betslip": [],
        }

    return USERS[user_id]


def total_odds(
    user_id,
):

    total = 1.0

    for item in get_user(
        user_id
    )["betslip"]:

        try:

            odd = float(
                item.get(
                    "odd",
                    1,
                )
            )

            if odd > 1:

                total *= odd

        except Exception:

            pass

    return total


def betslip_text(
    user_id,
):

    slips = get_user(
        user_id
    )["betslip"]

    if not slips:

        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa "
            "selection filadhu."
        )

    total = total_odds(
        user_id
    )

    text = (
        "🎟️ *BET SLIP*\n\n"
    )

    for i, item in enumerate(
        slips,
        1,
    ):

        try:

            odd = float(
                item.get(
                    "odd",
                    0,
                )
            )

        except Exception:

            odd = 0

        text += (

            f"*{i}.* "
            f"{item.get('home', '')} vs "
            f"{item.get('away', '')}\n"

            f"🏆 "
            f"{item.get('league', '')}\n"

            f"📊 "
            f"{item.get('market', '')}\n"

            f"🎯 *"
            f"{item.get('selection', '')}"
            f"*\n"

            f"Odd: *{odd:.2f}*\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━\n"
        f"📈 *Total Odds:* "
        f"{total:.2f}\n\n"
        "🧪 Demo/testing qofa."
    )

    return text


# =========================================================
# UTILITY
# =========================================================

def safe_float(
    value,
):

    try:

        return float(
            value
        )

    except Exception:

        return None


def parse_iso_datetime(
    value,
):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).astimezone(
            timezone.utc
        )

    except Exception:

        return None


def parse_sportmonks_datetime(
    value,
):

    if not value:
        return None

    text = str(
        value
    ).strip()

    formats = [

        "%Y-%m-%d %H:%M:%S",

        "%Y-%m-%dT%H:%M:%S",

        "%Y-%m-%dT%H:%M:%S.%f",

        "%Y-%m-%d %H:%M:%S.%f",

    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                text,
                fmt,
            ).replace(
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    return parse_iso_datetime(
        text
    )


def iso_z(
    dt,
):

    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def local_time_text(
    value,
):

    if not value:
        return ""

    dt = parse_sportmonks_datetime(
        value
    )

    if not dt:
        return ""

    local_dt = dt.astimezone(
        LOCAL_TZ
    )

    return local_dt.strftime(
        "%d/%m/%Y %H:%M"
    )


def relation_data(
    item,
    key,
):

    if not isinstance(
        item,
        dict,
    ):
        return []

    value = item.get(
        key
    )

    if value is None:
        return []

    if isinstance(
        value,
        dict,
    ):

        data = value.get(
            "data"
        )

        if isinstance(
            data,
            list,
        ):
            return data

        if isinstance(
            data,
            dict,
        ):
            return [data]

        # Some SportMonks
        # responses may put the
        # object directly here.
        if (
            "id" in value
            or "name" in value
            or "label" in value
        ):
            return [value]

        return []

    if isinstance(
        value,
        list,
    ):

        return value

    return []


def response_data(
    body,
):

    if not isinstance(
        body,
        dict,
    ):
        return []

    data = body.get(
        "data",
        [],
    )

    if isinstance(
        data,
        list,
    ):
        return data

    if isinstance(
        data,
        dict,
    ):
        return [data]

    return []


# =========================================================
# SPORTMONKS REQUEST
# =========================================================

def sportmonks_request(
    path,
    params=None,
):

    if not SPORTMONKS_API_TOKEN:

        raise RuntimeError(
            "SPORTMONKS_API_TOKEN hin jiru. "
            "Render > Environment keessatti "
            "SPORTMONKS_API_TOKEN galchi."
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

    try:

        response = requests.get(

            url,

            params=query,

            timeout=API_TIMEOUT,

            headers={
                "Accept":
                    "application/json",

                "User-Agent":
                    "BEST-BET/SPORTMONKS",
            },

        )

    except requests.RequestException as exc:

        message = (
            "SportMonks connection error: "
            f"{exc}"
        )

        API_STATS[
            "last_error"
        ] = message

        API_STATS[
            "last_status"
        ] = None

        raise RuntimeError(
            message
        ) from exc

    API_STATS[
        "last_status"
    ] = response.status_code

    try:

        body = response.json()

    except Exception:

        body = response.text[:2000]

    if response.status_code != 200:

        message = (
            f"SportMonks HTTP "
            f"{response.status_code}: "
            f"{body}"
        )

        if response.status_code == 401:

            message = (
                "SportMonks token sirrii miti "
                "ykn authentication rakkoo qaba.\n"
                f"{body}"
            )

        elif response.status_code == 403:

            message = (
                "SportMonks API access hin hayyamamne. "
                "Plan/token kee keessatti endpoint "
                "ykn odds feed jiraachuu qaba.\n"
                f"{body}"
            )

        elif response.status_code == 404:

            message = (
                "SportMonks endpoint hin argamne.\n"
                f"{body}"
            )

        elif response.status_code == 429:

            message = (
                "SportMonks request limit gahe.\n"
                f"{body}"
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

    API_STATS[
        "meta"
    ] = (
        body.get(
            "meta"
        )
        if isinstance(
            body,
            dict,
        )
        else None
    )

    return body


# =========================================================
# ODDS EXTRACTION
# =========================================================

def extract_odds(
    fixture,
):

    odds = relation_data(
        fixture,
        "odds",
    )

    return odds


def normalize_market_name(
    odd,
):

    return " ".join(

        str(
            odd.get(
                key,
                "",
            )
        )

        for key in (

            "market_description",

            "market_name",

            "description",

        )

    ).lower()


# =========================================================
# PARTICIPANTS
# =========================================================

def get_fixture_teams(
    fixture,
):

    home = ""
    away = ""

    participants = relation_data(
        fixture,
        "participants",
    )

    for participant in participants:

        name = (
            participant.get(
                "name"
            )
            or participant.get(
                "short_code"
            )
            or participant.get(
                "code"
            )
            or ""
        )

        meta = (
            participant.get(
                "meta"
            )
            or {}
        )

        if not isinstance(
            meta,
            dict,
        ):
            meta = {}

        location = str(
            meta.get(
                "location",
                "",
            )
        ).lower()

        if location == "home":

            home = name

        elif location == "away":

            away = name

    fixture_name = str(
        fixture.get(
            "name",
            "",
        )
    )

    if (
        not home
        or not away
    ):

        if " vs " in fixture_name:

            parts = fixture_name.split(
                " vs ",
                1,
            )

            if not home:
                home = parts[0].strip()

            if not away:
                away = parts[1].strip()

        elif " - " in fixture_name:

            parts = fixture_name.split(
                " - ",
                1,
            )

            if not home:
                home = parts[0].strip()

            if not away:
                away = parts[1].strip()

    return (
        home or "Home",
        away or "Away",
    )


# =========================================================
# SPORTMONKS ODDS PARSER
# =========================================================

def parse_fixture_odds(
    fixture,
):

    home, away = get_fixture_teams(
        fixture
    )

    result = {

        "h2h": {},

        "totals": {},

        "btts": {},

        "spreads": [],

    }

    raw_spreads = []

    odds = extract_odds(
        fixture
    )

    for odd in odds:

        if not isinstance(
            odd,
            dict,
        ):
            continue

        price = safe_float(
            odd.get(
                "value"
            )
        )

        if (
            price is None
            or price <= 1
        ):
            continue

        label = str(
            odd.get(
                "label",
                "",
            )
        ).strip()

        name = str(
            odd.get(
                "name",
                "",
            )
        ).strip()

        market = normalize_market_name(
            odd
        )

        label_lower = (
            label.lower()
        )

        name_lower = (
            name.lower()
        )

        # =================================================
        # H2H / MATCH WINNER
        # =================================================

        market_id = odd.get(
            "market_id"
        )

        is_h2h = (

            "match winner"
            in market

            or "fulltime result"
            in market

            or "1x2"
            in market

            or str(
                market_id
            ) == "1"

            or label_lower in (
                "1",
                "x",
                "2",
                "home",
                "draw",
                "away",
            )

        )

        if is_h2h:

            if label_lower in (
                "1",
                "home",
            ):

                result[
                    "h2h"
                ]["home"] = max(

                    result[
                        "h2h"
                    ].get(
                        "home",
                        0,
                    ),

                    price,

                )

                continue

            if label_lower in (
                "x",
                "draw",
            ):

                result[
                    "h2h"
                ]["draw"] = max(

                    result[
                        "h2h"
                    ].get(
                        "draw",
                        0,
                    ),

                    price,

                )

                continue

            if label_lower in (
                "2",
                "away",
            ):

                result[
                    "h2h"
                ]["away"] = max(

                    result[
                        "h2h"
                    ].get(
                        "away",
                        0,
                    ),

                    price,

                )

                continue

            if (
                home
                and name_lower
                == home.lower()
            ):

                result[
                    "h2h"
                ]["home"] = max(

                    result[
                        "h2h"
                    ].get(
                        "home",
                        0,
                    ),

                    price,

                )

                continue

            if (
                away
                and name_lower
                == away.lower()
            ):

                result[
                    "h2h"
                ]["away"] = max(

                    result[
                        "h2h"
                    ].get(
                        "away",
                        0,
                    ),

                    price,

                )

                continue

        # =================================================
        # TOTALS
        # =================================================

        if (

            "over/under"
            in market

            or "total goals"
            in market

            or "goals over/under"
            in market

            or (
                "total"
                in market
                and "match"
                in market
            )

        ):

            point = safe_float(
                odd.get(
                    "total"
                )
            )

            if point is None:

                point = safe_float(
                    odd.get(
                        "handicap"
                    )
                )

            if (
                point is not None
                and abs(
                    point - 2.5
                ) > 0.01
            ):

                continue

            if (

                label_lower
                == "over"

                or name_lower
                == "over"

                or "over" in name_lower

            ):

                result[
                    "totals"
                ]["over"] = max(

                    result[
                        "totals"
                    ].get(
                        "over",
                        0,
                    ),

                    price,

                )

            elif (

                label_lower
                == "under"

                or name_lower
                == "under"

                or "under" in name_lower

            ):

                result[
                    "totals"
                ]["under"] = max(

                    result[
                        "totals"
                    ].get(
                        "under",
                        0,
                    ),

                    price,

                )

            continue

        # =================================================
        # BTTS
        # =================================================

        if (

            "both teams to score"
            in market

            or "both team to score"
            in market

            or "btts"
            in market

        ):

            if label_lower in (
                "yes",
                "btts yes",
            ) or name_lower in (
                "yes",
                "btts yes",
            ):

                result[
                    "btts"
                ]["yes"] = max(

                    result[
                        "btts"
                    ].get(
                        "yes",
                        0,
                    ),

                    price,

                )

            elif label_lower in (
                "no",
                "btts no",
            ) or name_lower in (
                "no",
                "btts no",
            ):

                result[
                    "btts"
                ]["no"] = max(

                    result[
                        "btts"
                    ].get(
                        "no",
                        0,
                    ),

                    price,

                )

            continue

        # =================================================
        # HANDICAP / SPREAD
        # =================================================

        if (

            "handicap"
            in market

            or "asian handicap"
            in market

            or "spread"
            in market

        ):

            point = (
                odd.get(
                    "handicap"
                )
            )

            if point is None:

                point = odd.get(
                    "total"
                )

            raw_spreads.append({

                "name":
                    name or label,

                "point":
                    point,

                "price":
                    price,

            })

    # =====================================================
    # UNIQUE SPREADS
    # =====================================================

    seen = set()

    for item in raw_spreads:

        key = (

            item.get(
                "name"
            ),

            str(
                item.get(
                    "point"
                )
            ),

        )

        if key in seen:
            continue

        seen.add(
            key
        )

        result[
            "spreads"
        ].append(
            item
        )

    result[
        "spreads"
    ].sort(

        key=lambda x:
            float(
                x.get(
                    "price",
                    0,
                )
            ),

        reverse=True,

    )

    return result


# =========================================================
# BEST BET
# =========================================================

def calculate_best_bet(
    parsed,
):

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

    # =====================================================
    # 1X2
    # =====================================================

    if h2h.get(
        "home"
    ):

        candidates.append({

            "selection":
                "1",

            "odd":
                float(
                    h2h[
                        "home"
                    ]
                ),

            "market":
                "1X2",

        })

    if h2h.get(
        "draw"
    ):

        candidates.append({

            "selection":
                "X",

            "odd":
                float(
                    h2h[
                        "draw"
                    ]
                ),

            "market":
                "1X2",

        })

    if h2h.get(
        "away"
    ):

        candidates.append({

            "selection":
                "2",

            "odd":
                float(
                    h2h[
                        "away"
                    ]
                ),

            "market":
                "1X2",

        })

    # =====================================================
    # TOTALS
    # =====================================================

    if totals.get(
        "over"
    ):

        candidates.append({

            "selection":
                "Over 2.5",

            "odd":
                float(
                    totals[
                        "over"
                    ]
                ),

            "market":
                "Over/Under",

        })

    if totals.get(
        "under"
    ):

        candidates.append({

            "selection":
                "Under 2.5",

            "odd":
                float(
                    totals[
                        "under"
                    ]
                ),

            "market":
                "Over/Under",

        })

    # =====================================================
    # BTTS
    # =====================================================

    if btts.get(
        "yes"
    ):

        candidates.append({

            "selection":
                "BTTS Yes",

            "odd":
                float(
                    btts[
                        "yes"
                    ]
                ),

            "market":
                "BTTS",

        })

    if btts.get(
        "no"
    ):

        candidates.append({

            "selection":
                "BTTS No",

            "odd":
                float(
                    btts[
                        "no"
                    ]
                ),

            "market":
                "BTTS",

        })

    candidates = [

        x

        for x in candidates

        if (

            1.01
            < float(
                x[
                    "odd"
                ]
            )
            <= 20

        )

    ]

    if not candidates:
        return None

    candidates.sort(

        key=lambda x:
            float(
                x[
                    "odd"
                ]
            )

    )

    return candidates[0]


# =========================================================
# CONVERT FIXTURE
# =========================================================

def convert_fixture(
    fixture,
):

    parsed = parse_fixture_odds(
        fixture
    )

    home, away = get_fixture_teams(
        fixture
    )

    league = ""

    league_relation = relation_data(
        fixture,
        "league",
    )

    if league_relation:

        league = (
            league_relation[0].get(
                "name"
            )
            or ""
        )

    if not league:

        league_obj = fixture.get(
            "league"
        )

        if isinstance(
            league_obj,
            dict,
        ):

            league = (
                league_obj.get(
                    "name"
                )
                or ""
            )

            if not league:

                league_data = (
                    league_obj.get(
                        "data"
                    )
                )

                if isinstance(
                    league_data,
                    dict,
                ):

                    league = (
                        league_data.get(
                            "name"
                        )
                        or ""
                    )

    if not league:

        league = "Football"

    starting_at = (
        fixture.get(
            "starting_at"
        )
        or fixture.get(
            "startingAt"
        )
        or ""
    )

    return {

        "id":
            fixture.get(
                "id"
            ),

        "sport_key":
            "football",

        "fixture_id":
            fixture.get(
                "id"
            ),

        "league":
            league,

        "home":
            home,

        "away":
            away,

        "time":
            local_time_text(
                starting_at
            ),

        "commence_time":
            starting_at,

        "starting_at":
            starting_at,

        "h2h":
            parsed[
                "h2h"
            ],

        "totals":
            parsed[
                "totals"
            ],

        "btts":
            parsed[
                "btts"
            ],

        "spreads":
            parsed[
                "spreads"
            ],

        "best_bet":
            calculate_best_bet(
                parsed
            ),

        "has_odds":
            bool(
                extract_odds(
                    fixture
                )
            ),

    }


# =========================================================
# GET FIXTURES BETWEEN DATES
# =========================================================

def get_fixtures_between(
    start_date,
    end_date,
):

    all_fixtures = []

    for page in range(
        1,
        SPORTMONKS_MAX_PAGES + 1,
    ):

        print(
            "[SPORTMONKS FIXTURES]",
            start_date,
            end_date,
            "PAGE:",
            page,
        )

        body = sportmonks_request(

            (
                "/fixtures/between/"
                f"{start_date}/"
                f"{end_date}"
            ),

            {

                "include":
                    "participants;league;odds",

                "order":
                    "asc",

                "per_page":
                    SPORTMONKS_PER_PAGE,

                "page":
                    page,

                "timezone":
                    LOCAL_TIMEZONE_NAME,

            },

        )

        data = response_data(
            body
        )

        all_fixtures.extend(
            data
        )

        pagination = (
            body.get(
                "pagination"
            )
            or {}
        )

        meta = (
            body.get(
                "meta"
            )
            or {}
        )

        has_more = (

            pagination.get(
                "has_more"
            )

            or meta.get(
                "has_more"
            )

        )

        if not has_more:
            break

        if not data:
            break

    return all_fixtures


# =========================================================
# FETCH MATCHES
# =========================================================

def fetch_matches_from_api():

    if not SPORTMONKS_API_TOKEN:

        raise RuntimeError(
            "SPORTMONKS_API_TOKEN hin jiru."
        )

    now = datetime.now(
        timezone.utc
    )

    end_time = (
        now
        + timedelta(
            days=DAYS_AHEAD
        )
    )

    local_now = now.astimezone(
        LOCAL_TZ
    )

    local_end = end_time.astimezone(
        LOCAL_TZ
    )

    start_date = (
        local_now.date()
        .strftime(
            "%Y-%m-%d"
        )
    )

    end_date = (
        local_end.date()
        .strftime(
            "%Y-%m-%d"
        )
    )

    print(
        "===================================="
    )

    print(
        "[SPORTMONKS]"
    )

    print(
        "[TOKEN]",
        bool(
            SPORTMONKS_API_TOKEN
        ),
    )

    print(
        "[START DATE]",
        start_date,
    )

    print(
        "[END DATE]",
        end_date,
    )

    print(
        "[TIMEZONE]",
        LOCAL_TIMEZONE_NAME,
    )

    print(
        "[DAYS]",
        DAYS_AHEAD,
    )

    print(
        "===================================="
    )

    fixtures = get_fixtures_between(
        start_date,
        end_date,
    )

    print(
        "[FIXTURES RECEIVED]",
        len(
            fixtures
        ),
    )

    result = []

    for fixture in fixtures:

        try:

            starting_at = (
                fixture.get(
                    "starting_at"
                )
                or fixture.get(
                    "startingAt"
                )
            )

            dt = parse_sportmonks_datetime(
                starting_at
            )

            if not dt:
                continue

            if dt < now:
                continue

            if dt > end_time:
                continue

            odds = extract_odds(
                fixture
            )

            if not odds:

                print(
                    "[NO ODDS]",
                    fixture.get(
                        "id"
                    ),
                    fixture.get(
                        "name"
                    ),
                )

                continue

            converted = convert_fixture(
                fixture
            )

            if not converted.get(
                "h2h"
            ):

                print(
                    "[NO H2H]",
                    fixture.get(
                        "id"
                    ),
                    fixture.get(
                        "name"
                    ),
                )

                continue

            result.append(
                converted
            )

        except Exception as exc:

            print(
                "[FIXTURE ERROR]",
                repr(exc),
            )

    # =====================================================
    # UNIQUE
    # =====================================================

    unique = {}

    for match in result:

        key = str(
            match.get(
                "id",
                ""
            )
        )

        if key:

            unique[
                key
            ] = match

    result = list(
        unique.values()
    )

    # =====================================================
    # SORT
    # =====================================================

    result.sort(

        key=lambda x:
            x.get(
                "commence_time",
                "",
            )

    )

    print(
        "[MATCHES WITH ODDS]",
        len(
            result
        ),
    )

    return (
        result,
        [],
    )


# =========================================================
# REFRESH CACHE
# =========================================================

def refresh_matches(
    force=False,
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
                    ] = None

                else:

                    MATCH_CACHE[
                        "error"
                    ] = (

                        errors[-10:]

                        if errors

                        else
                        "SportMonks irraa "
                        "odds qaban match "
                        "hin argamne."

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
                len(
                    matches
                ),
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
                ] = str(
                    exc
                )

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
    force=False,
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


# =========================================================
# GET MATCHES
# =========================================================

def get_matches(
    force=False,
):

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

    if (
        not force
        and fresh
    ):

        return cached_matches

    if not refreshing:

        start_refresh_background(
            force=force
        )

    with CACHE_LOCK:

        cached_matches = list(
            MATCH_CACHE[
                "matches"
            ]
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
                MATCH_CACHE[
                    "matches"
                ]
            )

    return cached_matches


# =========================================================
# SINGLE SPORTMONKS FIXTURE
# =========================================================

def get_fixture_by_id(
    fixture_id,
):

    body = sportmonks_request(

        f"/fixtures/{fixture_id}",

        {

            "include":
                "participants;league;odds",

            "timezone":
                LOCAL_TIMEZONE_NAME,

        },

    )

    data = response_data(
        body
    )

    if not data:
        return None

    return data[0]


# =========================================================
# LIVE FIXTURES
# =========================================================

def get_live_fixtures():

    includes = (
        "participants;"
        "league;"
        "state;"
        "scores"
    )

    endpoints = [
        "/livescores/inplay",
        "/livescores/latest",
    ]

    last_error = None

    for endpoint in endpoints:

        try:

            body = sportmonks_request(

                endpoint,

                {
                    "include":
                        includes,

                    "timezone":
                        LOCAL_TIMEZONE_NAME,

                },

            )

            data = response_data(
                body
            )

            if data:

                return data

        except Exception as exc:

            last_error = str(
                exc
            )

            print(
                "[LIVE ERROR]",
                endpoint,
                repr(exc),
            )

    if last_error:

        raise RuntimeError(
            last_error
        )

    return []


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

    if not user:
        return

    get_user(

        user.id,

        user.first_name
        or "User",

    )

    if not update.message:
        return

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

    if not query:
        return

    await query.answer()

    user = query.from_user

    u = get_user(

        user.id,

        user.first_name
        or "User",

    )

    # =====================================================
    # PROFILE
    # =====================================================

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

    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        await query.edit_message_text(

            f"💳 *BALANCE*\n\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n\n"

            "🧪 Demo system qofa.",

            reply_markup=main_menu(),

            parse_mode="Markdown",

        )

    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        if not u[
            "history"
        ]:

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
                    f"{item.get('time', '')}\n"

                    f"💰 "
                    f"{float(item.get('stake', 0)):.2f}"
                    f" | "

                    f"📈 "
                    f"{float(item.get('odds', 0)):.2f}"
                    f" | "

                    f"{item.get('status', '')}\n\n"

                )

        await query.edit_message_text(

            text,

            reply_markup=main_menu(),

            parse_mode="Markdown",

        )

    # =====================================================
    # HOW TO
    # =====================================================

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

        "status":
            "online",

        "bot":
            "Best Bet",

        "api":
            "SportMonks",

        "api_key_configured":
            bool(
                SPORTMONKS_API_TOKEN
            ),

        "web_app":
            WEB_APP_URL,

        "timezone":
            LOCAL_TIMEZONE_NAME,

        "days":
            DAYS_AHEAD,

        "cache_seconds":
            CACHE_SECONDS,

        "api_timeout":
            API_TIMEOUT,

        "sportmonks_max_pages":
            SPORTMONKS_MAX_PAGES,

        "sportmonks_per_page":
            SPORTMONKS_PER_PAGE,

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


# =========================================================
# SPORTMONKS API TEST
# =========================================================

@app.route(
    "/api/sportmonks-test",
    methods=["GET"],
)
def sportmonks_test():

    try:

        now = datetime.now(
            LOCAL_TZ
        )

        end = (
            now
            + timedelta(
                days=1
            )
        )

        start_date = (
            now.date().strftime(
                "%Y-%m-%d"
            )
        )

        end_date = (
            end.date().strftime(
                "%Y-%m-%d"
            )
        )

        body = sportmonks_request(

            (
                "/fixtures/between/"
                f"{start_date}/"
                f"{end_date}"
            ),

            {

                "include":
                    "participants;league;odds",

                "per_page":
                    5,

                "timezone":
                    LOCAL_TIMEZONE_NAME,

            },

        )

        data = response_data(
            body
        )

        return jsonify({

            "success":
                True,

            "message":
                "SportMonks API connected.",

            "api":
                "SportMonks",

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "fixtures_found":
                len(data),

            "sample": [

                {

                    "id":
                        x.get(
                            "id"
                        ),

                    "name":
                        x.get(
                            "name"
                        ),

                    "starting_at":
                        x.get(
                            "starting_at"
                        ),

                    "odds_count":
                        len(
                            extract_odds(
                                x
                            )
                        ),

                }

                for x in data[:5]

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

    except Exception as exc:

        return jsonify({

            "success":
                False,

            "message":
                "SportMonks API connection failed.",

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

            "success":
                True,

            "count":
                len(matches),

            "matches":
                matches,

            "message": (

                "Football odds loaded."

                if matches

                else
                "Matches are loading. "
                "Please refresh shortly."

            ),

            "loading": (

                refreshing
                and not bool(matches)

            ),

            "stale":
                stale,

            "api":
                "SportMonks",

            "api_key_configured":
                bool(
                    SPORTMONKS_API_TOKEN
                ),

            "timezone":
                LOCAL_TIMEZONE_NAME,

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

            "success":
                False,

            "count":
                0,

            "matches":
                [],

            "error":
                str(exc),

            "message":
                "Football odds loading failed.",

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


# =========================================================
# SINGLE MATCH DETAILS
# =========================================================

@app.route(
    "/api/match/<match_id>",
    methods=["GET"],
)
def api_match(
    match_id,
):

    try:

        # -------------------------------------------------
        # FIRST TRY CACHE
        # -------------------------------------------------

        matches = get_matches()

        match = next(

            (

                item

                for item in matches

                if str(
                    item.get(
                        "id"
                    )
                )
                == str(
                    match_id
                )

            ),

            None,

        )

        # -------------------------------------------------
        # IF NOT CACHE, FETCH DIRECTLY
        # -------------------------------------------------

        fixture = get_fixture_by_id(
            match_id
        )

        if fixture:

            converted_direct = (
                convert_fixture(
                    fixture
                )
            )

            if not match:

                match = converted_direct

        if not match:

            return jsonify({

                "success":
                    False,

                "error":
                    "Match hin argamne.",

                "markets":
                    [],

            }), 200

        # -------------------------------------------------
        # DIRECT FIXTURE DATA
        # -------------------------------------------------

        if fixture:

            converted = convert_fixture(
                fixture
            )

        else:

            converted = match

        markets = []

        # =================================================
        # H2H
        # =================================================

        h2h = (
            converted.get(
                "h2h"
            )
            or {}
        )

        if h2h:

            selections = []

            if h2h.get(
                "home"
            ):

                selections.append({

                    "value":
                        "1",

                    "odd":
                        h2h[
                            "home"
                        ],

                })

            if h2h.get(
                "draw"
            ):

                selections.append({

                    "value":
                        "X",

                    "odd":
                        h2h[
                            "draw"
                        ],

                })

            if h2h.get(
                "away"
            ):

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

        # =================================================
        # TOTALS
        # =================================================

        totals = (
            converted.get(
                "totals"
            )
            or {}
        )

        if totals:

            selections = []

            if totals.get(
                "over"
            ):

                selections.append({

                    "value":
                        "Over 2.5",

                    "odd":
                        totals[
                            "over"
                        ],

                })

            if totals.get(
                "under"
            ):

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

        # =================================================
        # BTTS
        # =================================================

        btts = (
            converted.get(
                "btts"
            )
            or {}
        )

        if btts:

            selections = []

            if btts.get(
                "yes"
            ):

                selections.append({

                    "value":
                        "BTTS Yes",

                    "odd":
                        btts[
                            "yes"
                        ],

                })

            if btts.get(
                "no"
            ):

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

        # =================================================
        # SPREADS
        # =================================================

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

                    "value": (

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

            "api":
                "SportMonks",

            "api_last_status":
                API_STATS[
                    "last_status"
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

        fixtures = (
            get_live_fixtures()
        )

        result = []

        for fixture in fixtures:

            try:

                home, away = (
                    get_fixture_teams(
                        fixture
                    )
                )

                league = "Football"

                league_relation = (
                    relation_data(
                        fixture,
                        "league"
                    )
                )

                if league_relation:

                    league = (

                        league_relation[0].get(
                            "name"
                        )

                        or "Football"

                    )

                state_name = ""

                states = relation_data(
                    fixture,
                    "state"
                )

                if states:

                    state_name = (

                        states[0].get(
                            "name"
                        )
                        or states[0].get(
                            "short_name"
                        )
                        or ""

                    )

                score_map = {}

                scores = relation_data(
                    fixture,
                    "scores"
                )

                for score in scores:

                    if not isinstance(
                        score,
                        dict,
                    ):
                        continue

                    participant_id = (
                        score.get(
                            "participant_id"
                        )
                    )

                    score_obj = (
                        score.get(
                            "score"
                        )
                    )

                    if isinstance(
                        score_obj,
                        dict,
                    ):

                        score_value = (
                            score_obj.get(
                                "goals"
                            )
                            or score_obj.get(
                                "score"
                            )
                        )

                    else:

                        score_value = (
                            score_obj
                        )

                    if participant_id is not None:

                        score_map[
                            str(
                                participant_id
                            )
                        ] = score_value

                participants = relation_data(
                    fixture,
                    "participants"
                )

                home_id = None
                away_id = None

                for participant in participants:

                    pid = participant.get(
                        "id"
                    )

                    meta = (
                        participant.get(
                            "meta"
                        )
                        or {}
                    )

                    location = str(
                        meta.get(
                            "location",
                            "",
                        )
                    ).lower()

                    if location == "home":

                        home_id = pid

                    elif location == "away":

                        away_id = pid

                result.append({

                    "id":
                        fixture.get(
                            "id"
                        ),

                    "league":
                        league,

                    "home":
                        home,

                    "away":
                        away,

                    "home_score":
                        score_map.get(
                            str(
                                home_id
                            )
                        )
                        if home_id is not None
                        else None,

                    "away_score":
                        score_map.get(
                            str(
                                away_id
                            )
                        )
                        if away_id is not None
                        else None,

                    "state":
                        state_name,

                    "minute":
                        "",

                })

            except Exception as exc:

                print(
                    "[LIVE FIXTURE ERROR]",
                    repr(exc),
                )

        return jsonify({

            "success":
                True,

            "count":
                len(result),

            "matches":
                result,

            "api":
                "SportMonks",

        }), 200

    except Exception as exc:

        print(
            "[LIVE ERROR]",
            repr(exc),
        )

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
def handle_404(
    error,
):

    if request.path.startswith(
        "/api/"
    ):

        return jsonify({

            "success":
                False,

            "error": (

                "API endpoint not found: "
                f"{request.path}"

            ),

        }), 404

    return (

        "<h1>BEST BET</h1>"
        "<p>Page not found.</p>"

    ), 404


@app.errorhandler(500)
def handle_500(
    error,
):

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

            .token(
                BOT_TOKEN
            )

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
            "API: SPORTMONKS"
        )

        print(
            "===================================="
        )

        application.run_polling(

            allowed_updates=
                Update.ALL_TYPES,

            close_loop=False,

            stop_signals=None,

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
        "SPORTMONKS TOKEN:",
        bool(
            SPORTMONKS_API_TOKEN
        ),
    )

    print(
        "TIMEZONE:",
        LOCAL_TIMEZONE_NAME,
    )

    print(
        "DAYS:",
        DAYS_AHEAD,
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
        "MAX PAGES:",
        SPORTMONKS_MAX_PAGES,
    )

    print(
        "PER PAGE:",
        SPORTMONKS_PER_PAGE,
    )

    print(
        "PORT:",
        PORT,
    )

    print(
        "===================================="
    )

    # =====================================================
    # CACHE THREAD
    # =====================================================

    cache_thread = threading.Thread(

        target=cache_refresh_loop,

        daemon=True,

        name="cache-refresh-loop",

    )

    cache_thread.start()

    # =====================================================
    # TELEGRAM
    # =====================================================

    if BOT_TOKEN:

        bot_thread = threading.Thread(

            target=run_telegram_bot,

            daemon=True,

            name="telegram-bot",

        )

        bot_thread.start()

    # =====================================================
    # FLASK
    # =====================================================

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
