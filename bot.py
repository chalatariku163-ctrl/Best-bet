import os
import threading
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
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://best-bet-7t7f.onrender.com",
).strip()

PORT = int(os.getenv("PORT", "10000"))

ODDS_BASE = "https://api.the-odds-api.com/v4"

REGIONS = os.getenv("ODDS_REGIONS", "eu").strip()

LIST_MARKETS = "h2h"
DETAIL_MARKETS = "h2h,totals,spreads"

DAYS_AHEAD = 7
MAX_SOCCER_SPORTS = 40

web_app = Flask(__name__)
USERS = {}


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
            f"*{i}.* {item['home']} vs {item['away']}\n"
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
# ODDS API
# =========================================================

def odds_request(path, params=None):
    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru. Render Environment Variables "
            "keessatti ODDS_API_KEY galchi."
        )

    request_params = dict(params or {})
    request_params["apiKey"] = ODDS_API_KEY

    response = requests.get(
        ODDS_BASE + path,
        params=request_params,
        timeout=35,
        headers={"Accept": "application/json"},
    )

    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = response.text[:1000]

        raise RuntimeError(
            f"Odds API error {response.status_code}: {body}"
        )

    return response.json()


# =========================================================
# SOCCER SPORTS
# =========================================================

def soccer_sports():
    sports = odds_request("/sports")

    result = []

    for sport in sports:
        key = str(sport.get("key", ""))

        if sport.get("active") and key.startswith("soccer_"):
            result.append(sport)

    return result


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(event, sport):
    h2h = {}
    totals = {}
    spreads = []

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")

            for outcome in market.get("outcomes", []):
                name = str(outcome.get("name", ""))
                price = outcome.get("price")

                if price is None:
                    continue

                try:
                    price = float(price)
                except Exception:
                    continue

                # 1X2
                if market_key == "h2h":
                    if name == event.get("home_team"):
                        h2h.setdefault("home", price)

                    elif name == event.get("away_team"):
                        h2h.setdefault("away", price)

                    elif name == "Draw":
                        h2h.setdefault("draw", price)

                # TOTALS
                elif market_key == "totals":
                    point = outcome.get("point")

                    try:
                        point = float(point)
                    except Exception:
                        continue

                    if point == 2.5:
                        if name == "Over":
                            totals.setdefault("over", price)

                        elif name == "Under":
                            totals.setdefault("under", price)

                # HANDICAP
                elif market_key == "spreads":
                    spreads.append({
                        "name": name,
                        "point": outcome.get("point"),
                        "price": price,
                    })

    unique_spreads = []
    seen = set()

    for item in spreads:
        key = (
            item["name"],
            str(item["point"]),
            item["price"],
        )

        if key not in seen:
            seen.add(key)
            unique_spreads.append(item)

    # =====================================================
    # BEST BET
    # =====================================================

    candidates = []

    if h2h.get("home"):
        candidates.append(("1", h2h["home"], "1X2"))

    if h2h.get("draw"):
        candidates.append(("X", h2h["draw"], "1X2"))

    if h2h.get("away"):
        candidates.append(("2", h2h["away"], "1X2"))

    if totals.get("over"):
        candidates.append(
            ("Over 2.5", totals["over"], "Over/Under")
        )

    if totals.get("under"):
        candidates.append(
            ("Under 2.5", totals["under"], "Over/Under")
        )

    candidates = [
        x for x in candidates
        if 1.01 < x[1] <= 20
    ]

    # Keep highest quality simple selection.
    # This is an odds-based suggestion, not guaranteed.
    candidates.sort(key=lambda x: x[1])

    best = None

    if candidates:
        selection, odd, market = candidates[0]

        best = {
            "selection": selection,
            "odd": odd,
            "market": market,
        }

    # =====================================================
    # ETHIOPIA TIME UTC+3
    # =====================================================

    commence = event.get("commence_time", "")
    time_text = ""

    if commence:
        try:
            dt = datetime.fromisoformat(
                commence.replace("Z", "+00:00")
            )

            local_dt = dt.astimezone(
                timezone(timedelta(hours=3))
            )

            time_text = local_dt.strftime(
                "%d/%m, %H:%M"
            )

        except Exception:
            time_text = ""

    return {
        "id": event.get("id"),
        "sport_key": sport.get("key"),
        "league": sport.get("title", "Football"),
        "home": event.get("home_team", "Home"),
        "away": event.get("away_team", "Away"),
        "time": time_text,
        "commence_time": commence,
        "h2h": h2h,
        "totals": totals,
        "spreads": unique_spreads,
        "best_bet": best,
    }


# =========================================================
# GET MATCHES
# TODAY + NEXT 7 DAYS
# =========================================================

