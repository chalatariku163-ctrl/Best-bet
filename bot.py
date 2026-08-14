import os
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template, request

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
).strip().rstrip("/")

PORT = int(os.getenv("PORT", "10000"))

ODDS_BASE = "https://api.the-odds-api.com/v4"

# Keep this small. More regions/markets = more API quota usage.
REGIONS = [
    x.strip().lower()
    for x in os.getenv("ODDS_REGIONS", "eu,uk").split(",")
    if x.strip()
]
REGION_FALLBACKS = ["eu", "uk", "us", "au"]

# 7 calendar days from now.
DAYS_AHEAD = 7

# Lower than 50 to prevent very slow loading and quota waste.
MAX_SOCCER_SPORTS = int(os.getenv("MAX_SOCCER_SPORTS", "15"))

# Keep the main list lightweight.
LIST_MARKETS = os.getenv(
    "LIST_MARKETS",
    "h2h,totals,spreads",
).strip()

DETAIL_MARKETS = os.getenv(
    "DETAIL_MARKETS",
    "h2h,totals,spreads,btts",
).strip()

CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "90"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "12"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

USERS = {}

MATCH_CACHE = {
    "time": 0.0,
    "matches": [],
    "error": None,
}


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
        text += (
            f"*{i}.* {item.get('home', '')} vs {item.get('away', '')}\n"
            f"🏆 {item.get('league', '')}\n"
            f"📊 {item.get('market', '')}\n"
            f"🎯 *{item.get('selection', '')}*\n"
            f"Odd: *{float(item.get('odd', 0)):.2f}*\n\n"
        )

    text += f"━━━━━━━━━━━━━━\n📈 *Total Odds:* {total:.2f}\n\n🧪 Demo/testing qofa."
    return text


# =========================================================
# ODDS API
# =========================================================

def odds_request(path, params=None):
    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru. Render → Environment keessatti ODDS_API_KEY galchi."
        )

    p = dict(params or {})
    p["apiKey"] = ODDS_API_KEY

    url = ODDS_BASE + path

    response = requests.get(
        url,
        params=p,
        timeout=API_TIMEOUT,
        headers={
            "Accept": "application/json",
            "User-Agent": "BestBet/2.0",
        },
    )

    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = response.text[:500]

        raise RuntimeError(
            f"Odds API HTTP {response.status_code}: {body}"
        )

    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError("Odds API JSON hin deebine.") from exc


def soccer_sports():
    sports = odds_request("/sports")
    result = []

    for sport in sports:
        key = str(sport.get("key", "")).strip()
        if sport.get("active") and key.startswith("soccer_"):
            result.append(sport)

    return result


def region_list():
    result = []
    for region in REGIONS + REGION_FALLBACKS:
        if region and region not in result:
            result.append(region)
    return result


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# =========================================================
# PRIORITIZE COMMON FOOTBALL LEAGUES
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
]


def select_soccer_sports(sports):
    by_key = {str(x.get("key")): x for x in sports}
    selected = []

    for key in PRIORITY_KEYS:
        if key in by_key:
            selected.append(by_key[key])

    for sport in sports:
        if sport not in selected:
            selected.append(sport)
        if len(selected) >= MAX_SOCCER_SPORTS:
            break

    return selected[:MAX_SOCCER_SPORTS]


# =========================================================
# EVENT CONVERSION
# =========================================================

def convert_event(event, sport):
    h2h = {}
    totals = {}
    btts = {}
    spreads = []

    for bookmaker in event.get("bookmakers") or []:
        for market in bookmaker.get("markets") or []:
            market_key = market.get("key")
            outcomes = market.get("outcomes") or []

            for outcome in outcomes:
                name = str(outcome.get("name", "")).strip()
                price = safe_float(outcome.get("price"))

                if price is None or price <= 1:
                    continue

                if market_key == "h2h":
                    if name == event.get("home_team"):
                        h2h["home"] = max(h2h.get("home", 0), price)
                    elif name == event.get("away_team"):
                        h2h["away"] = max(h2h.get("away", 0), price)
                    elif name.lower() == "draw":
                        h2h["draw"] = max(h2h.get("draw", 0), price)

                elif market_key == "totals":
                    point = safe_float(outcome.get("point"))
                    if point != 2.5:
                        continue
                    if name.lower() == "over":
                        totals["over"] = max(totals.get("over", 0), price)
                    elif name.lower() == "under":
                        totals["under"] = max(totals.get("under", 0), price)

                elif market_key in ("btts", "both_teams_to_score"):
                    low = name.lower()
                    if low in ("yes", "btts yes"):
                        btts["yes"] = max(btts.get("yes", 0), price)
                    elif low in ("no", "btts no"):
                        btts["no"] = max(btts.get("no", 0), price)

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
            round(float(item["price"]), 5),
        )
        if key not in seen:
            seen.add(key)
            unique_spreads.append(item)

    unique_spreads.sort(
        key=lambda x: float(x["price"]),
        reverse=True,
    )

    # "Best bet" here means the lowest available decimal price,
    # not a prediction or guarantee of winning.
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
    if btts.get("yes"):
        candidates.append(("BTTS Yes", btts["yes"], "BTTS"))
    if btts.get("no"):
        candidates.append(("BTTS No", btts["no"], "BTTS"))

    candidates = [
        x for x in candidates
        if 1.01 < float(x[1]) <= 20
    ]
    candidates.sort(key=lambda x: float(x[1]))

    best = None
    if candidates:
        selection, odd, market = candidates[0]
        best = {
            "selection": selection,
            "odd": float(odd),
            "market": market,
        }

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
            time_text = local_dt.strftime("%d/%m %H:%M")
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
        "btts": btts,
        "spreads": unique_spreads,
        "best_bet": best,
    }


