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

# IMPORTANT:
# h2h is used for the main 7-day match list because it normally
# has broader soccer coverage. Extra markets are requested only
# after a user opens one match.
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


def add_demo_bet(
    user_id,
    match,
    market,
    selection,
    odd,
    bet_id=None,
):
    try:
        odd = float(odd)
    except Exception:
        return False

    if odd <= 1:
        return False

    user = get_user(user_id)

    user["betslip"] = [
        x
        for x in user["betslip"]
        if not (
            str(x.get("fixture_id")) == str(match.get("id"))
            and str(x.get("bet_id")) == str(bet_id)
        )
    ]

    user["betslip"].append({
        "fixture_id": match.get("id"),
        "home": match.get("home", ""),
        "away": match.get("away", ""),
        "league": match.get("league", ""),
        "market": market,
        "selection": selection,
        "odd": odd,
        "bet_id": bet_id,
    })

    return True


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
            "ODDS_API_KEY hin jiru. Render Environment Variables keessatti "
            "ODDS_API_KEY galchi."
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

        if (
            sport.get("active")
            and key.startswith("soccer_")
        ):
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

                # -------------------------------------------------
                # H2H / 1X2
                # -------------------------------------------------
                if market_key == "h2h":
                    if name == event.get("home_team"):
                        h2h.setdefault("home", price)

                    elif name == event.get("away_team"):
                        h2h.setdefault("away", price)

                    elif name == "Draw":
                        h2h.setdefault("draw", price)

                # -------------------------------------------------
                # TOTALS
                # -------------------------------------------------
                elif market_key == "totals":
                    point = outcome.get("point")

                    try:
                        point = float(point)
                    except Exception:
                        continue

                    # Prefer the standard 2.5 line.
                    if point == 2.5:
                        if name == "Over":
                            totals.setdefault("over", price)
                        elif name == "Under":
                            totals.setdefault("under", price)

                # -------------------------------------------------
                # SPREADS / HANDICAP
                # -------------------------------------------------
                elif market_key == "spreads":
                    spreads.append({
                        "name": name,
                        "point": outcome.get("point"),
                        "price": price,
                    })

    # Remove duplicate spread outcomes.
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

    # ---------------------------------------------------------
    # BEST BET
    # ---------------------------------------------------------
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
        x
        for x in candidates
        if 1.01 < x[1] <= 20
    ]

    candidates.sort(key=lambda x: x[1])

    best = None

    if candidates:
        selection, odd, market = candidates[0]

        best = {
            "selection": selection,
            "odd": odd,
            "market": market,
        }

    # ---------------------------------------------------------
    # TIME - Ethiopia UTC+3
    # ---------------------------------------------------------
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
                "%d/%m %H:%M"
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
# GET MATCHES - TODAY + NEXT 7 DAYS
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
    print(
        [x.get("key") for x in sports]
    )
    print("====================================")

    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD + 1)

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

                    # Only events with actual h2h odds.
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

    # Remove duplicate event IDs.
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
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# =========================================================
# TELEGRAM BUTTON HANDLER
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
            f"Balance: *{u['balance']:.2f}*\n"
            f"Bet Slip: *{len(u['betslip'])}*",
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
# =========================================================

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BEST BET</title>

<style>
*{box-sizing:border-box}

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

@media(max-width:500px){
 .teams{font-size:15px}
 .odds{gap:5px}
 .odd{padding:10px 3px}
}
</style>
</head>

<body>

<header>
<div class="logo">🎯 BEST BET</div>
<div class="sub">
Football • Today → Next 7 Days • Multiple Markets
</div>
</header>

<nav>
<button class="active" onclick="tab('matches',this)">
📅<br>Matches
</button>

<button onclick="tab('best',this)">
🎯<br>Best Bet
</button>

<button onclick="tab('live',this)">
🔴<br>Live
</button>

<button onclick="tab('slip',this)">
🎟️<br>Bet Slip
</button>
</nav>

<main>
<section id="content" class="panel">
Loading...
</section>
</main>

<script>
let matches=[];

let slip=JSON.parse(
 localStorage.getItem("bestbet_slip") || "[]"
);