def get_matches():
    result = []

    try:
        sports = soccer_sports()
    except Exception as e:
        print("[SPORTS ERROR]", e)
        return []

    print("====================================")
    print("[SOCCER SPORTS]", len(sports))
    print([x.get("key") for x in sports])
    print("====================================")

    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD)

    start_text = now.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    end_text = end_time.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    for sport in sports[:MAX_SOCCER_SPORTS]:

        sport_key = sport.get("key")

        if not sport_key:
            continue

        try:
            params = {
                "regions": REGIONS,
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

            print(
                "[ODDS]",
                sport_key,
                "events:",
                len(events),
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
                            "+00:00"
                        )
                    )

                    if dt < now or dt > end_time:
                        continue

                    converted = convert_event(
                        event,
                        sport,
                    )

                    if converted.get("h2h"):
                        result.append(converted)

                except Exception as e:
                    print(
                        "[EVENT ERROR]",
                        e,
                    )

        except Exception as e:
            print(
                "[SPORT ERROR]",
                sport_key,
                e,
            )

    # Remove duplicates
    unique = {}

    for match in result:
        match_id = str(
            match.get("id") or ""
        )

        if match_id:
            unique[match_id] = match

    result = list(unique.values())

    result.sort(
        key=lambda x:
        x.get("commence_time") or ""
    )

    print("====================================")
    print(
        "[TOTAL 7-DAY MATCHES]",
        len(result),
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
    context: ContextTypes.DEFAULT_TYPE
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
        "🎟️ Bet Slip\n"
        "👤 Profile\n\n"
        "👇 *FOOTBALL* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# =========================================================
# TELEGRAM CALLBACK
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
            f"💰 Balance: *{u['balance']:.2f}*\n"
            f"🎟️ Bet Slip: *{len(u['betslip'])}*",
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
            text = "📜 *HISTORY*\n\n"

            for item in u["history"][-10:]:
                text += (
                    f"🕐 {item['time']}\n"
                    f"💰 {item['stake']:.2f} | "
                    f"📈 {item['odds']:.2f} | "
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
            "guyyaa 7 agarsiisa.\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEB APP HTML
# BETIKA-LIKE BEST BET UI
# =========================================================

HTML = r"""
<!doctype html>

<html lang="en">

<head>

<meta charset="utf-8">

<meta
 name="viewport"
 content="width=device-width,
 initial-scale=1,
 maximum-scale=1,
 user-scalable=no"
>

<title>BEST BET</title>

<style>

*{
 box-sizing:border-box;
}

html,
body{
 margin:0;
 padding:0;
 background:#101c29;
 color:#fff;
 font-family:Arial,Helvetica,sans-serif;
}

/* =====================================================
   TOP HEADER
===================================================== */

.topbar{
 height:64px;
 background:#172737;
 display:flex;
 align-items:center;
 padding:0 14px;
 gap:14px;
 border-bottom:1px solid #24384b;
}

.menu{
 font-size:30px;
 color:#cbd5df;
 width:35px;
}

.logo{
 font-size:25px;
 font-weight:1000;
 color:#ffd400;
 letter-spacing:-1px;
}

.logo span{
 color:#fff;
}

.login{
 margin-left:auto;
 color:#8cc63f;
 font-size:16px;
 font-weight:800;
}

.register{
 background:#8cc63f;
 color:#111;
 padding:11px 15px;
 border-radius:10px;
 font-weight:900;
}

/* =====================================================
   SPORTS BAR
===================================================== */

.sportsbar{
 height:104px;
 background:#132230;
 display:flex;
 overflow-x:auto;
 border-bottom:1px solid #23394b;
}

.sport{
 min-width:90px;
 text-align:center;
 padding:17px 8px 10px;
 color:#7d8995;
 font-size:13px;
 font-weight:800;
}

.sport.active{
 background:#1b3041;
 color:#fff;
}

.sport-icon{
 font-size:31px;
 display:block;
 margin-bottom:5px;
}

/* =====================================================
   MAIN
===================================================== */

main{
 padding-bottom:90px;
 max-width:1000px;
 margin:auto;
}

/* =====================================================
   FILTER BAR
===================================================== */

.filters{
 display:flex;
 gap:8px;
 padding:13px 12px 8px;
 overflow-x:auto;
}

.filter{
 white-space:nowrap;
 background:#263948;
 border:0;
 color:#fff;
 padding:11px 15px;
 border-radius:18px;
 font-weight:800;
 font-size:13px;
}

.filter.active{
 background:#ffd400;
 color:#111;
}

/* =====================================================
   BANNER
===================================================== */

.banner{
 margin:6px 12px 12px;
 border-radius:4px;
 min-height:100px;
 background:
 linear-gradient(
  120deg,
  #b5db00,
  #dff21d,
  #9ed000
 );
 color:#173000;
 display:flex;
 align-items:center;
 justify-content:space-between;
 padding:15px;
 overflow:hidden;
}

.banner-title{
 font-size:21px;
 font-weight:1000;
 line-height:1.05;
}

.banner-small{
 font-size:11px;
 margin-top:7px;
 font-weight:700;
}

.banner-ball{
 font-size:62px;
}

/* =====================================================
   SECTION TITLE
===================================================== */

.section-title{
 padding:8px 15px;
 font-size:21px;
 font-weight:1000;
}

/* =====================================================
   DAYS
===================================================== */

.days{
 display:flex;
 gap:7px;
 overflow-x:auto;
 padding:5px 12px 12px;
}

.day{
 min-width:82px;
 background:#263948;
 color:#fff;
 border:0;
 border-radius:12px;
 padding:10px 9px;
 font-weight:900;
 font-size:12px;
}

.day.active{
 background:#ffd400;
 color:#111;
}

.day strong{
 display:block;
 font-size:14px;
 margin-bottom:3px;
}

/* =====================================================
   MATCH CARD
===================================================== */

.match{
 margin:8px 12px;
 background:#172939;
 border-radius:8px;
 overflow:hidden;
 border:1px solid #21394c;
}

.match-head{
 padding:10px 12px 6px;
 display:flex;
 justify-content:space-between;
 align-items:center;
 color:#9aaab8;
 font-size:11px;
}

.league{
 white-space:nowrap;
 overflow:hidden;
 text-overflow:ellipsis;
 max-width:65%;
}

.match-time{
 font-weight:900;
 color:#c2ced8;
}

.teams{
 padding:3px 12px 9px;
 font-size:16px;
 font-weight:900;
 line-height:1.55;
}

.team-row{
 display:flex;
 align-items:center;
 justify-content:space-between;
}

.team-name{
 display:flex;
 align-items:center;
 gap:8px;
}

.ball{
 font-size:18px;
}

.odds-title{
 display:grid;
 grid-template-columns:1fr 1fr 1fr;
 color:#8f9eaa;
 font-size:11px;
 font-weight:900;
 text-align:center;
 padding:0 10px 5px;
}

.odds{
 display:grid;
 grid-template-columns:repeat(3,1fr);
 gap:5px;
 padding:0 10px 9px;
}

.odd{
 border:0;
 background:#24394b;
 color:#fff;
 border-radius:20px;
 padding:10px 4px;
 text-align:center;
 font-weight:900;
 cursor:pointer;
}

.odd strong{
 font-size:16px;
 display:block;
}

.odd small{
 font-size:10px;
 color:#aebbc6;
}

.odd.selected{
 background:#8cc63f;
 color:#111;
}

.odd.selected small{
 color:#111;
}

.markets{
 padding:0 10px 11px;
}

.market-btn{
 width:100%;
 background:transparent;
 color:#8cc63f;
 border:0;
 text-align:right;
 font-weight:900;
 padding:3px 2px;
 cursor:pointer;
}

/* =====================================================
   BEST BET
===================================================== */

.best-card{
 border:1px solid #ffd400;
}

.best-label{
 background:#ffd400;
 color:#111;
 font-weight:1000;
 padding:7px 11px;
 font-size:12px;
}

.best-content{
 padding:11px;
}

.best-selection{
 display:flex;
 justify-content:space-between;
 align-items:center;
 background:#20384b;
 padding:11px;
 border-radius:9px;
}

.best-name{
 font-weight:1000;
}

.best-odd{
 color:#ffd400;
 font-size:20px;
 font-weight:1000;
}

/* =====================================================
   MARKET PAGE
===================================================== */

.back{
 margin:12px;
 border:0;
 background:#263948;
 color:#fff;
 padding:11px 16px;
 border-radius:10px;
 font-weight:900;
}

.match-detail{
 padding:14px;
 background:#172939;
 margin:12px;
 border-radius:10px;
}

.detail-teams{
 text-align:center;
 font-size:21px;
 font-weight:1000;
 line-height:1.5;
}

.detail-time{
 text-align:center;
 color:#9eacb7;
 font-size:12px;
 margin-top:5px;
}

.market-box{
 margin:10px 12px;
 background:#172939;
 border-radius:9px;
 overflow:hidden;
}

.market-header{
 background:#20384b;
 padding:12px;
 font-weight:1000;
}

.market-grid{
 display:grid;
 grid-template-columns:repeat(2,1fr);
 gap:7px;
 padding:8px;
}

.selection{
 border:0;
 background:#263b4e;
 color:#fff;
 padding:11px;
 border-radius:8px;
 text-align:left;
 font-weight:900;
}

.selection small{
 display:block;
 color:#9fadb8;
 margin-bottom:4px;
}

.selection strong{
 font-size:16px;
}

.selection.selected{
 background:#8cc63f;
 color:#111;
}

.selection.selected small{
 color:#111;
}

/* =====================================================
   BET SLIP
===================================================== */

.slip{
 padding:12px;
}

.slip-card{
 background:#172939;
 border-radius:9px;
 padding:12px;
 margin-bottom:8px;
 border:1px solid #243c4f;
}

.slip-team{
 font-weight:1000;
}

.slip-market{
 color:#9baab6;
 font-size:12px;
 margin-top:5px;
}

.slip-selection{
 color:#ffd400;
 font-weight:1000;
 margin-top:7px;
}

.remove{
 margin-top:9px;
 background:#263948;
 color:#ff7777;
 border:0;
 border-radius:7px;
 padding:8px 11px;
 font-weight:900;
}

.total-box{
 background:#172939;
 margin-top:10px;
 border-radius:9px;
 padding:15px;
 display:flex;
 justify-content:space-between;
}

.total-odd{
 color:#ffd400;
 font-size:20px;
 font-weight:1000;
}

/* =====================================================
   PROFILE
===================================================== */

.profile{
 padding:12px;
}

.profile-head{
 background:#172939;
 border-radius:10px;
 padding:20px;
 text-align:center;
}

.avatar{
 width:75px;
 height:75px;
 border-radius:50%;
 background:#263948;
 display:flex;
 justify-content:center;
 align-items:center;
 margin:auto;
 font-size:38px;
}

.profile-name{
 font-size:21px;
 font-weight:1000;
 margin-top:10px;
}

.profile-card{
 margin-top:10px;
 background:#172939;
 border-radius:9px;
 overflow:hidden;
}

.profile-row{
 display:flex;
 justify-content:space-between;
 padding:15px;
 border-bottom:1px solid #263b4c;
}

.profile-row:last-child{
 border-bottom:0;
}

.profile-value{
 color:#ffd400;
 font-weight:1000;
}

/* =====================================================
   EMPTY / ERROR
===================================================== */

.empty{
 text-align:center;
 padding:45px 20px;
 color:#8f9eaa;
}

.error{
 padding:20px;
 color:#ff7979;
 white-space:pre-wrap;
}

.loading{
 padding:35px;
 text-align:center;
 color:#a7b4bf;
}

/* =====================================================
   BOTTOM NAV
===================================================== */

.bottom{
 position:fixed;
 bottom:0;
 left:0;
 right:0;
 height:72px;
 background:#152535;
 border-top:1px solid #2b4051;
 display:grid;
 grid-template-columns:repeat(5,1fr);
 z-index:100;
}

.bottom button{
 border:0;
 background:transparent;
 color:#9aa7b2;
 font-size:10px;
 font-weight:900;
}

.bottom button.active{
 color:#8cc63f;
}

.bottom-icon{
 display:block;
 font-size:23px;
 margin-bottom:3px;
}

/* =====================================================
   RESPONSIVE
===================================================== */

@media(min-width:700px){

 .match{
  margin-left:18px;
  margin-right:18px;
 }

 .banner{
  margin-left:18px;
  margin-right:18px;
 }

 .filters,
 .days{
  padding-left:18px;
  padding-right:18px;
 }

}

</style>

</head>

<body>

<!-- ====================================================
     TOP HEADER
===================================================== -->

<div class="topbar">

 <div class="menu">☰</div>

 <div class="logo">
  BEST<span>BET</span>
 </div>

 <div class="login">
  Login
 </div>

 <div class="register">
  Register
 </div>

</div>


<!-- ====================================================
     SPORTS BAR
===================================================== -->

<div class="sportsbar">

 <div class="sport active">
  <span class="sport-icon">⚽</span>
  Soccer
 </div>

 <div class="sport">
  <span class="sport-icon">🏆</span>
  Best Bet
 </div>

 <div class="sport">
  <span class="sport-icon">🔴</span>
  Live
 </div>

 <div class="sport">
  <span class="sport-icon">🌍</span>
  Countries
 </div>

 <div class="sport">
  <span class="sport-icon">⭐</span>
  Popular
 </div>

</div>


<main>

<!-- ====================================================
     FILTERS
===================================================== -->

<div class="filters">

 <button class="filter">
  ⚙ Filters
 </button>

 <button
  class="filter active"
  onclick="loadMatches()"
 >
  Today
 </button>

 <button class="filter">
  Highlights
 </button>

 <button class="filter">
  1X2
 </button>

</div>


<!-- ====================================================
     BANNER
===================================================== -->

<div class="banner">

 <div>

  <div class="banner-title">
   BEST BET
   <br>
   PICK SMART
  </div>

  <div class="banner-small">
   Football odds • Multiple markets
  </div>

 </div>

 <div class="banner-ball">
  ⚽
 </div>

</div>


<!-- ====================================================
     CONTENT
===================================================== -->

<div id="content">

 <div class="loading">
  Loading football...
 </div>

</div>

</main>


<!-- ====================================================
     BOTTOM NAV
===================================================== -->

<div class="bottom">

 <button
  id="nav-home"
  class="active"
  onclick="setNav('home')"
 >
  <span class="bottom-icon">🏠</span>
  Home
 </button>

 <button
  id="nav-live"
  onclick="setNav('live')"
 >
  <span class="bottom-icon">🔴</span>
  Live
 </button>

 <button
  id="nav-best"
  onclick="setNav('best')"
 >
  <span class="bottom-icon">🎯</span>
  Best Bet
 </button>

 <button
  id="nav-slip"
  onclick="setNav('slip')"
 >
  <span class="bottom-icon">🎟️</span>
  Bets
 </button>

 <button
  id="nav-profile"
  onclick="setNav('profile')"
 >
  <span class="bottom-icon">👤</span>
  Profile
 </button>

</div>


<script>

let matches = [];

let slip = JSON.parse(
 localStorage.getItem(
  "bestbet_slip"
 ) || "[]"
);


/* =====================================================
   HELPERS
===================================================== */

function esc(x){

 return String(
  x ?? ""
 ).replace(
  /[&<>"']/g,
  function(m){

   return {
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#039;"
   }[m];

  }
 );

}


function saveSlip(){

 localStorage.setItem(
  "bestbet_slip",
  JSON.stringify(slip)
 );

}


function dayKey(date){

 const d = new Date(date);

 return d.toISOString().slice(
  0,
  10
 );

}


function dayLabel(date){

 const d = new Date(date);

 const today = new Date();

 const tomorrow = new Date();

 tomorrow.setDate(
  today.getDate() + 1
 );

 const key =
  d.toISOString().slice(
   0,
   10
  );

 const todayKey =
  today.toISOString().slice(
   0,
   10
  );

 const tomorrowKey =
  tomorrow.toISOString().slice(
   0,
   10
  );

 if(key === todayKey){
  return "TODAY";
 }

 if(key === tomorrowKey){
  return "TOMORROW";
 }

 return d.toLocaleDateString(
  undefined,
  {
   weekday:"short",
   day:"numeric",
   month:"short"
  }
 );

}


/* =====================================================
   API
===================================================== */

async function api(path){

 const response =
  await fetch(path);

 const data =
  await response.json();

 if(!response.ok){

  throw new Error(
   data.error ||
   "Server error"
  );

 }

 return data;

}


/* =====================================================
   NAVIGATION
===================================================== */

function setNav(name){

 document
  .querySelectorAll(".bottom button")
  .forEach(
   x=>x.classList.remove(
    "active"
   )
  );

 const btn =
  document.getElementById(
   "nav-" + name
  );

 if(btn){
  btn.classList.add(
   "active"
  );
 }

 if(name === "home"){
  loadMatches();
 }

 if(name === "live"){
  loadLive();
 }

 if(name === "best"){
  loadBest();
 }

 if(name === "slip"){
  renderSlip();
 }

 if(name === "profile"){
  renderProfile();
 }

}


/* =====================================================
   LOAD MATCHES
===================================================== */

function loadMatches(){

 const c =
  document.getElementById(
   "content"
  );

 c.innerHTML = `
  <div class="loading">
   ⏳ Loading football matches...
  </div>
 `;

 api("/api/matches")
 .then(function(data){

  matches =
   data.matches || [];

  if(!matches.length){

   c.innerHTML = `
    <div class="section-title">
     ⚽ Soccer
    </div>

    <div class="empty">

     No football matches
     with available odds
     found in the next 7 days.

     <br><br>

     <small>
      ${esc(
       data.message || ""
      )}
     </small>

    </div>
   `;

   return;

  }

  renderDays();

 })
 .catch(function(error){

  c.innerHTML = `
   <div class="error">
    ⚠️ ${esc(error.message)}
   </div>
  `;

 });

}


/* =====================================================
   DAY GROUP
===================================================== */

function renderDays(){

 const groups = {};

 matches.forEach(
  function(m){

   const key =
    dayKey(
     m.commence_time
    );

   if(!groups[key]){
    groups[key] = [];
   }

   groups[key].push(m);

  }
 );

 const keys =
  Object.keys(groups)
  .sort();

 let html = `

  <div class="section-title">
   ⚽ Soccer
  </div>

  <div class="days">
 `;

 keys.forEach(
  function(key,index){

   html += `
    <button
     class="day ${
      index === 0
      ? "active"
      : ""
     }"
     onclick="
      showDay(
       '${key}',
       this
      )
     "
    >

     <strong>
      ${esc(
       dayLabel(
        key +
        "T12:00:00"
       )
      )}
     </strong>

     ${groups[key].length}
     matches

    </button>
   `;

  }
 );

 html += `
  </div>

  <div id="dayMatches"></div>
 `;

 document
  .getElementById(
   "content"
  )
  .innerHTML = html;

 showDay(
  keys[0],
  document.querySelector(
   ".day"
  )
 );

}


/* =====================================================
   SHOW DAY
===================================================== */

function showDay(
 key,
 button
){

 document
  .querySelectorAll(
   ".day"
  )
  .forEach(
   x =>
    x.classList.remove(
     "active"
    )
  );

 if(button){
  button.classList.add(
   "active"
  );
 }

 const list =
  matches.filter(
   m =>
    dayKey(
     m.commence_time
    ) === key
  );

 const box =
  document.getElementById(
   "dayMatches"
  );

 if(!box){
  return;
 }

 let html = "";

 if(!list.length){

  html = `
   <div class="empty">
    No matches.
   </div>
  `;

 }else{

  html =
   list.map(
    renderMatch
   ).join("");

 }

 box.innerHTML =
  html;

}


/* =====================================================
   MATCH CARD
===================================================== */

function renderMatch(m){

 const h =
  m.h2h || {};

 return `

 <div class="match">

  <div class="match-head">

   <div class="league">
    ⚽ ${esc(
     m.league
    )}
   </div>

   <div class="match-time">
    ${esc(
     m.time
    )}
   </div>

  </div>


  <div class="teams">

   <div class="team-row">

    <div class="team-name">

     <span class="ball">
      ⚽
     </span>

     ${esc(
      m.home
     )}

    </div>

   </div>


   <div class="team-row">

    <div class="team-name">

     <span class="ball">
      ⚽
     </span>

     ${esc(
      m.away
     )}

    </div>

   </div>

  </div>


  <div class="odds-title">

   <div>1</div>
   <div>X</div>
   <div>2</div>

  </div>


  <div class="odds">

   ${oddButton(
    m,
    "1",
    h.home,
    "home"
   )}

   ${oddButton(
    m,
    "X",
    h.draw,
    "draw"
   )}

   ${oddButton(
    m,
    "2",
    h.away,
    "away"
   )}

  </div>


  <div class="markets">

   <button
    class="market-btn"
    onclick="
     openMatch(
      '${esc(m.id)}'
     )
    "
   >
    + Markets
   </button>

  </div>

 </div>

 `;

}


/* =====================================================
   ODD BUTTON
===================================================== */

function isSelected(
 id,
 betId,
 selection
){

 return slip.some(
  x =>
   String(x.id) ===
   String(id)

   &&

   String(x.betId) ===
   String(betId)

   &&

   x.selection ===
   selection
 );

}


function oddButton(
 m,
 label,
 odd
){

 if(!odd){

  return `
   <button
    class="odd"
    disabled
   >

    <small>
     ${label}
    </small>

    <strong>
     -
    </strong>

   </button>
  `;

 }

 const selected =
  isSelected(
   m.id,
   "h2h",
   label
  );

 const bet = {
  id:m.id,
  home:m.home,
  away:m.away,
  league:m.league,
  market:"1X2",
  selection:label,
  odd:Number(odd),
  betId:"h2h"
 };

 return `

  <button
   class="odd ${
    selected
    ? "selected"
    : ""
   }"
   onclick='selectBet(
    ${JSON.stringify(
     bet
    )}
   )'
  >

   <small>
    ${label}
   </small>

   <strong>
    ${Number(
     odd
    ).toFixed(2)}
   </strong>

  </button>

 `;

}


/* =====================================================
   SELECT BET
===================================================== */

function selectBet(
 bet
){

 if(
  !bet.odd ||
  Number(bet.odd) <= 1
 ){

  alert(
   "Odd is not available."
  );

  return;

 }

 // One selection per market
 slip =
  slip.filter(
   x =>
    !(
     String(x.id) ===
     String(bet.id)

     &&

     String(x.betId) ===
     String(bet.betId)
    )
  );

 slip.push(bet);

 saveSlip();

 renderSlip();

}


/* =====================================================
   OPEN MATCH
===================================================== */

function openMatch(id){

 const c =
  document.getElementById(
   "content"
  );

 c.innerHTML = `
  <div class="loading">
   ⏳ Loading all markets...
  </div>
 `;

 api(
  "/api/match/" +
  encodeURIComponent(id)
 )
 .then(
  function(data){

   const m =
    data.match;

   const markets =
    data.markets || [];

   let html = `

    <button
     class="back"
     onclick="loadMatches()"
    >
     ← Back
    </button>


    <div class="match-detail">

     <div class="detail-teams">
      ⚽ ${esc(
       m.home
      )}
      <br>
      ${esc(
       m.away
      )}
     </div>

     <div class="detail-time">
      🏆 ${esc(
       m.league
      )}
      <br>
      🕐 ${esc(
       m.time
      )}
     </div>

    </div>
   `;


   /* BEST BET */

   if(data.best_bet){

    html += `

     <div class="market-box">

      <div class="best-label">
       🎯 BEST BET
      </div>

      <div
       class="best-content"
      >

       <div class="best-selection">

        <div class="best-name">
         ${esc(
          data.best_bet.selection
         )}
        </div>

        <div class="best-odd">
         @
         ${Number(
          data.best_bet.odd
         ).toFixed(2)}
        </div>

       </div>

      </div>

     </div>
    `;

   }


   /* MARKETS */

   markets.forEach(
    function(market){

     html += `

      <div class="market-box">

       <div class="market-header">
        ${esc(
         market.name
        )}
       </div>

       <div class="market-grid">
     `;


     (
      market.selections ||
      []
     ).forEach(
      function(s){

       const selected =
        isSelected(
         m.id,
         market.id,
         s.value
        );

       const bet = {
        id:m.id,
        home:m.home,
        away:m.away,
        league:m.league,
        market:market.name,
        selection:s.value,
        odd:Number(s.odd),
        betId:String(
         market.id
        )
       };

       html += `

        <button
         class="selection ${
          selected
          ? "selected"
          : ""
         }"
         onclick='selectBet(
          ${JSON.stringify(
           bet
          )}
         )'
        >

         <small>
          ${esc(
           s.value
          )}
         </small>

         <strong>
          @
          ${Number(
           s.odd
          ).toFixed(2)}
         </strong>

        </button>

       `;

      }
     );


     html += `
       </div>
      </div>
     `;

    }
   );


   if(
    !markets.length
   ){

    html += `

     <div class="empty">
      No additional markets
      available right now.

      <br><br>

      ${esc(
       data.odds_error ||
       ""
      )}
     </div>
    `;

   }


   html += `

    <button
     class="back"
     style="
      width:calc(100% - 24px);
      margin-top:4px;
     "
     onclick="renderSlip()"
    >
     🎟️ Bet Slip
     (${slip.length})
    </button>

   `;


   c.innerHTML =
    html;

  }
 )
 .catch(
  function(error){

   c.innerHTML = `
    <div class="error">
     ⚠️ ${esc(
      error.message
     )}
    </div>
   `;

  }
 );

}


/* =====================================================
   BEST BET PAGE
===================================================== */

function loadBest(){

 const c =
  document.getElementById(
   "content"
  );

 c.innerHTML = `
  <div class="loading">
   ⏳ Finding Best Bets...
  </div>
 `;

 api("/api/matches")
 .then(
  function(data){

   const list =
    (data.matches || [])
    .filter(
     x =>
      x.best_bet
    );

   let html = `

    <div class="section-title">
     🎯 Best Bet
    </div>

    <div class="filters">

     <button
      class="filter active"
     >
      Recommended
     </button>

     <button
      class="filter"
     >
      Today
     </button>

     <button
      class="filter"
     >
      7 Days
     </button>

    </div>
   `;


   if(!list.length){

    html += `

     <div class="empty">

      🎯

      <br><br>

      No Best Bet available
      in the current
      7-day odds data.

      <br><br>

      <small>
       Make sure
       ODDS_API_KEY is configured.
      </small>

     </div>
    `;

    c.innerHTML =
     html;

    return;

   }


   list.forEach(
    function(m){

     html += `

      <div
       class="match best-card"
      >

       <div class="best-label">
        🎯 BEST BET
       </div>

       <div class="match-head">

        <div class="league">
         ⚽ ${esc(
          m.league
         )}
        </div>

        <div class="match-time">
         ${esc(
          m.time
         )}
        </div>

       </div>

       <div class="teams">

        ⚽ ${esc(
         m.home
        )}

        <br>

        ⚽ ${esc(
         m.away
        )}

       </div>

       <div class="best-content">

        <div
         class="best-selection"
        >

         <div>

          <div
           class="best-name"
          >
           ${esc(
            m.best_bet.selection
           )}
          </div>

          <small
           style="
            color:#9daab5
           "
          >
           ${esc(
            m.best_bet.market
           )}
          </small>

         </div>

         <div
          class="best-odd"
         >
          ${Number(
           m.best_bet.odd
          ).toFixed(2)}
         </div>

        </div>


        <button
         class="back"
         style="
          width:100%;
          margin:9px 0 0;
          background:#ffd400;
          color:#111;
         "
         onclick="
          openMatch(
           '${esc(m.id)}'
          )
         "
        >
         📊 View All Markets
        </button>

       </div>

      </div>

     `;

    }
   );


   c.innerHTML =
    html;

  }
 )
 .catch(
  function(error){

   c.innerHTML = `
    <div class="error">
     ${esc(
      error.message
     )}
    </div>
   `;

  }
 );

}


/* =====================================================
   LIVE
===================================================== */

function loadLive(){

 const c =
  document.getElementById(
   "content"
  );

 c.innerHTML = `
  <div class="loading">
   ⏳ Loading live matches...
  </div>
 `;

 api("/api/live")
 .then(
  function(data){

   const list =
    data.matches || [];

   let html = `

    <div class="section-title">
     🔴 Live
    </div>
   `;


   if(!list.length){

    html += `

     <div class="empty">

      🔴

      <br><br>

      No live matches now.

     </div>

    `;

   }else{

    list.forEach(
     function(x){

      html += `

       <div class="match">

        <div class="match-head">

         <div>
          🔴 LIVE
         </div>

         <div>
          ${esc(
           x.league
          )}
         </div>

        </div>

        <div class="teams">

         ⚽ ${esc(
          x.home
         )}

         <strong>
          ${x.home_score ?? 0}
         </strong>

         <br>

         ⚽ ${esc(
          x.away
         )}

         <strong>
          ${x.away_score ?? 0}
         </strong>

        </div>

       </div>

      `;

     }
    );

   }


   c.innerHTML =
    html;

  }
 )
 .catch(
  function(error){

   c.innerHTML = `
    <div class="error">
     ${esc(
      error.message
     )}
    </div>
   `;

  }
 );

}


/* =====================================================
   BET SLIP
===================================================== */

function renderSlip(){

 const c =
  document.getElementById(
   "content"
  );

 let html = `

  <div class="section-title">
   🎟️ Bet Slip
  </div>

 `;


 if(!slip.length){

  html += `

   <div class="empty">

    🎟️

    <br><br>

    Your Bet Slip is empty.

    <br><br>

    Select odds from
    football matches.

   </div>

  `;

  c.innerHTML =
   html;

  return;

 }


 let total = 1;


 slip.forEach(
  function(x,i){

   total *=
    Number(x.odd);


   html += `

    <div class="slip">

     <div class="slip-card">

      <div class="slip-team">
       ${i+1}.
       ${esc(
        x.home
       )}
       vs
       ${esc(
        x.away
       )}
      </div>

      <div class="slip-market">
       ${esc(
        x.market
       )}
      </div>

      <div class="slip-selection">
       🎯
       ${esc(
        x.selection
       )}
       @
       ${Number(
        x.odd
       ).toFixed(2)}
      </div>

      <button
       class="remove"
       onclick="
        removeBet(${i})
       "
      >
       🗑 Remove
      </button>

     </div>

    </div>

   `;

  }
 );


 html += `

  <div class="slip">

   <div class="total-box">

    <div>
     Total Odds
    </div>

    <div class="total-odd">
     ${total.toFixed(2)}
    </div>

   </div>

   <button
    class="back"
    style="
     width:100%;
     margin:10px 0;
     background:#ffd400;
     color:#111;
    "
    onclick="clearSlip()"
   >
    🗑 Clear Bet Slip
   </button>

  </div>

 `;


 c.innerHTML =
  html;

}


/* =====================================================
   REMOVE BET
===================================================== */

function removeBet(i){

 slip.splice(
  i,
  1
 );

 saveSlip();

 renderSlip();

}


function clearSlip(){

 slip = [];

 saveSlip();

 renderSlip();

}


/* =====================================================
   PROFILE
===================================================== */

function renderProfile(){

 const c =
  document.getElementById(
   "content"
  );

 const user =
  window.Telegram &&
  window.Telegram.WebApp &&
  window.Telegram.WebApp.initDataUnsafe &&
  window.Telegram.WebApp.initDataUnsafe.user;

 const name =
  user &&
  user.first_name
  ? user.first_name
  : "Best Bet User";


 c.innerHTML = `

  <div class="section-title">
   👤 Profile
  </div>


  <div class="profile">

   <div class="profile-head">

    <div class="avatar">
     👤
    </div>

    <div class="profile-name">
     ${esc(
      name
     )}
    </div>

    <div
     style="
      color:#8d9ba7;
      font-size:12px;
      margin-top:4px;
     "
    >
     BEST BET MEMBER
    </div>

   </div>


   <div class="profile-card">

    <div class="profile-row">

     <span>
      💰 Balance
     </span>

     <span class="profile-value">
      0.00
     </span>

    </div>


    <div class="profile-row">

     <span>
      🎟️ Bet Slip
     </span>

     <span class="profile-value">
      ${slip.length}
     </span>

    </div>


    <div class="profile-row">

     <span>
      🎯 Best Bets
     </span>

     <span class="profile-value">
      Available
     </span>

    </div>


    <div class="profile-row">

     <span>
      📅 Football
     </span>

     <span class="profile-value">
      7 Days
     </span>

    </div>

   </div>


   <div class="profile-card">

    <div
     class="profile-row"
     onclick="renderSlip()"
    >
     <span>
      🎟️ My Bets
     </span>

     <span>
      →
     </span>
    </div>


    <div
     class="profile-row"
     onclick="loadBest()"
    >
     <span>
      🎯 Best Bet
     </span>

     <span>
      →
     </span>
    </div>


    <div class="profile-row">

     <span>
      ⚙️ Settings
     </span>

     <span>
      →
     </span>

    </div>


    <div class="profile-row">

     <span>
      ℹ️ How To Play
     </span>

     <span>
      →
     </span>

    </div>

   </div>

  </div>

 `;

}


/* =====================================================
   START
===================================================== */

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
    return render_template_string(HTML)


@web_app.route("/health")
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
        "days":
            DAYS_AHEAD,
        "web_app":
            WEB_APP_URL,
    })


# =========================================================
# MATCHES API
# =========================================================

@web_app.route("/api/matches")
def api_matches():

    try:

        matches =
            get_matches()

        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football odds loaded for "
                "today and the next 7 days."
                if matches
                else
                "No football matches with available "
                "odds found in the next 7 days. "
                "Check ODDS_API_KEY, ODDS_REGIONS "
                "and The Odds API soccer coverage."
            ),
        })

    except Exception as e:

        print(
            "[API MATCHES ERROR]",
            e
        )

        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(e),
        }), 500


# =========================================================
# SINGLE MATCH + ALL MARKETS
# =========================================================

@web_app.route("/api/match/<match_id>")
def api_match(match_id):

    try:

        matches =
            get_matches()

        match = next(
            (
                x
                for x in matches
                if str(
                    x.get("id")
                ) ==
                str(match_id)
            ),
            None,
        )

        if not match:

            return jsonify({
                "error":
                    "Match hin argamne."
            }), 404


        events = odds_request(
            f"/sports/{match['sport_key']}/odds",
            {
                "regions":
                    REGIONS,
                "markets":
                    DETAIL_MARKETS,
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
                ) ==
                str(match_id)
            ),
            None,
        )


        if not event:

            return jsonify({
                "match": match,
                "markets": [],
                "best_bet":
                    match.get(
                        "best_bet"
                    ),
                "odds_error":
                    "Current odds hin argamne.",
            })


        converted =
            convert_event(
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
                        f"{item['name']} "
                        f"{item['point']}",
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

@web_app.route("/api/live")
def api_live():

    try:

        result = []

        sports =
            soccer_sports()


        for sport in sports[:20]:

            try:

                scores =
                    odds_request(
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
                    e,
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
                    ) or []
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

        return jsonify({
            "success":
                False,
            "error":
                str(e),
            "matches":
                [],
        })


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
            "WARNING: ODDS_API_KEY hin jiru."
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
            start,
        )
    )


    application.add_handler(
        CallbackQueryHandler(
            button_handler,
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
        bool(ODDS_API_KEY)
    )

    print(
        "LIST MARKETS:",
        LIST_MARKETS
    )

    print(
        "DETAIL MARKETS:",
        DETAIL_MARKETS
    )

    print(
        "DAYS:",
        DAYS_AHEAD
    )

    print(
        "===================================="
    )


    application.run_polling(
        allowed_updates=
            Update.ALL_TYPES
    )


if __name__ == "__main__":

    main()