# =========================================================
# ONE SPORT
# =========================================================

def get_soccer_odds_for_sport(sport_key, start_text, end_text):
    last_error = None

    for region in region_list():
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

            if events:
                print(
                    "[ODDS OK]",
                    sport_key,
                    "region=",
                    region,
                    "events=",
                    len(events),
                )
                return events, region, None

            print("[NO EVENTS]", sport_key, region)

        except Exception as exc:
            last_error = str(exc)
            print("[REGION ERROR]", sport_key, region, repr(exc))

    return [], None, last_error


# =========================================================
# GET MATCHES
# =========================================================

def get_matches(force=False):
    now_ts = time.time()

    # Normal cache.
    if (
        not force
        and MATCH_CACHE["time"]
        and now_ts - MATCH_CACHE["time"] < CACHE_SECONDS
    ):
        return MATCH_CACHE["matches"]

    try:
        sports = soccer_sports()
    except Exception as exc:
        print("[SPORTS ERROR]", repr(exc))

        # Keep old data if API temporarily fails.
        if MATCH_CACHE["matches"]:
            return MATCH_CACHE["matches"]
        raise

    sports = select_soccer_sports(sports)

    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=DAYS_AHEAD)

    start_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_text = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("====================================")
    print("[SOCCER SPORTS SELECTED]", len(sports))
    print("[REGIONS]", region_list())
    print("[FROM]", start_text)
    print("[TO]", end_text)
    print("====================================")

    result = []
    errors = []

    # Parallel requests make the page much faster than 50 sequential requests.
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as pool:
        jobs = {
            pool.submit(
                get_soccer_odds_for_sport,
                sport.get("key"),
                start_text,
                end_text,
            ): sport
            for sport in sports
            if sport.get("key")
        }

        for future in as_completed(jobs):
            sport = jobs[future]

            try:
                events, used_region, error = future.result()
            except Exception as exc:
                print("[SPORT FUTURE ERROR]", repr(exc))
                continue

            if error:
                errors.append(
                    f"{sport.get('key')}: {error}"
                )

            for event in events:
                try:
                    commence = event.get("commence_time")
                    if not commence:
                        continue

                    dt = datetime.fromisoformat(
                        commence.replace("Z", "+00:00")
                    )

                    if dt < now or dt > end_time:
                        continue

                    converted = convert_event(event, sport)
                    converted["odds_region"] = used_region or ""

                    if (
                        converted["h2h"]
                        or converted["totals"]
                        or converted["btts"]
                        or converted["spreads"]
                    ):
                        result.append(converted)

                except Exception as exc:
                    print("[EVENT ERROR]", repr(exc))

    # Remove duplicate fixtures.
    unique = {}
    for match in result:
        mid = str(match.get("id") or "")
        if mid:
            unique[mid] = match

    result = list(unique.values())
    result.sort(key=lambda x: x.get("commence_time", ""))

    MATCH_CACHE["time"] = time.time()
    MATCH_CACHE["matches"] = result
    MATCH_CACHE["error"] = errors[-10:] if errors else None

    print("[TOTAL MATCHES]", len(result))
    return result


# =========================================================
# TELEGRAM MENU
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 PLAY BEST BET",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ],
        [
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ],
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
            f"💳 *BALANCE*\n\n"
            f"Balance: *{u['balance']:.2f}*\n\n"
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
            "2. 📅 Guyyaa filadhu\n"
            "3. ⚽ Match filadhu\n"
            "4. 📊 Market filadhu\n"
            "5. 🎯 Selection filadhu\n"
            "6. 🎟️ Bet Slip ilaali\n\n"
            "📅 Today irraa kaasee *guyyaa 7* agarsiisa.\n\n"
            "🧪 Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )


# =========================================================
# WEB ROUTES
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "The Odds API",
        "api_key_configured": bool(ODDS_API_KEY),
        "list_markets": LIST_MARKETS,
        "detail_markets": DETAIL_MARKETS,
        "regions": region_list(),
        "days": DAYS_AHEAD,
        "cache_seconds": CACHE_SECONDS,
        "api_timeout": API_TIMEOUT,
        "max_soccer_sports": MAX_SOCCER_SPORTS,
        "web_app": WEB_APP_URL,
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "BEST BET API is working.",
        "status": "online",
    })