function esc(x){
 return String(x ?? "").replace(/[&<>"']/g,m=>({
  "&":"&amp;",
  "<":"&lt;",
  ">":"&gt;",
  '"':"&quot;",
  "'":"&#039;"
 }[m]));
}

function saveSlip(){
 localStorage.setItem(
  "bestbet_slip",
  JSON.stringify(slip)
 );
}

async function api(path){
 const r=await fetch(path);
 const d=await r.json();

 if(!r.ok){
  throw new Error(
   d.error || "Server error"
  );
 }

 return d;
}

function tab(name,btn){
 document.querySelectorAll("nav button")
 .forEach(x=>x.classList.remove("active"));

 btn.classList.add("active");

 if(name==="matches") loadMatches();
 if(name==="best") loadBest();
 if(name==="live") loadLive();
 if(name==="slip") renderSlip();
}

function dayKey(date){
 const d=new Date(date);
 return d.toISOString().slice(0,10);
}

function dayLabel(date){
 const d=new Date(date);
 const today=new Date();
 const tomorrow=new Date();

 tomorrow.setDate(today.getDate()+1);

 const key=d.toISOString().slice(0,10);
 const todayKey=today.toISOString().slice(0,10);
 const tomorrowKey=tomorrow.toISOString().slice(0,10);

 if(key===todayKey) return "TODAY";
 if(key===tomorrowKey) return "TOMORROW";

 return d.toLocaleDateString(undefined,{
  weekday:"short",
  day:"numeric",
  month:"short"
 });
}

function loadMatches(){
 const c=document.getElementById("content");

 c.innerHTML=`
 <div class="status">
 ⏳ Loading football matches...
 </div>`;

 api("/api/matches")
 .then(d=>{
  matches=d.matches || [];

  if(!matches.length){
   c.innerHTML=`
   <h2>⚽ Football</h2>
   <div class="empty">
   No football matches with available odds
   found in the next 7 days.
   <br><br>
   <span class="status">
   ${esc(d.message || "")}
   </span>
   </div>`;
   return;
  }

  renderDayTabs();
 })
 .catch(e=>{
  c.innerHTML=`
  <h2>⚠️ Error</h2>
  <div class="error">${esc(e.message)}</div>`;
 });
}

function renderDayTabs(){
 const c=document.getElementById("content");
 const groups={};

 matches.forEach(m=>{
  const key=dayKey(m.commence_time);

  if(!groups[key]) groups[key]=[];

  groups[key].push(m);
 });

 const keys=Object.keys(groups).sort();

 if(!keys.length){
  c.innerHTML=`
  <div class="empty">No matches found.</div>`;
  return;
 }

 let html=`
 <h2>⚽ Football</h2>
 <div class="status">
 Today → next 7 days
 </div>
 <div class="days">`;

 keys.forEach((key,i)=>{
  html+=`
  <button
   class="${i===0?'active':''}"
   onclick="showDay('${key}',this)"
  >
   ${esc(dayLabel(key+"T12:00:00"))}
   <br>
   <small>${groups[key].length}</small>
  </button>`;
 });

 html+=`
 </div>
 <div id="dayMatches"></div>`;

 c.innerHTML=html;

 showDay(
  keys[0],
  document.querySelector(".days button")
 );
}

function showDay(key,btn){
 document.querySelectorAll(".days button")
 .forEach(x=>x.classList.remove("active"));

 if(btn) btn.classList.add("active");

 const list=matches.filter(
  m=>dayKey(m.commence_time)===key
 );

 const box=document.getElementById("dayMatches");

 if(!box) return;

 let html=`
 <div class="day-title">
 ${esc(dayLabel(key+"T12:00:00"))}
 </div>`;

 if(!list.length){
  html+=`
  <div class="empty">No matches.</div>`;
 }else{
  html+=list.map(renderMatch).join("");
 }

 box.innerHTML=html;
}

function renderMatch(m){
 const h=m.h2h || {};

 return `
 <div class="match">

 <div class="teams">
 ${esc(m.home)}
 <span class="status">vs</span>
 ${esc(m.away)}
 </div>

 <div class="meta">
 🏆 ${esc(m.league)}
 •
 🕐 ${esc(m.time)}
 </div>

 <div class="odds">

 ${oddButton(m,"1",h.home,"home")}

 ${oddButton(m,"X",h.draw,"draw")}

 ${oddButton(m,"2",h.away,"away")}

 </div>

 <button
  class="big back"
  onclick="openMatch('${esc(m.id)}')"
 >
 📊 View All Markets
 </button>

 </div>`;
}

