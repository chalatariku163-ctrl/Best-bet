import os
import random
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


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
MARKETS = "h2h,totals,spreads"

web_app = Flask(__name__)

# Demo-only in-memory user data.
USERS = {}


# =========================================================
# DEMO USER DATA
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
        total *= float(item["odd"])
    return total


def add_demo_bet(user_id, match, market, selection, odd, bet_id=None):
    try:
        odd = float(odd)
    except Exception:
        return False

    if odd <= 1:
        return False

    user = get_user(user_id)

    # One selection per fixture + market.
    user["betslip"] = [
        x for x in user["betslip"]
        if not (
            str(x["fixture_id"]) == str(match["id"])
            and str(x.get("bet_id")) == str(bet_id)
        )
    ]

    user["betslip"].append({
        "fixture_id": match["id"],
        "home": match["home"],
        "away": match["away"],
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

    text += f"━━━━━━━━━━━━━━\n📈 *Total Odds:* {total:.2f}\n\n"
    text += "🧪 Demo/testing qofa."
    return text


# =========================================================
# THE ODDS API
# =========================================================

def odds_request(path, params=None):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY hin jiru.")

    params = dict(params or {})
    params["apiKey"] = ODDS_API_KEY

    r = requests.get(
        ODDS_BASE + path,
        params=params,
        timeout=30,
        headers={"Accept": "application/json"},
    )

    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
        raise RuntimeError(
            f"Odds API error {r.status_code}: {body}"
        )

    return r.json()


def soccer_sports():
    sports = odds_request("/sports")
    return [
        s for s in sports
        if s.get("active") and str(s.get("key", "")).startswith("soccer_")
    ]


def convert_event(event, sport):
    h2h = {}
    totals = {}
    spreads = []

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            key = market.get("key")

            for outcome in market.get("outcomes", []):
                name = str(outcome.get("name", ""))
                price = outcome.get("price")
                if price is None:
                    continue

                try:
                    price = float(price)
                except Exception:
                    continue

                if key == "h2h":
                    if name == event.get("home_team"):
                        h2h.setdefault("home", price)
                    elif name == event.get("away_team"):
                        h2h.setdefault("away", price)
                    elif name == "Draw":
                        h2h.setdefault("draw", price)

                elif key == "totals":
                    point = outcome.get("point")
                    if point is None:
                        continue
                    try:
                        point = float(point)
                    except Exception:
                        continue
                    if point == 2.5:
                        if name == "Over":
                            totals.setdefault("over", price)
                        elif name == "Under":
                            totals.setdefault("under", price)

                elif key == "spreads":
                    spreads.append({
                        "name": name,
                        "point": outcome.get("point"),
                        "price": price,
                    })

    # Remove duplicate spread choices.
    unique_spreads = []
    seen = set()
    for x in spreads:
        k = (x["name"], str(x["point"]), x["price"])
        if k not in seen:
            seen.add(k)
            unique_spreads.append(x)

    # Best Bet is only an odds-based suggestion, not a guarantee.
    candidates = []
    if h2h.get("home"):
        candidates.append(("1", h2h["home"], "1X2"))
    if h2h.get("draw"):
        candidates.append(("X", h2h["draw"], "1X2"))
    if h2h.get("away"):
        candidates.append(("2", h2h["away"], "1X2"))
    if totals.get("over"):
        candidates.append(("Over 2.5", totals["over"], "Over/Under"))
    if totals.get("under"):
        candidates.append(("Under 2.5", totals["under"], "Over/Under"))

    candidates = [x for x in candidates if 1.01 < x[1] <= 5.0]
    candidates.sort(key=lambda x: x[1])

    best = None
    if candidates:
        selection, odd, market = candidates[0]
        best = {
            "selection": selection,
            "odd": odd,
            "market": market,
        }

    commence = event.get("commence_time", "")
    time_text = ""
    if commence:
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            time_text = dt.strftime("%H:%M")
        except Exception:
            pass

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


def get_matches():
    result = []

    for sport in soccer_sports()[:20]:
        try:
            events = odds_request(
                f"/sports/{sport['key']}/odds",
                {
                    "regions": REGIONS,
                    "markets": MARKETS,
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
            )
            for event in events:
                result.append(convert_event(event, sport))
        except Exception as e:
            print("[SPORT SKIP]", sport.get("key"), e)

    result.sort(key=lambda x: x.get("commence_time") or "")
    return result


# =========================================================
# TELEGRAM MENUS
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🎮 PLAY BEST BET",
            web_app=WebAppInfo(url=WEB_APP_URL),
        )],
        [InlineKeyboardButton(
            "⚽ FOOTBALL",
            web_app=WebAppInfo(url=WEB_APP_URL),
        )],
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
        "⚽ Football odds\n"
        "📊 Multiple markets\n"
        "🎟️ Bet Slip\n\n"
        "👇 *⚽ FOOTBALL* cuqaasi.",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user
    u = get_user(user.id, user.first_name or "User")

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
            f"💳 *BALANCE*\n\nBalance: *{u['balance']:.2f}*\n\n"
            "🧪 Demo system qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif q.data == "history":
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
        await q.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif q.data == "how":
        await q.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football bani\n"
            "2. Match filadhu\n"
            "3. Market keessaa selection filadhu\n"
            "4. Selection magariisa ta'a\n"
            "5. 🎟️ Bet Slip keessatti argita\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEB APP
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
body{margin:0;background:#0d1724;color:#fff;font-family:Arial,sans-serif}
header{background:#152536;padding:20px 15px;text-align:center}
.logo{font-size:31px;font-weight:900;color:#ffd400}
.sub{font-size:13px;color:#9fb0c2;margin-top:5px}
nav{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;padding:10px;background:#111f2e;position:sticky;top:0;z-index:5}
nav button{border:0;border-radius:12px;padding:10px 3px;background:#233547;color:#fff;font-weight:800}
nav button.active{background:#ffd400;color:#111}
main{padding:14px;max-width:900px;margin:auto}
.panel{background:#152536;border-radius:18px;padding:15px}
.match{background:#1b2c3d;border-radius:16px;padding:15px;margin:12px 0}
.teams{font-size:18px;font-weight:800}
.meta{font-size:12px;color:#9fb0c2;margin:8px 0 12px}
.odds{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.odd{background:#26394b;padding:13px 7px;border-radius:12px;text-align:center;cursor:pointer;border:2px solid transparent}
.odd b{display:block;font-size:18px;margin-top:4px}
.odd.selected{background:#2ecc71!important;color:#111;border-color:#fff;box-shadow:0 0 12px rgba(46,204,113,.45)}
.odd.selected b{color:#111}
.market{background:#172a3b;border-radius:14px;margin-top:12px;overflow:hidden}
.market-title{padding:12px;font-weight:800;background:#203448}
.market-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px}
.selection{background:#26394b;border:2px solid transparent;border-radius:11px;padding:11px;cursor:pointer;text-align:left;color:#fff}
.selection.selected{background:#2ecc71;color:#111;border-color:#fff}
.selection span{display:block;font-size:12px;color:#b8c7d3}
.selection.selected span{color:#111}
.selection b{display:block;margin-top:3px}
.big{width:100%;border:0;border-radius:13px;padding:14px;margin-top:12px;background:#ffd400;color:#111;font-weight:900}
.back{background:#26394b;color:#fff}
.slip-item{background:#1b2c3d;padding:12px;border-radius:12px;margin:8px 0}
.status{color:#9fb0c2;font-size:13px}
.error{color:#ff7b7b;white-space:pre-wrap}
.yellow{color:#ffd400}
@media(max-width:500px){.teams{font-size:16px}}
</style>
</head>
<body>

<header>
<div class="logo">🎯 BEST BET</div>
<div class="sub">Football odds • multiple markets • bet slip</div>
</header>

<nav>
<button class="active" onclick="tab('today',this)">📅<br>Today</button>
<button onclick="tab('best',this)">🎯<br>Best Bet</button>
<button onclick="tab('live',this)">🔴<br>Live</button>
<button onclick="tab('slip',this)">🎟️<br>Bet Slip</button>
</nav>

<main><section id="content" class="panel">Loading...</section></main>

<script>
let matches=[];
let slip=JSON.parse(localStorage.getItem("bestbet_slip")||"[]");

function esc(x){
 return String(x??"").replace(/[&<>"']/g,m=>({
 "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
 }[m]));
}

function saveSlip(){
 localStorage.setItem("bestbet_slip",JSON.stringify(slip));
}

async function api(path){
 const r=await fetch(path);
 const d=await r.json();
 if(!r.ok) throw new Error(d.error||"Server error");
 return d;
}

function tab(name,btn){
 document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
 btn.classList.add("active");
 if(name==="today") loadToday();
 if(name==="best") loadBest();
 if(name==="live") loadLive();
 if(name==="slip") renderSlip();
}

function isSelected(id,betId,selection){
 return slip.some(x=>
   String(x.id)===String(id) &&
   String(x.betId)===String(betId) &&
   x.selection===selection
 );
}

function loadToday(){
 const c=document.getElementById("content");
 c.innerHTML='<div class="status">⏳ Loading matches...</div>';

 api("/api/matches").then(d=>{
   matches=d.matches||[];
   if(!matches.length){
     c.innerHTML="<h2>📅 Today Matches</h2><div class='status'>No matches found.</div>";
     return;
   }
   c.innerHTML="<h2>📅 Today Matches</h2>"+
     "<div class='status'>Real odds from The Odds API</div>"+
     matches.map(renderMatch).join("");
 }).catch(e=>{
   c.innerHTML="<h2>⚠️ Error</h2><div class='error'>"+esc(e.message)+"</div>";
 });
}

function renderMatch(m){
 const h=m.h2h||{};
 return `
 <div class="match">
   <div class="teams">${esc(m.home)} <span class="status">vs</span> ${esc(m.away)}</div>
   <div class="meta">🏆 ${esc(m.league)} • 🕐 ${esc(m.time)}</div>
   <div class="odds">
     ${oddButton(m,"1",h.home,"home")}
     ${oddButton(m,"X",h.draw,"draw")}
     ${oddButton(m,"2",h.away,"away")}
   </div>
   <button class="big back" onclick="openMatch('${esc(m.id)}')">
     📊 View all betting markets
   </button>
 </div>`;
}

function oddButton(m,label,odd,side){
 if(!odd) return `<div class="odd"><span>${label}</span><b>-</b></div>`;
 const selected=isSelected(m.id,"h2h",label);
 return `
 <div class="odd ${selected?"selected":""}"
      onclick='selectBet(${JSON.stringify({
        id:m.id,home:m.home,away:m.away,league:m.league,
        market:"1X2",selection:label,odd:Number(odd),betId:"h2h"
      })})'>
   ${label}<b>${Number(odd).toFixed(2)}</b>
 </div>`;
}

function selectBet(bet){
 if(!bet.odd||Number(bet.odd)<=1){
   alert("Odd is not available.");
   return;
 }

 // Same fixture + same market: only one selection.
 slip=slip.filter(x=>!(
   String(x.id)===String(bet.id) &&
   String(x.betId)===String(bet.betId)
 ));

 slip.push(bet);
 saveSlip();

 // Re-render current match so selected choice becomes green.
 openMatch(String(bet.id));
}

function marketButton(m,market,selection,odd,betId){
 const selected=isSelected(m.id,betId,selection);
 return `
 <button class="selection ${selected?"selected":""}"
   onclick='selectBet(${JSON.stringify({
     id:m.id,home:m.home,away:m.away,league:m.league,
     market:market,selection:selection,odd:Number(odd),betId:String(betId)
   })})'>
   <span>${esc(selection)}</span>
   <b>@ ${Number(odd).toFixed(2)}</b>
 </button>`;
}

function openMatch(id){
 const c=document.getElementById("content");
 c.innerHTML="<div class='status'>⏳ Loading markets...</div>";

 api("/api/match/"+encodeURIComponent(id)).then(d=>{
   const m=d.match;
   const markets=d.markets||[];
   let html=`
    <button class="big back" onclick="loadToday()">⬅️ Matches</button>
    <h2>⚽ ${esc(m.home)} vs ${esc(m.away)}</h2>
    <div class="meta">🏆 ${esc(m.league)} • 🕐 ${esc(m.time)}</div>`;

   if(d.best_bet){
     html+=`
     <div class="market">
       <div class="market-title">🎯 Best Bet</div>
       <div style="padding:12px" class="yellow">
         <b>${esc(d.best_bet.selection)}</b>
         @ ${Number(d.best_bet.odd).toFixed(2)}
       </div>
     </div>`;
   }

   if(!markets.length){
     html+=`<div class="market"><div class="market-title">⚠️ Odds</div>
       <div style="padding:12px" class="error">${esc(d.odds_error||"No markets")}</div></div>`;
   }

   markets.forEach(market=>{
     html+=`
     <div class="market">
       <div class="market-title">${esc(market.name)}</div>
       <div class="market-grid">`;

     (market.selections||[]).forEach(s=>{
       html+=marketButton(
         m,market.name,s.value,s.odd,
         market.id
       );
     });

     html+="</div></div>";
   });

   html+=`
    <button class="big" onclick="renderSlip()">
      🎟️ Bet Slip (${slip.length})
    </button>`;

   c.innerHTML=html;
 }).catch(e=>{
   c.innerHTML="<h2>⚠️ Error</h2><div class='error'>"+esc(e.message)+"</div>";
 });
}

function loadBest(){
 const c=document.getElementById("content");
 c.innerHTML="<div class='status'>⏳ Finding best available selection...</div>";

 api("/api/matches").then(d=>{
   const list=(d.matches||[]).filter(x=>x.best_bet);
   if(!list.length){
     c.innerHTML="<h2>🎯 Best Bet</h2><div class='status'>No best bet available.</div>";
     return;
   }

   c.innerHTML="<h2>🎯 Best Bet</h2>"+
     "<div class='status'>Odds-based suggestion; not guaranteed.</div>"+
     list.slice(0,10).map(m=>`
       <div class="match">
         <div class="teams">${esc(m.home)} vs ${esc(m.away)}</div>
         <div class="meta">${esc(m.league)} • ${esc(m.time)}</div>
         <div class="yellow">
           🎯 ${esc(m.best_bet.selection)}
           @ ${Number(m.best_bet.odd).toFixed(2)}
         </div>
         <button class="big" onclick="openMatch('${esc(m.id)}')">
           📊 Open Markets
         </button>
       </div>
     `).join("");
 }).catch(e=>{
   c.innerHTML="<h2>⚠️ Error</h2><div class='error'>"+esc(e.message)+"</div>";
 });
}

function loadLive(){
 const c=document.getElementById("content");
 c.innerHTML="<div class='status'>⏳ Loading live...</div>";

 api("/api/live").then(d=>{
   const list=d.matches||[];
   if(!list.length){
     c.innerHTML="<h2>🔴 Live</h2><div class='status'>No live matches now.</div>";
     return;
   }
   c.innerHTML="<h2>🔴 Live</h2>"+
    list.map(x=>`
      <div class="match">
       <div class="teams">
        ${esc(x.home)} ${x.home_score??0} -
        ${x.away_score??0} ${esc(x.away)}
       </div>
       <div class="meta">⏱️ ${esc(x.minute||"-")}'</div>
      </div>
    `).join("");
 }).catch(e=>{
   c.innerHTML="<h2>🔴 Live</h2><div class='error'>"+esc(e.message)+"</div>";
 });
}

function renderSlip(){
 const c=document.getElementById("content");

 if(!slip.length){
   c.innerHTML="<h2>🎟️ Bet Slip</h2><div class='status'>Your Bet Slip is empty.</div>";
   return;
 }

 let total=1;
 let html="<h2>🎟️ Bet Slip</h2>";

 slip.forEach((x,i)=>{
   total*=Number(x.odd);
   html+=`
    <div class="slip-item">
      <b>${i+1}. ${esc(x.home)} vs ${esc(x.away)}</b>
      <div class="meta">${esc(x.league||"")}</div>
      <div>📊 ${esc(x.market)}</div>
      <div class="yellow">🎯 ${esc(x.selection)} @ ${Number(x.odd).toFixed(2)}</div>
      <button class="big back" onclick="removeBet(${i})">🗑 Remove</button>
    </div>`;
 });

 html+=`
   <div class="market">
     <div class="market-title">📈 Total Odds: ${total.toFixed(2)}</div>
   </div>
   <button class="big" onclick="clearSlip()">🗑 Clear Bet Slip</button>`;

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

loadToday();
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
        "web_app": WEB_APP_URL,
    })


@web_app.route("/api/matches")
def api_matches():
    try:
        return jsonify({
            "success": True,
            "matches": get_matches(),
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
        }), 500


@web_app.route("/api/match/<match_id>")
def api_match(match_id):
    try:
        matches = get_matches()
        match = next(
            (x for x in matches if str(x["id"]) == str(match_id)),
            None,
        )

        if not match:
            return jsonify({
                "error": "Match hin argamne."
            }), 404

        # Re-query the sport containing this match for current markets.
        events = odds_request(
            f"/sports/{match['sport_key']}/odds",
            {
                "regions": REGIONS,
                "markets": MARKETS,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )

        event = next(
            (x for x in events if str(x.get("id")) == str(match_id)),
            None,
        )

        if not event:
            return jsonify({
                "match": match,
                "markets": [],
                "odds_error": "Current odds hin argamne.",
            })

        converted = convert_event(
            event,
            {"key": match["sport_key"], "title": match["league"]},
        )

        markets = []

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
            markets.append({
                "id": "h2h",
                "name": "🎯 1X2",
                "selections": selections,
            })

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
            markets.append({
                "id": "totals",
                "name": "⚽ Over / Under",
                "selections": selections,
            })

        if converted["spreads"]:
            selections = [
                {
                    "value": f"{x['name']} {x['point']}",
                    "odd": x["price"],
                }
                for x in converted["spreads"]
            ]
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
        return jsonify({
            "success": False,
            "error": str(e),
            "markets": [],
        }), 500


@web_app.route("/api/live")
def api_live():
    try:
        result = []

        for sport in soccer_sports()[:15]:
            try:
                scores = odds_request(
                    f"/sports/{sport['key']}/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat": "iso",
                    },
                )
            except Exception:
                continue

            for event in scores:
                if event.get("completed"):
                    continue

                score_map = {}
                for s in event.get("scores") or []:
                    score_map[s.get("name")] = s.get("score")

                result.append({
                    "id": event.get("id"),
                    "league": sport.get("title"),
                    "home": event.get("home_team"),
                    "away": event.get("away_team"),
                    "home_score": score_map.get(event.get("home_team")),
                    "away_score": score_map.get(event.get("away_team")),
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
        raise RuntimeError("BOT_TOKEN hin jiru.")

    if not ODDS_API_KEY:
        print("WARNING: ODDS_API_KEY hin jiru.")

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
        CommandHandler("start", start)
    )
    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("====================================")
    print("BEST BET BOT ONLINE")
    print("WEB APP:", WEB_APP_URL)
    print("ODDS API:", bool(ODDS_API_KEY))
    print("====================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
