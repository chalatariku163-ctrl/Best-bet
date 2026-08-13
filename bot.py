import os
import threading
import time
from datetime import datetime, timezone, timedelta

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

ODDS_API_KEY = os.getenv(
    "ODDS_API_KEY",
    ""
).strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip()

PORT = int(
    os.getenv("PORT", "10000")
)

ODDS_BASE = "https://api.the-odds-api.com/v4"

REGIONS = os.getenv(
    "ODDS_REGIONS",
    "eu"
).strip()

# Core soccer markets supported broadly by The Odds API.
MARKETS = os.getenv(
    "ODDS_MARKETS",
    "h2h,totals,spreads"
).strip()

# Today + next 7 days
DAYS_AHEAD = 7

# Limit number of soccer competitions to avoid quota problems.
MAX_SOCCER_SPORTS = int(
    os.getenv(
        "MAX_SOCCER_SPORTS",
        "40"
    )
)

# Cache matches for 5 minutes.
CACHE_SECONDS = int(
    os.getenv(
        "CACHE_SECONDS",
        "300"
    )
)


# =========================================================
# FLASK
# =========================================================

web_app = Flask(__name__)


# =========================================================
# GLOBAL DATA
# =========================================================

USERS = {}

MATCH_CACHE = {
    "time": 0,
    "matches": None,
    "error": None,
}

CACHE_LOCK = threading.Lock()


# =========================================================
# USER DATA
# =========================================================

def get_user(
    user_id,
    name="User"
):

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

    for item in get_user(
        user_id
    )["betslip"]:

        try:
            total *= float(
                item["odd"]
            )

        except Exception:
            pass

    return total


def add_demo_bet(
    user_id,
    match,
    market,
    selection,
    odd,
    bet_id=None
):

    try:
        odd = float(odd)

    except Exception:
        return False

    if odd <= 1:
        return False

    user = get_user(
        user_id
    )

    user["betslip"] = [

        x
        for x in user["betslip"]

        if not (
            str(
                x["fixture_id"]
            )
            == str(
                match["id"]
            )

            and

            str(
                x.get("bet_id")
            )
            == str(
                bet_id
            )
        )
    ]

    user["betslip"].append({

        "fixture_id":
            match["id"],

        "home":
            match["home"],

        "away":
            match["away"],

        "league":
            match.get(
                "league",
                ""
            ),

        "market":
            market,

        "selection":
            selection,

        "odd":
            odd,

        "bet_id":
            bet_id,
    })

    return True


def betslip_text(user_id):

    user = get_user(
        user_id
    )

    slips = user["betslip"]

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
        1
    ):

        text += (

            f"*{i}.* "
            f"{item['home']} vs "
            f"{item['away']}\n"

            f"🏆 {item.get('league', '')}\n"

            f"📊 {item['market']}\n"

            f"🎯 *{item['selection']}*\n"

            f"Odd: *{item['odd']:.2f}*\n\n"
        )

    text += (

        "━━━━━━━━━━━━━━\n"

        f"📈 *Total Odds:* "
        f"{total:.2f}\n\n"

        "🧪 Demo/testing qofa."
    )

    return text


# =========================================================
# ODDS API
# =========================================================

def odds_request(
    path,
    params=None
):

    if not ODDS_API_KEY:

        raise RuntimeError(
            "ODDS_API_KEY hin jiru. "
            "Render Environment Variables keessatti "
            "ODDS_API_KEY galchi."
        )

    final_params = dict(
        params or {}
    )

    final_params["apiKey"] = (
        ODDS_API_KEY
    )

    response = requests.get(

        ODDS_BASE + path,

        params=final_params,

        timeout=35,

        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Best-Bet/1.0",
        },
    )

    if response.status_code != 200:

        try:
            body = response.json()

        except Exception:
            body = response.text[:1500]

        raise RuntimeError(

            f"Odds API error "
            f"{response.status_code}: "
            f"{body}"
        )

    try:

        return response.json()

    except Exception:

        raise RuntimeError(
            "Odds API JSON response "
            "sirriitti hin dubbifamne."
        )


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
            sport.get(
                "key",
                ""
            )
        )

        if not key.startswith(
            "soccer_"
        ):
            continue

        # Active competitions only.
        if not sport.get(
            "active",
            False
        ):
            continue

        result.append(
            sport
        )

    return result


# =========================================================
# TIME HELPERS
# =========================================================

ETHIOPIA_TZ = timezone(
    timedelta(
        hours=3
    )
)


def format_local_time(
    commence
):

    if not commence:
        return ""

    try:

        dt = datetime.fromisoformat(
            commence.replace(
                "Z",
                "+00:00"
            )
        )

        local_dt = dt.astimezone(
            ETHIOPIA_TZ
        )

        return local_dt.strftime(
            "%H:%M"
        )

    except Exception:

        return ""