function isSelected(id,betId,selection){
 return slip.some(
  x=>
   String(x.id)===String(id)
   &&
   String(x.betId)===String(betId)
   &&
   x.selection===selection
 );
}

function oddButton(m,label,odd){
 if(!odd){
  return `
  <div class="odd">
   ${label}
   <b>-</b>
  </div>`;
 }

 const selected=isSelected(
  m.id,
  "h2h",
  label
 );

 return `
 <div
  class="odd ${selected ? "selected" : ""}"
  onclick='selectBet(${JSON.stringify({
   id:m.id,
   home:m.home,
   away:m.away,
   league:m.league,
   market:"1X2",
   selection:label,
   odd:Number(odd),
   betId:"h2h"
  })})'
 >
  ${label}
  <b>${Number(odd).toFixed(2)}</b>
 </div>`;
}

function selectBet(bet){
 if(!bet.odd || Number(bet.odd)<=1){
  alert("Odd is not available.");
  return;
 }

 // Same match + same market replaces old selection.
 slip=slip.filter(
  x=>!(
   String(x.id)===String(bet.id)
   &&
   String(x.betId)===String(bet.betId)
  )
 );

 slip.push(bet);
 saveSlip();

 openMatch(String(bet.id));
}

function marketButton(
 m,
 market,
 selection,
 odd,
 betId
){
 const selected=isSelected(
  m.id,
  betId,
  selection
 );

 return `
 <button
  class="selection ${selected ? "selected" : ""}"
  onclick='selectBet(${JSON.stringify({
   id:m.id,
   home:m.home,
   away:m.away,
   league:m.league,
   market:market,
   selection:selection,
   odd:Number(odd),
   betId:String(betId)
  })})'
 >
  <span>${esc(selection)}</span>
  <b>@ ${Number(odd).toFixed(2)}</b>
 </button>`;
}

function openMatch(id){
 const c=document.getElementById("content");

 c.innerHTML=`
 <div class="status">
 ⏳ Loading markets...
 </div>`;

 api("/api/match/"+encodeURIComponent(id))
 .then(d=>{
  const m=d.match;
  const markets=d.markets || [];

  let html=`
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
  </div>`;

  if(d.best_bet){
   html+=`
   <div class="market best-card">
    <div class="market-title">
    🎯 Best Bet
    </div>

    <div style="padding:12px">
    <div class="yellow">
    <b>${esc(d.best_bet.selection)}</b>
    @
    ${Number(d.best_bet.odd).toFixed(2)}
    </div>
    </div>
   </div>`;
  }

  if(!markets.length){
   html+=`
   <div class="market">
    <div class="market-title">
    ⚠️ Odds
    </div>

    <div
     style="padding:12px"
     class="error"
    >
    ${esc(
     d.odds_error ||
     "No markets available."
    )}
    </div>
   </div>`;
  }

  markets.forEach(market=>{
   html+=`
   <div class="market">
    <div class="market-title">
    ${esc(market.name)}
    </div>

    <div class="market-grid">`;

   (market.selections || []).forEach(s=>{
    html+=marketButton(
     m,
     market.name,
     s.value,
     s.odd,
     market.id
    );
   });

   html+=`
    </div>
   </div>`;
  });

  html+=`
  <button
   class="big"
   onclick="renderSlip()"
  >
  🎟️ Bet Slip (${slip.length})
  </button>`;

  c.innerHTML=html;
 })
 .catch(e=>{
  c.innerHTML=`
  <h2>⚠️ Error</h2>
  <div class="error">${esc(e.message)}</div>`;
 });
}