@app.route("/api/matches", methods=["GET"])
def api_matches():
    try:
        force = request.args.get("refresh", "0") == "1"
        matches = get_matches(force=force)

        if matches:
            return jsonify({
                "success": True,
                "count": len(matches),
                "matches": matches,
                "message": "Football odds loaded.",
            })

        return jsonify({
            "success": True,
            "count": 0,
            "matches": [],
            "message": (
                "No football matches with available odds "
                "were found for the next 7 days."
            ),
            "api_key_configured": bool(ODDS_API_KEY),
            "regions": region_list(),
        })

    except Exception as exc:
        print("[API MATCHES ERROR]", repr(exc))

        return jsonify({
            "success": False,
            "count": 0,
            "matches": [],
            "error": str(exc),
            "message": "Football odds loading failed.",
        }), 502


@app.route("/api/match/<match_id>", methods=["GET"])
def api_match(match_id):
    try:
        matches = get_matches()

        match = next(
            (
                x for x in matches
                if str(x.get("id")) == str(match_id)
            ),
            None,
        )

        if not match:
            return jsonify({
                "success": False,
                "error": "Match hin argamne.",
            }), 404

        events = []
        last_error = None
        used_region = None

        for region in region_list():
            try:
                events = odds_request(
                    f"/sports/{match['sport_key']}/odds",
                    {
                        "regions": region,
                        "markets": DETAIL_MARKETS,
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                )
                used_region = region
                if events:
                    break
            except Exception as exc:
                last_error = str(exc)
                print("[DETAIL ERROR]", region, repr(exc))

        event = next(
            (
                x for x in events
                if str(x.get("id")) == str(match_id)
            ),
            None,
        )

        if not event:
            return jsonify({
                "success": True,
                "match": match,
                "markets": [],
                "best_bet": match.get("best_bet"),
                "odds_error": last_error or "Current odds hin argamne.",
            })

        converted = convert_event(
            event,
            {
                "key": match["sport_key"],
                "title": match["league"],
            },
        )

        markets = []

        h2h = converted.get("h2h") or {}
        if h2h:
            selections = []
            if h2h.get("home"):
                selections.append({"value": "1", "odd": h2h["home"]})
            if h2h.get("draw"):
                selections.append({"value": "X", "odd": h2h["draw"]})
            if h2h.get("away"):
                selections.append({"value": "2", "odd": h2h["away"]})
            if selections:
                markets.append({
                    "id": "h2h",
                    "name": "🎯 1X2",
                    "selections": selections,
                })

        totals = converted.get("totals") or {}
        if totals:
            selections = []
            if totals.get("over"):
                selections.append({
                    "value": "Over 2.5",
                    "odd": totals["over"],
                })
            if totals.get("under"):
                selections.append({
                    "value": "Under 2.5",
                    "odd": totals["under"],
                })
            if selections:
                markets.append({
                    "id": "totals",
                    "name": "⚽ Over / Under",
                    "selections": selections,
                })

        btts = converted.get("btts") or {}
        if btts:
            selections = []
            if btts.get("yes"):
                selections.append({
                    "value": "BTTS Yes",
                    "odd": btts["yes"],
                })
            if btts.get("no"):
                selections.append({
                    "value": "BTTS No",
                    "odd": btts["no"],
                })
            if selections:
                markets.append({
                    "id": "btts",
                    "name": "🎯 Both Teams To Score",
                    "selections": selections,
                })

        spreads = converted.get("spreads") or []
        if spreads:
            selections = []
            for item in spreads:
                selections.append({
                    "value": f"{item['name']} {item.get('point')}",
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
            "odds_region": used_region,
        })

    except Exception as exc:
        print("[MATCH ERROR]", repr(exc))
        return jsonify({
            "success": False,
            "error": str(exc),
            "markets": [],
        }), 502


@app.route("/api/live", methods=["GET"])
def api_live():
    try:
        result = []

        for sport in select_soccer_sports(soccer_sports())[:10]:
            try:
                scores = odds_request(
                    f"/sports/{sport['key']}/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat": "iso",
                    },
                )
            except Exception as exc:
                print("[LIVE SKIP]", sport.get("key"), repr(exc))
                continue

            for event in scores:
                if event.get("completed"):
                    continue

                score_map = {}
                for score in event.get("scores") or []:
                    score_map[score.get("name")] = score.get("score")

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
            "count": len(result),
            "matches": result,
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "matches": [],
        }), 502


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
# TELEGRAM
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN hin jiru.")

    if not ODDS_API_KEY:
        print("WARNING: ODDS_API_KEY hin jiru.")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("====================================")
    print("BEST BET BOT ONLINE")
    print("WEB APP:", WEB_APP_URL)
    print("ODDS API:", bool(ODDS_API_KEY))
    print("REGIONS:", region_list())
    print("LIST MARKETS:", LIST_MARKETS)
    print("DETAIL MARKETS:", DETAIL_MARKETS)
    print("MAX SPORTS:", MAX_SOCCER_SPORTS)
    print("TIMEOUT:", API_TIMEOUT)
    print("CACHE:", CACHE_SECONDS)
    print("RANGE: TODAY + NEXT 6 DAYS")
    print("====================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