def local_date_key(
    commence
):

    if not commence:
        return ""

    try:

        dt = datetime.fromisoformat(
            commence.replace(
                "Z",
                "+00:00"
            )
        )

        local_dt = dt.astimezone(
            ETHIOPIA_TZ
        )

        return local_dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:

        return ""


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(
    event,
    sport
):

    h2h = {}

    totals = {}

    spreads = []

    # -----------------------------------------------------
    # Read bookmaker markets
    # -----------------------------------------------------

    for bookmaker in event.get(
        "bookmakers",
        []
    ):

        for market in bookmaker.get(
            "markets",
            []
        ):

            market_key = market.get(
                "key"
            )

            for outcome in market.get(
                "outcomes",
                []
            ):

                name = str(
                    outcome.get(
                        "name",
                        ""
                    )
                )

                price = outcome.get(
                    "price"
                )

                if price is None:
                    continue

                try:

                    price = float(
                        price
                    )

                except Exception:
                    continue

                if price <= 1:
                    continue

                # =================================================
                # 1X2
                # =================================================

                if market_key == "h2h":

                    if (
                        name
                        == event.get(
                            "home_team"
                        )
                    ):

                        h2h.setdefault(
                            "home",
                            price
                        )

                    elif (
                        name
                        == event.get(
                            "away_team"
                        )
                    ):

                        h2h.setdefault(
                            "away",
                            price
                        )

                    elif name.lower() == "draw":

                        h2h.setdefault(
                            "draw",
                            price
                        )

                # =================================================
                # TOTALS
                # =================================================

                elif market_key == "totals":

                    point = outcome.get(
                        "point"
                    )

                    if point is None:
                        continue

                    try:

                        point = float(
                            point
                        )

                    except Exception:
                        continue

                    # Keep the common 2.5 line.
                    if point == 2.5:

                        if name.lower() == "over":

                            totals.setdefault(
                                "over",
                                price
                            )

                        elif name.lower() == "under":

                            totals.setdefault(
                                "under",
                                price
                            )

                # =================================================
                # SPREADS / HANDICAP
                # =================================================

                elif market_key == "spreads":

                    point = outcome.get(
                        "point"
                    )

                    spreads.append({

                        "name":
                            name,

                        "point":
                            point,

                        "price":
                            price,
                    })


    # =====================================================
    # REMOVE DUPLICATE SPREADS
    # =====================================================

    unique_spreads = []

    seen = set()

    for item in spreads:

        key = (
            item["name"],
            str(
                item["point"]
            ),
            item["price"],
        )

        if key in seen:
            continue

        seen.add(key)

        unique_spreads.append(
            item
        )


    # =====================================================
    # BEST BET
    # =====================================================

    candidates = []

    if h2h.get("home"):

        candidates.append(
            (
                "1",
                h2h["home"],
                "1X2"
            )
        )

    if h2h.get("draw"):

        candidates.append(
            (
                "X",
                h2h["draw"],
                "1X2"
            )
        )

    if h2h.get("away"):

        candidates.append(
            (
                "2",
                h2h["away"],
                "1X2"
            )
        )

    if totals.get("over"):

        candidates.append(
            (
                "Over 2.5",
                totals["over"],
                "Over/Under"
            )
        )

    if totals.get("under"):

        candidates.append(
            (
                "Under 2.5",
                totals["under"],
                "Over/Under"
            )
        )

    candidates = [

        x
        for x in candidates

        if (
            1.01
            < x[1]
            <= 20
        )
    ]

    candidates.sort(
        key=lambda x: x[1]
    )

    best = None

    if candidates:

        selection, odd, market = (
            candidates[0]
        )

        best = {

            "selection":
                selection,

            "odd":
                odd,

            "market":
                market,
        }


    # =====================================================
    # RESULT
    # =====================================================

    commence = event.get(
        "commence_time",
        ""
    )

    return {

        "id":
            event.get(
                "id"
            ),

        "sport_key":
            sport.get(
                "key"
            ),

        "league":
            sport.get(
                "title",
                "Football"
            ),

        "home":
            event.get(
                "home_team",
                "Home"
            ),

        "away":
            event.get(
                "away_team",
                "Away"
            ),

        "time":
            format_local_time(
                commence
            ),

        "local_date":
            local_date_key(
                commence
            ),

        "commence_time":
            commence,

        "h2h":
            h2h,

        "totals":
            totals,

        "spreads":
            unique_spreads,

        "best_bet":
            best,
    }


# =========================================================
# GET MATCHES
# =========================================================