function loadBest(){
 const c=document.getElementById("content");

 c.innerHTML=`
 <div class="status">
 ⏳ Finding best bets...
 </div>`;

 api("/api/matches")
 .then(d=>{
  const list=(d.matches || [])
   .filter(x=>x.best_bet);

  if(!list.length){
   c.innerHTML=`
   <h2>🎯 Best Bet</h2>
   <div class="empty">
   No Best Bet available in the current
   7-day odds data.
   <br><br>
   <span class="status">
   First make sure ODDS_API_KEY is configured
   and the API returns soccer h2h odds.
   </span>
   </div>`;
   return;
  }

  let html=`
  <h2>🎯 Best Bet</h2>
  <div class="status">
  Odds-based suggestion.
  Not a guarantee.
  </div>`;

  list.forEach(m=>{
   html+=`
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
    <b>${esc(m.best_bet.selection)}</b>
    @
    ${Number(m.best_bet.odd).toFixed(2)}
    </div>

    <button
     class="big"
     onclick="openMatch('${esc(m.id)}')"
    >
    📊 Open Markets
    </button>

   </div>`;
  });

  c.innerHTML=html;
 })
 .catch(e=>{
  c.innerHTML=`
  <h2>⚠️ Error</h2>
  <div class="error">${esc(e.message)}</div>`;
 });
}

function loadLive(){
 const c=document.getElementById("content");

 c.innerHTML=`
 <div class="status">
 ⏳ Loading live matches...
 </div>`;

 api("/api/live")
 .then(d=>{
  const list=d.matches || [];

  if(!list.length){
   c.innerHTML=`
   <h2>🔴 Live</h2>
   <div class="empty">
   No live matches now.
   </div>`;
   return;
  }

  c.innerHTML=`
  <h2>🔴 Live</h2>
  ${
   list.map(x=>`
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
   `).join("")
  }`;
 })
 .catch(e=>{
  c.innerHTML=`
  <h2>🔴 Live</h2>
  <div class="error">${esc(e.message)}</div>`;
 });
}

function renderSlip(){
 const c=document.getElementById("content");

 if(!slip.length){
  c.innerHTML=`
  <h2>🎟️ Bet Slip</h2>
  <div class="empty">
  Your Bet Slip is empty.
  </div>`;
  return;
 }

 let total=1;

 let html=`
 <h2>🎟️ Bet Slip</h2>`;

 slip.forEach((x,i)=>{
  total*=Number(x.odd);

  html+=`
  <div class="slip-item">

   <b>
   ${i+1}.
   ${esc(x.home)}
   vs
   ${esc(x.away)}
   </b>

   <div class="meta">
   ${esc(x.league || "")}
   </div>

   <div>
   📊 ${esc(x.market)}
   </div>

   <div class="yellow">
   🎯
   ${esc(x.selection)}
   @
   ${Number(x.odd).toFixed(2)}
   </div>

   <button
    class="big back"
    onclick="removeBet(${i})"
   >
   🗑 Remove
   </button>

  </div>`;
 });

 html+=`
 <div class="market">
  <div class="market-title">
  📈 Total Odds: ${total.toFixed(2)}
  </div>
 </div>

 <button
  class="big"
  onclick="clearSlip()"
 >
 🗑 Clear Bet Slip
 </button>`;

 c.innerHTML=html;
}

function removeBet(i){
 slip.splice(i,1);
 saveSlip();
 renderSlip();
}

function clearSlip(){
 slip=[];
 saveSlip();
 renderSlip();
}

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
        "api_key_configured": bool(ODDS_API_KEY),
        "list_markets": LIST_MARKETS,
        "detail_markets": DETAIL_MARKETS,
        "days": DAYS_AHEAD,
        "web_app": WEB_APP_URL,
    })


# =========================================================
# MATCHES API
# =========================================================

@web_app.route("/api/matches")
def api_matches():
    try:
        matches = get_matches()

        return jsonify({
            "success": True,
            "count": len(matches),
            "matches": matches,
            "message": (
                "Football odds loaded for today and "
                "the next 7 days."
                if matches
                else
                "No football matches with available odds "
                "found in the next 7 days. Check ODDS_API_KEY, "
                "ODDS_REGIONS and The Odds API soccer coverage."
            ),
        })

    except Exception as e:
        print("[API MATCHES ERROR]", e)

        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(e),
        }), 500


# =========================================================
# SINGLE MATCH
# =========================================================

@web_app.route("/api/match/<match_id>")
def api_match(match_id):
    try:
        matches = get_matches()

        match = next(
            (
                x
                for x in matches
                if str(x.get("id")) == str(match_id)
            ),
            None,
        )

        if not match:
            return jsonify({
                "error": "Match hin argamne."
            }), 404

        # IMPORTANT:
        # The list uses h2h only for wider coverage.
        # Once one match is selected, request extra markets.
        events = odds_request(
            f"/sports/{match['sport_key']}/odds",
            {
                "regions": REGIONS,
                "markets": DETAIL_MARKETS,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )

        event = next(
            (
                x
                for x in events
                if str(x.get("id")) == str(match_id)
            ),
            None,
        )

        if not event:
            return jsonify({
                "match": match,
                "markets": [],
                "best_bet": match.get("best_bet"),
                "odds_error":
                    "Current odds hin argamne.",
            })

        converted = convert_event(
            event,
            {
                "key": match["sport_key"],
                "title": match["league"],
            },
        )

        markets = []

        # -------------------------------------------------
        # 1X2
        # -------------------------------------------------
        if converted["h2h"]:
            selections = []

            if converted["h2h"].get("home"):
                selections.append({
                    "value": "1",
                    "odd": converted["h2h"]["home"],
                })

            if converted["h2h"].get("draw"):
                selections.append({
                    "value": "X",
                    "odd": converted["h2h"]["draw"],
                })

            if converted["h2h"].get("away"):
                selections.append({
                    "value": "2",
                    "odd": converted["h2h"]["away"],
                })

            if selections:
                markets.append({
                    "id": "h2h",
                    "name": "🎯 1X2",
                    "selections": selections,
                })

        # -------------------------------------------------
        # OVER / UNDER
        # -------------------------------------------------
        if converted["totals"]:
            selections = []

            if converted["totals"].get("over"):
                selections.append({
                    "value": "Over 2.5",
                    "odd": converted["totals"]["over"],
                })

            if converted["totals"].get("under"):
                selections.append({
                    "value": "Under 2.5",
                    "odd": converted["totals"]["under"],
                })

            if selections:
                markets.append({
                    "id": "totals",
                    "name": "⚽ Over / Under",
                    "selections": selections,
                })

        # -------------------------------------------------
        # HANDICAP
        # -------------------------------------------------
        if converted["spreads"]:
            selections = []

            for item in converted["spreads"]:
                selections.append({
                    "value": (
                        f"{item['name']} "
                        f"{item['point']}"
                    ),
                    "odd": item["price"],
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
        })

    except Exception as e:
        print("[MATCH ERROR]", e)

        return jsonify({
            "success": False,
            "error": str(e),
            "markets": [],
        }), 500


# =========================================================
# LIVE
# =========================================================

@web_app.route("/api/live")
def api_live():
    try:
        result = []
        sports = soccer_sports()

        for sport in sports[:20]:
            try:
                scores = odds_request(
                    f"/sports/{sport['key']}/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat": "iso",
                    },
                )
            except Exception as e:
                print(
                    "[LIVE SKIP]",
                    sport.get("key"),
                    e,
                )
                continue

            for event in scores:
                if event.get("completed"):
                    continue

                score_map = {}

                for score in (
                    event.get("scores") or []
                ):
                    score_map[
                        score.get("name")
                    ] = score.get("score")

                result.append({
                    "id": event.get("id"),
                    "league": sport.get("title"),
                    "home": event.get("home_team"),
                    "away": event.get("away_team"),
                    "home_score":
                        score_map.get(
                            event.get("home_team")
                        ),
                    "away_score":
                        score_map.get(
                            event.get("away_team")
                        ),
                    "minute": "",
                })

        return jsonify({
            "success": True,
            "matches": result,
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
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
            "WARNING: ODDS_API_KEY hin jiru. "
            "Web app will not load football odds."
        )

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

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
            button_handler,
        )
    )

    print("====================================")
    print("BEST BET BOT ONLINE")
    print("WEB APP:", WEB_APP_URL)
    print("ODDS API:", bool(ODDS_API_KEY))
    print("LIST MARKETS:", LIST_MARKETS)
    print("DETAIL MARKETS:", DETAIL_MARKETS)
    print("DAYS:", DAYS_AHEAD)
    print("====================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