def fetch_matches_from_api():

    sports = soccer_sports()

    if not sports:

        return []


    print(
        "===================================="
    )

    print(
        "[SOCCER SPORTS]",
        len(sports)
    )

    print(
        [
            x.get("key")
            for x in sports
        ][:50]
    )

    print(
        "===================================="
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


    start_text = (
        now.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )

    end_text = (
        end_time.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


    result = []

    errors = []


    # -----------------------------------------------------
    # Soccer competitions
    # -----------------------------------------------------

    selected_sports = sports[
        :MAX_SOCCER_SPORTS
    ]


    for sport in selected_sports:

        sport_key = sport.get(
            "key"
        )

        if not sport_key:
            continue


        try:

            events = odds_request(

                f"/sports/"
                f"{sport_key}/odds",

                {

                    "regions":
                        REGIONS,

                    "markets":
                        MARKETS,

                    "oddsFormat":
                        "decimal",

                    "dateFormat":
                        "iso",

                    "commenceTimeFrom":
                        start_text,

                    "commenceTimeTo":
                        end_text,
                },
            )


            print(
                "[ODDS]",
                sport_key,
                "events:",
                len(events)
            )


            for event in events:

                try:

                    converted = convert_event(
                        event,
                        sport
                    )

                    # Keep event if at least
                    # one usable market exists.
                    if (
                        converted["h2h"]
                        or converted["totals"]
                        or converted["spreads"]
                    ):

                        result.append(
                            converted
                        )

                except Exception as e:

                    print(
                        "[EVENT ERROR]",
                        sport_key,
                        e
                    )


        except Exception as e:

            error_text = (
                f"{sport_key}: {e}"
            )

            print(
                "[SPORT ERROR]",
                error_text
            )

            errors.append(
                error_text
            )


    # -----------------------------------------------------
    # Remove duplicates
    # -----------------------------------------------------

    unique = {}

    for match in result:

        match_id = str(
            match.get(
                "id"
            )
        )

        if match_id:

            unique[
                match_id
            ] = match


    result = list(
        unique.values()
    )


    # -----------------------------------------------------
    # Sort by commencement time
    # -----------------------------------------------------

    result.sort(
        key=lambda x:
        x.get(
            "commence_time"
        ) or ""
    )


    print(
        "===================================="
    )

    print(
        "[TOTAL MATCHES]",
        len(result)
    )

    print(
        "===================================="
    )


    return result, errors


# =========================================================
# CACHED GET MATCHES
# =========================================================

def get_matches(
    force=False
):

    current = time.time()

    with CACHE_LOCK:

        if (
            not force
            and MATCH_CACHE["matches"]
            is not None
            and
            current
            - MATCH_CACHE["time"]
            < CACHE_SECONDS
        ):

            return (
                MATCH_CACHE["matches"],
                MATCH_CACHE["error"] or []
            )


    matches, errors = (
        fetch_matches_from_api()
    )


    with CACHE_LOCK:

        MATCH_CACHE["time"] = (
            time.time()
        )

        MATCH_CACHE["matches"] = (
            matches
        )

        MATCH_CACHE["error"] = (
            errors
        )


    return (
        matches,
        errors
    )


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


# =========================================================
# START
# =========================================================

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
    context: ContextTypes.DEFAULT_TYPE
):

    q = update.callback_query

    await q.answer()

    user = q.from_user

    u = get_user(
        user.id,
        user.first_name or "User"
    )


    if q.data == "profile":

        await q.edit_message_text(

            f"👤 *PROFILE*\n\n"

            f"Name: *{u['name']}*\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n"

            f"Bet Slip: "
            f"*{len(u['betslip'])}*",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )


    elif q.data == "balance":

        await q.edit_message_text(

            f"💳 *BALANCE*\n\n"

            f"Balance: "
            f"*{u['balance']:.2f}*\n\n"

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

            for item in (
                u["history"][-10:]
            ):

                text += (

                    f"🕐 {item['time']}\n"

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
            "hanga guyyaa 7 agarsiisa.\n\n"

            "🧪 Demo/testing qofa.",

            reply_markup=main_menu(),

            parse_mode="Markdown",
        )


# =========================================================
# WEB APP HTML
# =========================================================

HTML = r"""
<!doctype html>

<html lang="en">

<head>

<meta charset="utf-8">

<meta
 name="viewport"
 content="width=device-width,initial-scale=1"
>

<title>BEST BET</title>

<style>

*{
 box-sizing:border-box
}

body{
 margin:0;
 background:#0b1420;
 color:#fff;
 font-family:Arial,sans-serif;
}

header{
 background:#142638;
 padding:18px 14px;
 text-align:center;
}

.logo{
 font-size:31px;
 font-weight:900;
 color:#ffd400;
}

.sub{
 font-size:12px;
 color:#9fb0c2;
 margin-top:5px;
}

nav{
 display:grid;
 grid-template-columns:repeat(4,1fr);
 gap:6px;
 padding:8px;
 background:#101e2c;
 position:sticky;
 top:0;
 z-index:10;
}

nav button{
 border:0;
 border-radius:11px;
 padding:9px 3px;
 background:#24384b;
 color:#fff;
 font-weight:800;
}

nav button.active{
 background:#ffd400;
 color:#111;
}

main{
 padding:12px;
 max-width:950px;
 margin:auto;
}

.panel{
 background:#142638;
 border-radius:18px;
 padding:13px;
}

.days{
 display:flex;
 gap:7px;
 overflow-x:auto;
 padding:4px 0 12px;
}

.days button{
 flex:0 0 auto;
 border:0;
 border-radius:11px;
 padding:10px 13px;
 background:#263c50;
 color:#fff;
 font-weight:800;
}

.days button.active{
 background:#ffd400;
 color:#111;
}

.day-title{
 font-size:20px;
 font-weight:900;
 margin:5px 0 12px;
}

.match{
 background:#1a2c3d;
 border-radius:16px;
 padding:14px;
 margin:11px 0;
}

.teams{
 font-size:17px;
 font-weight:900;
 line-height:1.4;
}

.meta{
 font-size:12px;
 color:#9fb0c2;
 margin:7px 0 11px;
}

.odds{
 display:grid;
 grid-template-columns:repeat(3,1fr);
 gap:7px;
}

.odd{
 background:#263b4f;
 padding:12px 5px;
 border-radius:11px;
 text-align:center;
 cursor:pointer;
 border:2px solid transparent;
}

.odd b{
 display:block;
 font-size:17px;
 margin-top:3px;
}

.odd.selected{
 background:#2ecc71;
 color:#111;
 border-color:#fff;
}

.market{
 background:#172a3b;
 border-radius:14px;
 margin-top:12px;
 overflow:hidden;
}

.market-title{
 padding:11px;
 font-weight:900;
 background:#20364a;
}

.market-grid{
 display:grid;
 grid-template-columns:repeat(2,1fr);
 gap:8px;
 padding:9px;
}

.selection{
 background:#263b4f;
 border:2px solid transparent;
 border-radius:11px;
 padding:11px;
 cursor:pointer;
 text-align:left;
 color:#fff;
}

.selection.selected{
 background:#2ecc71;
 color:#111;
 border-color:#fff;
}

.selection span{
 display:block;
 font-size:12px;
 color:#b9c8d4;
}

.selection.selected span{
 color:#111;
}

.selection b{
 display:block;
 margin-top:4px;
}

.big{
 width:100%;
 border:0;
 border-radius:12px;
 padding:13px;
 margin-top:10px;
 background:#ffd400;
 color:#111;
 font-weight:900;
 cursor:pointer;
}

.back{
 background:#263b4f;
 color:#fff;
}

.yellow{
 color:#ffd400;
}

.status{
 color:#9fb0c2;
 font-size:13px;
}

.error{
 color:#ff7979;
 white-space:pre-wrap;
}

.empty{
 text-align:center;
 padding:25px 10px;
 color:#9fb0c2;
}

.slip-item{
 background:#1a2c3d;
 padding:12px;
 border-radius:12px;
 margin:8px 0;
}

.best-card{
 border:1px solid #ffd400;
}

.info{
 background:#20364a;
 border-radius:12px;
 padding:11px;
 margin:10px 0;
}

@media(max-width:500px){

 .teams{
   font-size:15px;
 }

 .odds{
   gap:5px;
 }

 .odd{
   padding:10px 3px;
 }

}

</style>

</head>

<body>

<header>

<div class="logo">
🎯 BEST BET
</div>

<div class="sub">
Football • Today → Next 7 Days • Multiple Markets
</div>

</header>


<nav>

<button
 class="active"
 onclick="tab('matches',this)"
>
📅<br>Matches
</button>

<button
 onclick="tab('best',this)"
>
🎯<br>Best Bet
</button>

<button
 onclick="tab('live',this)"
>
🔴<br>Live
</button>

<button
 onclick="tab('slip',this)"
>
🎟️<br>Bet Slip
</button>

</nav>


<main>

<section
 id="content"
 class="panel"
>
Loading...
</section>

</main>


<script>

let matches=[];

let slip=JSON.parse(
 localStorage.getItem(
   "bestbet_slip"
 ) || "[]"
);


function esc(x){

 return String(x ?? "")
 .replace(
   /[&<>"']/g,
   m=>({
     "&":"&amp;",
     "<":"&lt;",
     ">":"&gt;",
     '"':"&quot;",
     "'":"&#039;"
   }[m])
 );

}


function saveSlip(){

 localStorage.setItem(
   "bestbet_slip",
   JSON.stringify(slip)
 );

}


async function api(path){

 const r=await fetch(path);

 let d={};

 try{
   d=await r.json();
 }catch(e){
   throw new Error(
     "Server response hin sirre."
   );
 }

 if(!r.ok){

   throw new Error(
     d.error ||
     "Server error"
   );

 }

 return d;

}


function tab(
 name,
 btn
){

 document
 .querySelectorAll(
   "nav button"
 )
 .forEach(
   x=>x.classList.remove(
     "active"
   )
 );

 btn.classList.add(
   "active"
 );

 if(name==="matches")
   loadMatches();

 if(name==="best")
   loadBest();

 if(name==="live")
   loadLive();

 if(name==="slip")
   renderSlip();

}


function dayKey(date){

 if(!date)
   return "";

 const d=new Date(date);

 return d.toLocaleDateString(
   "en-CA",
   {
     timeZone:
       "Africa/Addis_Ababa"
   }
 );

}


function dayLabel(
 key
){

 if(!key)
   return "";

 const parts=
   key.split("-");

 if(parts.length!==3)
   return key;

 const d=new Date(
   Number(parts[0]),
   Number(parts[1])-1,
   Number(parts[2])
 );

 const today=new Date();

 const todayKey=
   today.toLocaleDateString(
     "en-CA",
     {
       timeZone:
         "Africa/Addis_Ababa"
     }
   );

 const tomorrow=
   new Date(
     today.getTime()
     +
     86400000
   );

 const tomorrowKey=
   tomorrow.toLocaleDateString(
     "en-CA",
     {
       timeZone:
         "Africa/Addis_Ababa"
     }
   );

 if(key===todayKey)
   return "TODAY";

 if(key===tomorrowKey)
   return "TOMORROW";

 return d.toLocaleDateString(
   undefined,
   {
     weekday:"short",
     day:"numeric",
     month:"short"
   }
 );

}


function loadMatches(){

 const c=
 document.getElementById(
   "content"
 );

 c.innerHTML=
 `
 <div class="status">
 ⏳ Loading football matches...
 </div>
 `;

 api(
   "/api/matches"
 )

 .then(d=>{

   matches=
     d.matches || [];

   if(!matches.length){

     let errorText=
       d.error || "";

     c.innerHTML=
     `
     <h2>⚽ Football</h2>

     ${
       errorText
       ?
       `
       <div class="error">
       ⚠️ ${esc(errorText)}
       </div>
       `
       :
       `
       <div class="empty">

       No football matches with
       available odds found in the
       next 7 days.

       <br><br>

       <span class="status">
       ${esc(
         d.message || ""
       )}
       </span>

       </div>
       `
     }

     `;

     return;
   }

   renderDayTabs();

 })

 .catch(e=>{

   c.innerHTML=
   `
   <h2>⚠️ Error</h2>

   <div class="error">
   ${esc(e.message)}
   </div>

   <button
     class="big"
     onclick="loadMatches()"
   >
   🔄 Try Again
   </button>
   `;

 });

}


function renderDayTabs(){

 const c=
 document.getElementById(
   "content"
 );

 const groups={};

 matches.forEach(m=>{

   const key=
     m.local_date ||
     dayKey(
       m.commence_time
     );

   if(!groups[key])
     groups[key]=[];

   groups[key].push(m);

 });


 const keys=
 Object.keys(groups)
 .sort();

 if(!keys.length){

   c.innerHTML=
   `
   <div class="empty">
   No matches found.
   </div>
   `;

   return;
 }


 let html=
 `
 <h2>⚽ Football</h2>

 <div class="status">
 Today → next 7 days
 </div>

 <div class="days">
 `;


 keys.forEach(
   (key,i)=>{

     html+=
     `
     <button
       class="${i===0?'active':''}"
       onclick="showDay(
         '${key}',
         this
       )"
     >
       ${esc(
         dayLabel(key)
       )}
       <br>
       <small>
       ${groups[key].length}
       </small>
     </button>
     `;

   }
 );


 html+=
 `
 </div>

 <div id="dayMatches"></div>
 `;


 c.innerHTML=html;


 showDay(
   keys[0],
   document.querySelector(
     ".days button"
   )
 );

}


function showDay(
 key,
 btn
){

 document
 .querySelectorAll(
   ".days button"
 )
 .forEach(
   x=>x.classList.remove(
     "active"
   )
 );

 if(btn)
   btn.classList.add(
     "active"
   );


 const list=
 matches.filter(
   m=>
     (
       m.local_date ||
       dayKey(
         m.commence_time
       )
     )===key
 );


 const box=
 document.getElementById(
   "dayMatches"
 );


 if(!box)
   return;


 let html=
 `
 <div class="day-title">
 ${esc(
   dayLabel(key)
 )}
 </div>
 `;


 if(!list.length){

   html+=
   `
   <div class="empty">
   No matches.
   </div>
   `;

 }else{

   html+=
     list
     .map(
       renderMatch
     )
     .join("");

 }


 box.innerHTML=html;

}


function renderMatch(
 m
){

 const h=
   m.h2h || {};

 return `
 <div class="match">

   <div class="teams">
     ${esc(m.home)}
     <span class="status">
     vs
     </span>
     ${esc(m.away)}
   </div>

   <div class="meta">
     🏆 ${esc(m.league)}
     •
     🕐 ${esc(m.time)}
   </div>

   <div class="odds">

     ${oddButton(
       m,
       "1",
       h.home
     )}

     ${oddButton(
       m,
       "X",
       h.draw
     )}

     ${oddButton(
       m,
       "2",
       h.away
     )}

   </div>

   <button
     class="big back"
     onclick="openMatch(
       '${esc(m.id)}'
     )"
   >
     📊 View All Markets
   </button>

 </div>
 `;

}


function isSelected(
 id,
 betId,
 selection
){

 return slip.some(
   x=>
     String(x.id)
     ===
     String(id)

     &&

     String(x.betId)
     ===
     String(betId)

     &&

     x.selection
     ===
     selection
 );

}


function oddButton(
 m,
 label,
 odd
){

 if(
   odd === undefined
   ||
   odd === null
 ){

   return `
   <div class="odd">
     ${label}
     <b>-</b>
   </div>
   `;

 }


 const selected=
   isSelected(
     m.id,
     "h2h",
     label
   );


 return `
 <div
   class="odd ${
     selected
       ? "selected"
       : ""
   }"

   onclick='selectBet(
     ${JSON.stringify({

       id:m.id,

       home:m.home,

       away:m.away,

       league:m.league,

       market:"1X2",

       selection:label,

       odd:Number(odd),

       betId:"h2h"

     })}
   )'
 >

   ${label}

   <b>
   ${Number(odd).toFixed(2)}
   </b>

 </div>
 `;

}


function selectBet(
 bet
){

 if(
   !bet.odd
   ||
   Number(bet.odd)<=1
 ){

   alert(
     "Odd is not available."
   );

   return;
 }


 // Same match + same market
 // replaces previous selection.

 slip=slip.filter(
   x=>!(
     String(x.id)
     ===
     String(bet.id)

     &&

     String(x.betId)
     ===
     String(bet.betId)
   )
 );


 slip.push(
   bet
 );

 saveSlip();

 openMatch(
   String(bet.id)
 );

}


function marketButton(
 m,
 market,
 selection,
 odd,
 betId
){

 const selected=
   isSelected(
     m.id,
     betId,
     selection
   );


 return `
 <button

   class="selection ${
     selected
       ? "selected"
       : ""
   }"

   onclick='selectBet(
     ${JSON.stringify({

       id:m.id,

       home:m.home,

       away:m.away,

       league:m.league,

       market:market,

       selection:selection,

       odd:Number(odd),

       betId:String(
         betId
       )

     })}
   )'
 >

   <span>
   ${esc(selection)}
   </span>

   <b>
   @ ${Number(odd).toFixed(2)}
   </b>

 </button>
 `;

}


function openMatch(
 id
){

 const c=
 document.getElementById(
   "content"
 );

 c.innerHTML=
 `
 <div class="status">
 ⏳ Loading markets...
 </div>
 `;


 api(
   "/api/match/"
   +
   encodeURIComponent(id)
 )

 .then(d=>{

   const m=d.match;

   const markets=
     d.markets || [];


   let html=
   `

   <button
     class="big back"
     onclick="loadMatches()"
   >
     ⬅️ Back to Matches
   </button>

   <h2>
   ⚽ ${esc(m.home)}
   vs
   ${esc(m.away)}
   </h2>

   <div class="meta">
   🏆 ${esc(m.league)}
   •
   🕐 ${esc(m.time)}
   </div>
   `;


   // =================================================
   // BEST BET
   // =================================================

   if(d.best_bet){

     html+=
     `
     <div class="market best-card">

       <div class="market-title">
       🎯 Best Bet
       </div>

       <div style="padding:12px">

       <div class="yellow">
       <b>
       ${esc(
         d.best_bet.selection
       )}
       </b>

       @

       ${Number(
         d.best_bet.odd
       ).toFixed(2)}

       </div>

       <div class="status">
       Odds-based suggestion.
       Not a guarantee.
       </div>

       </div>

     </div>
     `;

   }


   if(d.odds_error){

     html+=
     `
     <div class="info error">
     ⚠️ ${esc(
       d.odds_error
     )}
     </div>
     `;

   }


   if(!markets.length){

     html+=
     `
     <div class="market">

       <div class="market-title">
       ⚠️ Markets
       </div>

       <div
         style="padding:12px"
         class="empty"
       >
       No markets available.
       </div>

     </div>
     `;

   }


   markets.forEach(
     market=>{

       html+=
       `
       <div class="market">

         <div class="market-title">
         ${esc(
           market.name
         )}
         </div>

         <div class="market-grid">
       `;


       (
         market.selections
         || []
       ).forEach(
         s=>{

           html+=marketButton(

             m,

             market.name,

             s.value,

             s.odd,

             market.id

           );

         }
       );


       html+=
       `
         </div>
       </div>
       `;

     }
   );


   html+=
   `
   <button
     class="big"
     onclick="renderSlip()"
   >
     🎟️ Bet Slip
     (${slip.length})
   </button>
   `;


   c.innerHTML=html;

 })

 .catch(e=>{

   c.innerHTML=
   `
   <h2>⚠️ Error</h2>

   <div class="error">
   ${esc(e.message)}
   </div>

   <button
     class="big back"
     onclick="loadMatches()"
   >
   ⬅️ Back
   </button>
   `;

 });

}


function loadBest(){

 const c=
 document.getElementById(
   "content"
 );

 c.innerHTML=
 `
 <div class="status">
 ⏳ Finding best bets...
 </div>
 `;


 api(
   "/api/matches"
 )

 .then(d=>{

   const list=
     (d.matches || [])
     .filter(
       x=>x.best_bet
     );


   if(!list.length){

     c.innerHTML=
     `
     <h2>🎯 Best Bet</h2>

     <div class="empty">

     No Best Bet available
     in the current 7-day
     odds data.

     </div>
     `;

     return;
   }


   let html=
   `
   <h2>🎯 Best Bet</h2>

   <div class="status">
   Odds-based suggestion.
   Not a guarantee.
   </div>
   `;


   list.forEach(
     m=>{

       html+=
       `
       <div class="match best-card">

         <div class="teams">
         ${esc(m.home)}
         vs
         ${esc(m.away)}
         </div>

         <div class="meta">
         ${esc(m.league)}
         •
         ${esc(m.time)}
         </div>

         <div class="yellow">
         🎯

         <b>
         ${esc(
           m.best_bet.selection
         )}
         </b>

         @

         ${Number(
           m.best_bet.odd
         ).toFixed(2)}

         </div>

         <button
           class="big"
           onclick="openMatch(
             '${esc(m.id)}'
           )"
         >
         📊 Open Markets
         </button>

       </div>
       `;

     }
   );


   c.innerHTML=html;

 })

 .catch(e=>{

   c.innerHTML=
   `
   <h2>⚠️ Error</h2>

   <div class="error">
   ${esc(e.message)}
   </div>
   `;

 });

}


function loadLive(){

 const c=
 document.getElementById(
   "content"
 );

 c.innerHTML=
 `
 <div class="status">
 ⏳ Loading live matches...
 </div>
 `;


 api(
   "/api/live"
 )

 .then(d=>{

   const list=
     d.matches || [];


   if(!list.length){

     c.innerHTML=
     `
     <h2>🔴 Live</h2>

     <div class="empty">
     No live matches now.
     </div>
     `;

     return;
   }


   c.innerHTML=
   `
   <h2>🔴 Live</h2>

   ${
     list.map(
       x=>`

       <div class="match">

         <div class="teams">

         ${esc(x.home)}

         ${x.home_score ?? 0}

         -

         ${x.away_score ?? 0}

         ${esc(x.away)}

         </div>

         <div class="meta">
         🔴 LIVE
         </div>

       </div>

       `
     ).join("")
   }

   `;

 })

 .catch(e=>{

   c.innerHTML=
   `
   <h2>🔴 Live</h2>

   <div class="error">
   ${esc(e.message)}
   </div>
   `;

 });

}


function renderSlip(){

 const c=
 document.getElementById(
   "content"
 );


 if(!slip.length){

   c.innerHTML=
   `
   <h2>🎟️ Bet Slip</h2>

   <div class="empty">
   Your Bet Slip is empty.
   </div>
   `;

   return;
 }


 let total=1;

 let html=
 `
 <h2>🎟️ Bet Slip</h2>
 `;


 slip.forEach(
   (x,i)=>{

     const odd=
       Number(x.odd);

     if(
       Number.isFinite(odd)
       &&
       odd>1
     ){

       total*=odd;

     }


     html+=
     `
     <div class="slip-item">

       <b>
       ${i+1}.
       ${esc(x.home)}
       vs
       ${esc(x.away)}
       </b>

       <div class="meta">
       ${esc(
         x.league || ""
       )}
       </div>

       <div>
       📊 ${esc(x.market)}
       </div>

       <div class="yellow">
       🎯
       ${esc(x.selection)}
       @
       ${odd.toFixed(2)}
       </div>

       <button
         class="big back"
         onclick="removeBet(${i})"
       >
       🗑 Remove
       </button>

     </div>
     `;

   }
 );


 html+=
 `
 <div class="market">

   <div class="market-title">

   📈 Total Odds:
   ${total.toFixed(2)}

   </div>

 </div>

 <button
   class="big"
   onclick="clearSlip()"
 >
 🗑 Clear Bet Slip
 </button>
 `;


 c.innerHTML=html;

}


function removeBet(i){

 slip.splice(
   i,
   1
 );

 saveSlip();

 renderSlip();

}


function clearSlip(){

 slip=[];

 saveSlip();

 renderSlip();

}


// =========================================================
// START
// =========================================================

loadMatches();

</script>

</body>

</html>
"""


# =========================================================
# WEB ROUTES
# =========================================================

@web_app.route("/")
def index():

    return render_template_string(
        HTML
    )


@web_app.route("/health")
def health():

    return jsonify({

        "status":
            "online",

        "bot":
            "Best Bet",

        "api":
            "The Odds API",

        "api_key_configured":
            bool(
                ODDS_API_KEY
            ),

        "days":
            DAYS_AHEAD,

        "markets":
            MARKETS,

        "cache_seconds":
            CACHE_SECONDS,

        "web_app":
            WEB_APP_URL,
    })


# =========================================================
# MATCHES API
# =========================================================

@web_app.route(
    "/api/matches"
)
def api_matches():

    try:

        matches, errors = (
            get_matches()
        )

        message = (
            "Football odds loaded "
            "for today and the next "
            "7 days."
            if matches

            else

            "No football matches with "
            "available odds found in "
            "the next 7 days."
        )


        response = {

            "success":
                True,

            "count":
                len(matches),

            "matches":
                matches,

            "message":
                message,

        }


        # Give debugging information
        # without exposing the API key.

        if errors:

            response[
                "sport_errors"
            ] = errors[:10]


        return jsonify(
            response
        )


    except Exception as e:

        print(
            "[API MATCHES ERROR]",
            e
        )

        return jsonify({

            "success":
                False,

            "count":
                0,

            "matches":
                [],

            "error":
                str(e),

        }), 500


# =========================================================
# SINGLE MATCH
# =========================================================

@web_app.route(
    "/api/match/<match_id>"
)
def api_match(
    match_id
):

    try:

        matches, errors = (
            get_matches()
        )


        match = next(

            (
                x
                for x in matches

                if str(
                    x.get("id")
                )
                ==
                str(match_id)
            ),

            None,
        )


        if not match:

            return jsonify({

                "success":
                    False,

                "error":
                    "Match hin argamne.",

            }), 404


        # -------------------------------------------------
        # Request current odds for this competition.
        # -------------------------------------------------

        events = odds_request(

            f"/sports/"
            f"{match['sport_key']}/odds",

            {

                "regions":
                    REGIONS,

                "markets":
                    MARKETS,

                "oddsFormat":
                    "decimal",

                "dateFormat":
                    "iso",

            },
        )


        event = next(

            (
                x
                for x in events

                if str(
                    x.get("id")
                )
                ==
                str(match_id)
            ),

            None,
        )


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

                "odds_error":
                    "Current odds hin argamne.",

            })


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

                    "value":
                        "1",

                    "odd":
                        converted[
                            "h2h"
                        ]["home"],

                })


            if converted[
                "h2h"
            ].get("draw"):

                selections.append({

                    "value":
                        "X",

                    "odd":
                        converted[
                            "h2h"
                        ]["draw"],

                })


            if converted[
                "h2h"
            ].get("away"):

                selections.append({

                    "value":
                        "2",

                    "odd":
                        converted[
                            "h2h"
                        ]["away"],

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
        # OVER / UNDER
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

                    "id":
                        "totals",

                    "name":
                        "⚽ Over / Under",

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

                selections.append({

                    "value":
                        (
                            f"{item['name']} "
                            f"{item['point']}"
                        ),

                    "odd":
                        item["price"],

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

        })


    except Exception as e:

        print(
            "[MATCH ERROR]",
            e
        )

        return jsonify({

            "success":
                False,

            "error":
                str(e),

            "markets":
                [],

        }), 500


# =========================================================
# LIVE
# =========================================================

@web_app.route(
    "/api/live"
)
def api_live():

    try:

        result = []

        sports = soccer_sports()


        for sport in sports[
            :20
        ]:

            try:

                scores = odds_request(

                    f"/sports/"
                    f"{sport['key']}/scores",

                    {

                        "daysFrom":
                            1,

                        "dateFormat":
                            "iso",

                    },
                )


            except Exception as e:

                print(
                    "[LIVE SKIP]",
                    sport.get(
                        "key"
                    ),
                    e
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

            "matches":
                result,

        })


    except Exception as e:

        print(
            "[LIVE ERROR]",
            e
        )

        return jsonify({

            "success":
                False,

            "error":
                str(e),

            "matches":
                [],

        }), 500


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
            "WARNING: "
            "ODDS_API_KEY hin jiru."
        )


    threading.Thread(

        target=run_web,

        daemon=True,

    ).start()


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
            start
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
        "BEST BET BOT ONLINE"
    )

    print(
        "WEB APP:",
        WEB_APP_URL
    )

    print(
        "ODDS API:",
        bool(
            ODDS_API_KEY
        )
    )

    print(
        "DAYS:",
        DAYS_AHEAD
    )

    print(
        "MARKETS:",
        MARKETS
    )

    print(
        "CACHE:",
        CACHE_SECONDS,
        "seconds"
    )

    print(
        "===================================="
    )


    application.run_polling(

        allowed_updates=
            Update.ALL_TYPES

    )


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":

    main()
