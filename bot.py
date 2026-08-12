import os
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

API_URL = "https://v3.football.api-sports.io"
web_app = Flask(__name__)

users = {}


# =========================================================
# USER DATA - DEMO ONLY
# =========================================================

def get_user(user_id, first_name="User"):
    if user_id not in users:
        users[user_id] = {
            "name": first_name,
            "balance": 0.0,
            "history": [],
            "betslip": []
        }
    return users[user_id]


# =========================================================
# FOOTBALL API
# =========================================================

def football_request(endpoint, params=None):
    if not FOOTBALL_API_KEY:
        return None, "FOOTBALL_API_KEY hin jiru."

    try:
        r = requests.get(
            f"{API_URL}/{endpoint}",
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            params=params or {},
            timeout=20
        )

        if r.status_code != 200:
            return None, f"API HTTP {r.status_code}"

        data = r.json()

        if data.get("errors"):
            return None, str(data["errors"])

        return data, None

    except requests.RequestException as e:
        return None, str(e)


def format_time(date_string):
    if not date_string:
        return "--:--"
    try:
        dt = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M")
    except Exception:
        return "--:--"


def get_today_matches():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data, error = football_request(
        "fixtures",
        {
            "date": today,
            "timezone": "Africa/Addis_Ababa"
        }
    )

    if error:
        return [], error

    matches = []

    for item in data.get("response", []):
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        home = teams.get("home", {})
        away = teams.get("away", {})

        matches.append({
            "id": fixture.get("id"),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
            "home_logo": home.get("logo", ""),
            "away_logo": away.get("logo", ""),
            "league": league.get("name", "Unknown"),
            "country": league.get("country", ""),
            "time": format_time(fixture.get("date")),
            "status": fixture.get("status", {}).get("short", ""),
            "odds": {},
            "markets": {}
        })

    return matches, None


def empty_odds():
    return {
        "1": "-",
        "X": "-",
        "2": "-",
        "Over 2.5": "-",
        "Under 2.5": "-",
        "BTTS": "-",
        "No BTTS": "-"
    }


def parse_odds(data):
    result = empty_odds()
    response = data.get("response", [])

    if not response:
        return result

    bookmakers = response[0].get("bookmakers", [])
    if not bookmakers:
        return result

    # Find a bookmaker that actually contains useful markets.
    bookmaker = bookmakers[0]
    for b in bookmakers:
        if b.get("bets"):
            bookmaker = b
            break

    for bet in bookmaker.get("bets", []):
        name = str(bet.get("name", "")).lower()
        bet_id = bet.get("id")
        values = bet.get("values", [])

        if "match winner" in name or bet_id == 1:
            for value in values:
                label = str(value.get("value", "")).lower()
                odd = value.get("odd", "-")
                if label in ("home", "1"):
                    result["1"] = odd
                elif label in ("draw", "x"):
                    result["X"] = odd
                elif label in ("away", "2"):
                    result["2"] = odd

        if "over/under" in name or bet_id == 5:
            for value in values:
                label = str(value.get("value", ""))
                odd = value.get("odd", "-")
                low = label.lower()
                if "over 2.5" in low:
                    result["Over 2.5"] = odd
                elif "under 2.5" in low:
                    result["Under 2.5"] = odd

        if "both teams" in name or bet_id == 8:
            for value in values:
                label = str(value.get("value", "")).lower()
                odd = value.get("odd", "-")
                if label == "yes":
                    result["BTTS"] = odd
                elif label == "no":
                    result["No BTTS"] = odd

    return result


def get_match_odds(fixture_id):
    data, error = football_request("odds", {"fixture": fixture_id})
    if error:
        return empty_odds()
    return parse_odds(data)


def get_prediction(fixture_id):
    data, error = football_request("predictions", {"fixture": fixture_id})

    if error:
        return None

    response = data.get("response", [])
    if not response:
        return None

    item = response[0]
    prediction = item.get("predictions", {})
    winner = prediction.get("winner") or {}
    percent = prediction.get("percent") or {}
    goals = prediction.get("goals") or {}

    return {
        "advice": prediction.get("advice", ""),
        "winner": winner.get("name"),
        "home_percent": percent.get("home", "0%"),
        "draw_percent": percent.get("draw", "0%"),
        "away_percent": percent.get("away", "0%"),
        "under_over": prediction.get("under_over"),
        "goals_home": goals.get("home"),
        "goals_away": goals.get("away")
    }


def confidence(prediction):
    if not prediction:
        return 0

    values = []
    for key in ("home_percent", "draw_percent", "away_percent"):
        try:
            values.append(float(str(prediction.get(key, "0%")).replace("%", "")))
        except Exception:
            pass

    return int(max(values)) if values else 0


def enrich_matches(matches, limit=15):
    for match in matches[:limit]:
        match["odds"] = get_match_odds(match["id"])
    return matches


def get_best_bet():
    matches, error = get_today_matches()

    if error:
        return None, error

    if not matches:
        return None, "Har'a match hin argamne."

    # Check several matches, then choose the highest confidence.
    candidates = []

    for match in matches[:15]:
        prediction = get_prediction(match["id"])
        if not prediction:
            continue

        conf = confidence(prediction)
        if conf <= 0:
            continue

        match["prediction"] = prediction
        match["confidence"] = conf
        match["odds"] = get_match_odds(match["id"])
        match["bet"] = prediction.get("winner") or prediction.get("advice") or "Analysis"
        candidates.append(match)

    if not candidates:
        return None, "Prediction har'aaf hin argamne."

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[0], None


# =========================================================
# TELEGRAM MENUS
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 BEST BET", callback_data="best_bet")],
        [
            InlineKeyboardButton("⚽ FOOTBALL", callback_data="football"),
            InlineKeyboardButton("⚡ KENO FAST", callback_data="keno")
        ],
        [InlineKeyboardButton("🎟️ BET SLIP", callback_data="betslip")],
        [
            InlineKeyboardButton("👤 PROFILE", callback_data="profile"),
            InlineKeyboardButton("💳 BALANCE", callback_data="balance")
        ],
        [
            InlineKeyboardButton("📜 HISTORY", callback_data="history"),
            InlineKeyboardButton("🏆 WINNERS", callback_data="winners")
        ],
        [InlineKeyboardButton("ℹ️ HOW TO PLAY", callback_data="how")],
        [InlineKeyboardButton("📞 SUPPORT", callback_data="support")]
    ])


def football_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 TODAY MATCHES", callback_data="matches")],
        [InlineKeyboardButton("🎯 PREDICTIONS", callback_data="prediction")],
        [InlineKeyboardButton("🔴 LIVE", callback_data="live")],
        [InlineKeyboardButton("🏆 LEAGUES", callback_data="leagues")],
        [InlineKeyboardButton("🎟️ BET SLIP", callback_data="betslip")],
        [InlineKeyboardButton("🌐 OPEN WEBSITE", callback_data="website")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="home")]
    ])


def betslip_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ CLEAR", callback_data="clear"),
            InlineKeyboardButton("💰 DEMO BET", callback_data="place")
        ],
        [InlineKeyboardButton("⚽ FOOTBALL", callback_data="football")],
        [InlineKeyboardButton("⬅️ HOME", callback_data="home")]
    ])


def keno_menu():
    rows = []
    for start in range(1, 81, 10):
        rows.append([
            InlineKeyboardButton(str(n), callback_data=f"keno_{n}")
            for n in range(start, start + 10)
        ])
    rows.append([InlineKeyboardButton("🎲 RANDOM DRAW", callback_data="keno_draw")])
    rows.append([InlineKeyboardButton("⬅️ HOME", callback_data="home")])
    return InlineKeyboardMarkup(rows)


# =========================================================
# BET SLIP
# =========================================================

def add_bet(user_id, match, market, selection, odd):
    user = get_user(user_id)

    try:
        odd = float(str(odd).replace(",", "."))
    except Exception:
        return False

    if odd <= 1:
        return False

    user["betslip"] = [
        x for x in user["betslip"]
        if not (str(x["fixture_id"]) == str(match["id"]) and x["market"] == market)
    ]

    user["betslip"].append({
        "fixture_id": match["id"],
        "home": match["home"],
        "away": match["away"],
        "market": market,
        "selection": selection,
        "odd": odd
    })
    return True


def betslip_text(user_id):
    user = get_user(user_id)
    slips = user["betslip"]

    if not slips:
        return (
            "🎟️ *BET SLIP*\n\n"
            "Bet hin qabdu.\n\n"
            "⚽ Match keessaa odds filadhu."
        )

    total = 1.0
    text = "🎟️ *BET SLIP*\n\n"

    for i, item in enumerate(slips, 1):
        total *= item["odd"]
        text += (
            f"*{i}.* {item['home']} vs {item['away']}\n"
            f"🎯 {item['market']}: *{item['selection']}*\n"
            f"📊 Odd: *{item['odd']:.2f}*\n\n"
        )

    text += f"━━━━━━━━━━━━━━\n📈 *Total Odds:* {total:.2f}\n"
    text += "\n⚠️ Demo/testing qofa."
    return text


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.first_name or "User")

    await update.message.reply_text(
        f"👋 Baga nagaan dhuftan *{user.first_name}*!\n\n"
        "🎯 *BEST BET*\n\n"
        "⚽ Football odds\n"
        "📊 Predictions\n"
        "🎟️ Bet Slip\n"
        "🔴 Live football\n"
        "🌐 Website\n\n"
        "👇 Menu keessaa filadhu.",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    get_user(user.id, user.first_name or "User")
    data = query.data

    if data == "home":
        await query.edit_message_text(
            "🏠 *BEST BET*\n\nMenu keessaa filadhu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "website":
        await query.edit_message_text(
            "🌐 *BEST BET WEBSITE*\n\n"
            "Website Render URL kee browser keessatti bani.\n\n"
            "Fakkeenya:\n"
            "`https://best-bet-xxxx.onrender.com`\n\n"
            "⚠️ `xxxx` bakka URL Render kee ti.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "football":
        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\nFootball keessaa filadhu.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "matches":
        matches, error = get_today_matches()

        if error:
            await query.edit_message_text(
                f"⚽ *TODAY MATCHES*\n\n❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        if not matches:
            await query.edit_message_text(
                "⚽ *TODAY MATCHES*\n\nMatch har'aa hin argamne.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        matches = enrich_matches(matches, 12)
        text = "📅 *TODAY'S MATCHES*\n\n"
        buttons = []

        for m in matches[:10]:
            o = m["odds"]
            text += (
                f"⚽ *{m['home']}* vs *{m['away']}*\n"
                f"🏆 {m['league']}\n"
                f"🕐 {m['time']}\n"
                f"1️⃣ {o['1']}   ❌ {o['X']}   2️⃣ {o['2']}\n\n"
            )
            buttons.append([
                InlineKeyboardButton(
                    f"⚽ {m['home']} vs {m['away']}"[:60],
                    callback_data=f"match|{m['id']}"
                )
            ])

        buttons += [
            [InlineKeyboardButton("🎟️ BET SLIP", callback_data="betslip")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="football")]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )

    elif data.startswith("match|"):
        fixture_id = data.split("|", 1)[1]
        matches, error = get_today_matches()

        if error:
            await query.edit_message_text(
                f"❌ {error}",
                reply_markup=football_menu()
            )
            return

        selected = next((m for m in matches if str(m["id"]) == fixture_id), None)

        if not selected:
            await query.answer("Match hin argamne.", show_alert=True)
            return

        odds = get_match_odds(fixture_id)
        pred = get_prediction(fixture_id)
        conf = confidence(pred)

        if pred:
            pred_text = (
                f"🎯 Prediction: *{pred.get('winner') or pred.get('advice') or 'Analysis'}*\n"
                f"📊 Confidence: *{conf}%*\n"
                f"🏠 Home: {pred.get('home_percent', '0%')}\n"
                f"🤝 Draw: {pred.get('draw_percent', '0%')}\n"
                f"✈️ Away: {pred.get('away_percent', '0%')}\n"
            )
        else:
            pred_text = "🎯 Prediction: *Unavailable*\n"

        text = (
            "⚽ *MATCH DETAILS*\n\n"
            f"*{selected['home']} vs {selected['away']}*\n"
            f"🏆 {selected['league']}\n"
            f"🌍 {selected['country']}\n"
            f"🕐 {selected['time']}\n\n"
            "💰 *1X2 ODDS*\n"
            f"🏠 1: *{odds['1']}*\n"
            f"🤝 X: *{odds['X']}*\n"
            f"✈️ 2: *{odds['2']}*\n\n"
            "📊 *MARKETS*\n"
            f"⬆️ Over 2.5: *{odds['Over 2.5']}*\n"
            f"⬇️ Under 2.5: *{odds['Under 2.5']}*\n"
            f"⚽ BTTS Yes: *{odds['BTTS']}*\n\n"
            f"{pred_text}\n👇 Odds filadhu."
        )

        keyboard = [
            [
                InlineKeyboardButton(f"1️⃣ {odds['1']}", callback_data=f"bet|{fixture_id}|1"),
                InlineKeyboardButton(f"❌ {odds['X']}", callback_data=f"bet|{fixture_id}|X"),
                InlineKeyboardButton(f"2️⃣ {odds['2']}", callback_data=f"bet|{fixture_id}|2")
            ],
            [InlineKeyboardButton(f"⬆️ O2.5 {odds['Over 2.5']}", callback_data=f"bet|{fixture_id}|O25")],
            [InlineKeyboardButton(f"⬇️ U2.5 {odds['Under 2.5']}", callback_data=f"bet|{fixture_id}|U25")],
            [InlineKeyboardButton(f"⚽ BTTS {odds['BTTS']}", callback_data=f"bet|{fixture_id}|BTTS")],
            [InlineKeyboardButton("🎟️ BET SLIP", callback_data="betslip")],
            [InlineKeyboardButton("⬅️ MATCHES", callback_data="matches")]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("bet|"):
        _, fixture_id, code = data.split("|")
        matches, error = get_today_matches()

        if error:
            await query.answer("API error.", show_alert=True)
            return

        selected = next((m for m in matches if str(m["id"]) == fixture_id), None)
        if not selected:
            await query.answer("Match hin argamne.", show_alert=True)
            return

        odds = get_match_odds(fixture_id)

        mapping = {
            "1": ("1X2", selected["home"], odds["1"]),
            "X": ("1X2", "Draw", odds["X"]),
            "2": ("1X2", selected["away"], odds["2"]),
            "O25": ("Over/Under 2.5", "Over 2.5", odds["Over 2.5"]),
            "U25": ("Over/Under 2.5", "Under 2.5", odds["Under 2.5"]),
            "BTTS": ("BTTS", "Yes", odds["BTTS"])
        }

        if code not in mapping or not add_bet(user.id, selected, *mapping[code]):
            await query.answer("Odd yeroo ammaa hin jiru.", show_alert=True)
            return

        await query.edit_message_text(
            "✅ *Bet slip keessa gale!*\n\n" + betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "betslip":
        await query.edit_message_text(
            betslip_text(user.id),
            reply_markup=betslip_keyboard(),
            parse_mode="Markdown"
        )

    elif data == "clear":
        get_user(user.id)["betslip"] = []
        await query.edit_message_text(
            "🗑️ Bet slip qulqullaa'e.",
            reply_markup=football_menu()
        )

    elif data == "place":
        u = get_user(user.id)
        slips = u["betslip"]

        if not slips:
            await query.answer("Bet slip duwwaa dha.", show_alert=True)
            return

        # Demo only; no real-money deposit/payment.
        stake = 10.0
        total = 1.0

        for item in slips:
            total *= item["odd"]

        if u["balance"] < stake:
            await query.edit_message_text(
                "💳 *BALANCE XIQQAA*\n\n"
                f"Balance: *{u['balance']:.2f}*\n"
                f"Demo stake: *{stake:.2f}*\n\n"
                "⚠️ Real-money deposit/payment hin dabalamin.",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
            return

        potential = stake * total
        u["balance"] -= stake
        u["history"].append({
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "stake": stake,
            "odds": total,
            "potential": potential,
            "status": "OPEN"
        })
        u["betslip"] = []

        await query.edit_message_text(
            "✅ *DEMO BET PLACED*\n\n"
            f"💰 Stake: *{stake:.2f}*\n"
            f"📈 Total Odds: *{total:.2f}*\n"
            f"🏆 Potential: *{potential:.2f}*\n"
            f"💳 Balance: *{u['balance']:.2f}*\n\n"
            "⚠️ Demo/testing qofa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "best_bet" or data == "prediction":
        match, error = get_best_bet()

        if error:
            await query.edit_message_text(
                f"🎯 *PREDICTION*\n\n❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        p = match["prediction"]
        text = (
            "🎯 *BEST BET / PREDICTION*\n\n"
            f"⚽ *{match['home']} vs {match['away']}*\n"
            f"🏆 {match['league']}\n"
            f"🕐 {match['time']}\n\n"
            f"🔥 Prediction: *{match['bet']}*\n"
            f"📊 Confidence: *{match['confidence']}%*\n\n"
            f"🏠 Home: {p['home_percent']}\n"
            f"🤝 Draw: {p['draw_percent']}\n"
            f"✈️ Away: {p['away_percent']}\n\n"
            "⚠️ Prediction bu'aa mirkanaa'aa miti."
        )

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "live":
        live, error = football_request("fixtures", {"live": "all"})

        if error:
            await query.edit_message_text(
                f"🔴 *LIVE*\n\n❌ {error}",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        items = live.get("response", [])
        if not items:
            await query.edit_message_text(
                "🔴 *LIVE*\n\nAmmaaf live match hin jiru.",
                reply_markup=football_menu(),
                parse_mode="Markdown"
            )
            return

        text = "🔴 *LIVE MATCHES*\n\n"
        for item in items[:10]:
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            status = item.get("fixture", {}).get("status", {})
            h = teams.get("home", {}).get("name", "Home")
            a = teams.get("away", {}).get("name", "Away")
            hg = goals.get("home") or 0
            ag = goals.get("away") or 0
            minute = status.get("elapsed") or "-"
            text += f"⚽ *{h}* {hg} - {ag} *{a}*  ⏱️ {minute}'\n\n"

        await query.edit_message_text(
            text,
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "leagues":
        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "🏴 Premier League\n"
            "🇪🇸 La Liga\n"
            "🇮🇹 Serie A\n"
            "🇩🇪 Bundesliga\n"
            "🇫🇷 Ligue 1\n"
            "🏆 Champions League",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )

    elif data == "keno":
        await query.edit_message_text(
            "⚡ *KENO FAST*\n\nLakkoofsa 1 hanga 80 keessaa filadhu.\n🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif data.startswith("keno_") and data != "keno_draw":
        number = data.split("_", 1)[1]
        await query.answer(f"Lakkoofsa {number} filatte.", show_alert=True)

    elif data == "keno_draw":
        import random
        nums = sorted(random.sample(range(1, 81), 10))
        await query.edit_message_text(
            "🎲 *KENO FAST DRAW*\n\n"
            + " • ".join(map(str, nums))
            + "\n\n🧪 Demo qofa.",
            reply_markup=keno_menu(),
            parse_mode="Markdown"
        )

    elif data == "profile":
        u = get_user(user.id)
        await query.edit_message_text(
            f"👤 *PROFILE*\n\n"
            f"Name: *{u['name']}*\n"
            f"Balance: *{u['balance']:.2f}*\n"
            f"Open selections: *{len(u['betslip'])}*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "balance":
        u = get_user(user.id)
        await query.edit_message_text(
            f"💳 *BALANCE*\n\nBalance: *{u['balance']:.2f}*\n\n"
            "⚠️ Real-money deposit/payment hin dabalamin.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "history":
        u = get_user(user.id)
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
        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "winners":
        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo system keessatti winners list yeroo ammaa hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "how":
        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1. ⚽ Football seeni\n"
            "2. 📅 Today's Matches bani\n"
            "3. Match filadhu\n"
            "4. Odd filadhu\n"
            "5. 🎟️ Bet Slip ilaali\n"
            "6. Demo bet qofa.\n\n"
            "⚠️ Real-money betting/payment hin dabalamin.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

    elif data == "support":
        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Bot owner/contact kee asitti dabali.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


# =========================================================
# WEBSITE - ALL HTML IS INSIDE THIS ONE FILE
# =========================================================

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BEST BET - Football</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#0d1724;color:#fff}
header{background:#152536;padding:16px;text-align:center;position:sticky;top:0;z-index:5}
.logo{font-size:28px;font-weight:900;color:#ffd400}
.sub{font-size:13px;color:#9fb0c2;margin-top:5px}
nav{display:flex;gap:8px;overflow:auto;padding:12px;background:#111f2e}
button{border:0;border-radius:12px;padding:12px 16px;background:#233547;color:#fff;font-weight:700}
button.active{background:#ffd400;color:#111}
main{padding:14px;max-width:900px;margin:auto}
.panel{background:#152536;border-radius:18px;padding:16px;margin-bottom:14px}
h2{margin:0 0 12px}
.status{font-size:13px;color:#9fb0c2}
.match{background:#1b2c3d;border-radius:16px;padding:15px;margin:12px 0}
.teams{font-size:18px;font-weight:800;margin-bottom:7px}
.meta{font-size:12px;color:#9fb0c2;margin-bottom:12px}
.odds{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.odd{background:#26394b;padding:12px;border-radius:12px;text-align:center;cursor:pointer}
.odd b{display:block;font-size:18px;margin-top:4px}
.markets{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}
.market{background:#203448;padding:8px 10px;border-radius:10px;font-size:12px}
.green{color:#83d13b}.yellow{color:#ffd400}
#error{color:#ff7b7b}
.loader{text-align:center;padding:25px}
</style>
</head>
<body>
<header>
  <div class="logo">🎯 BEST BET</div>
  <div class="sub">Football odds • predictions • bet slip</div>
</header>

<nav>
  <button class="active" onclick="showTab('today',this)">📅 Today</button>
  <button onclick="showTab('best',this)">🎯 Best Bet</button>
  <button onclick="showTab('live',this)">🔴 Live</button>
  <button onclick="showTab('slip',this)">🎟️ Bet Slip</button>
</nav>

<main>
  <section id="content" class="panel">
    <div class="loader">Loading football...</div>
  </section>
</main>

<script>
let matches=[];
let slip=[];

function esc(x){
  return String(x ?? '').replace(/[&<>"']/g,m=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[m]));
}

async function api(path){
  const r=await fetch(path);
  const d=await r.json();
  if(!r.ok) throw new Error(d.error || 'Server error');
  return d;
}

function showTab(tab,btn){
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
  if(btn) btn.classList.add('active');

  if(tab==='today') loadToday();
  if(tab==='best') loadBest();
  if(tab==='live') loadLive();
  if(tab==='slip') renderSlip();
}

async function loadToday(){
  const c=document.getElementById('content');
  c.innerHTML='<div class="loader">Loading matches...</div>';
  try{
    const d=await api('/api/matches');
    matches=d.matches||[];
    if(!matches.length){
      c.innerHTML='<h2>📅 Today Matches</h2><div class="status">No matches found.</div>';
      return;
    }
    c.innerHTML='<h2>📅 Today Matches</h2><div class="status">Addis Ababa time • Real API data</div>'+
      matches.map(renderMatch).join('');
  }catch(e){
    c.innerHTML='<h2>⚠️ Error</h2><div id="error">'+esc(e.message)+'</div>'+
      '<p class="status">Check FOOTBALL_API_KEY on Render.</p>';
  }
}

function renderMatch(m){
  const o=m.odds||{};
  return `<div class="match">
    <div class="teams">${esc(m.home)} <span class="status">vs</span> ${esc(m.away)}</div>
    <div class="meta">🏆 ${esc(m.league)} • 🕐 ${esc(m.time)}</div>
    <div class="odds">
      <div class="odd" onclick="addBet(${m.id},'1','${esc(m.home)}',${JSON.stringify(o['1']||'-')})">1<b>${esc(o['1']||'-')}</b></div>
      <div class="odd" onclick="addBet(${m.id},'X','Draw',${JSON.stringify(o['X']||'-')})">X<b>${esc(o['X']||'-')}</b></div>
      <div class="odd" onclick="addBet(${m.id},'2','${esc(m.away)}',${JSON.stringify(o['2']||'-')})">2<b>${esc(o['2']||'-')}</b></div>
    </div>
    <div class="markets">
      <div class="market">O2.5: <b>${esc(o['Over 2.5']||'-')}</b></div>
      <div class="market">U2.5: <b>${esc(o['Under 2.5']||'-')}</b></div>
      <div class="market">BTTS: <b>${esc(o['BTTS']||'-')}</b></div>
    </div>
  </div>`;
}

async function loadBest(){
  const c=document.getElementById('content');
  c.innerHTML='<div class="loader">Analysing...</div>';
  try{
    const d=await api('/api/best');
    const m=d.match;
    if(!m){c.innerHTML='<h2>🎯 Best Bet</h2><div id="error">'+esc(d.error||'No prediction')+'</div>';return;}
    const p=m.prediction||{};
    c.innerHTML=`<h2>🎯 Best Bet</h2>
      <div class="match">
        <div class="teams">${esc(m.home)} vs ${esc(m.away)}</div>
        <div class="meta">🏆 ${esc(m.league)} • 🕐 ${esc(m.time)}</div>
        <p class="yellow"><b>Prediction: ${esc(m.bet)}</b></p>
        <p>📊 Confidence: <b>${esc(m.confidence)}%</b></p>
        <p>🏠 Home: ${esc(p.home_percent)}<br>🤝 Draw: ${esc(p.draw_percent)}<br>✈️ Away: ${esc(p.away_percent)}</p>
      </div>
      <div class="status">⚠️ Prediction is analysis, not a guaranteed result.</div>`;
  }catch(e){
    c.innerHTML='<h2>🎯 Best Bet</h2><div id="error">'+esc(e.message)+'</div>';
  }
}

async function loadLive(){
  const c=document.getElementById('content');
  c.innerHTML='<div class="loader">Loading live...</div>';
  try{
    const d=await api('/api/live');
    const list=d.matches||[];
    c.innerHTML='<h2>🔴 Live</h2>'+
      (list.length?list.map(x=>`<div class="match"><div class="teams">${esc(x.home)} ${x.home_goals??0} - ${x.away_goals??0} ${esc(x.away)}</div><div class="meta">⏱️ ${esc(x.elapsed||'-')}'</div></div>`).join(''):'<div class="status">No live matches now.</div>');
  }catch(e){
    c.innerHTML='<h2>🔴 Live</h2><div id="error">'+esc(e.message)+'</div>';
  }
}

function addBet(id,selection,label,odd){
  if(!odd || odd==='-' || Number(odd)<=1){
    alert('Odd is not available.');
    return;
  }
  slip=slip.filter(x=>!(x.id===id && x.market==='1X2'));
  slip.push({id,market:'1X2',selection,label,odd:Number(odd)});
  alert('✅ Added to Bet Slip');
}

function renderSlip(){
  const c=document.getElementById('content');
  if(!slip.length){
    c.innerHTML='<h2>🎟️ Bet Slip</h2><div class="status">Your bet slip is empty.</div>';
    return;
  }
  let total=1;
  let html='<h2>🎟️ Bet Slip</h2>';
  slip.forEach((x,i)=>{
    total*=x.odd;
    html+=`<div class="match"><b>${i+1}. ${esc(x.label)}</b><div class="meta">Selection: ${esc(x.selection)} • Odd: ${x.odd.toFixed(2)}</div></div>`;
  });
  html+=`<div class="panel"><b>Total Odds: ${total.toFixed(2)}</b></div>`;
  html+='<button onclick="slip=[];renderSlip()">🗑️ Clear</button>';
  c.innerHTML=html;
}

loadToday();
</script>
</body>
</html>
"""


# =========================================================
# FLASK ROUTES
# =========================================================

@web_app.route("/")
def index():
    return render_template_string(HTML)


@web_app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "Best Bet",
        "football_api_key": bool(FOOTBALL_API_KEY)
    })


@web_app.route("/api/matches")
def api_matches():
    matches, error = get_today_matches()
    if error:
        return jsonify({"error": error}), 500

    matches = enrich_matches(matches, 15)
    return jsonify({"matches": matches})


@web_app.route("/api/best")
def api_best():
    match, error = get_best_bet()
    if error:
        return jsonify({"error": error}), 404
    return jsonify({"match": match})


@web_app.route("/api/live")
def api_live():
    data, error = football_request("fixtures", {"live": "all"})

    if error:
        return jsonify({"error": error}), 500

    result = []

    for item in data.get("response", []):
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        status = item.get("fixture", {}).get("status", {})

        result.append({
            "home": teams.get("home", {}).get("name", "Home"),
            "away": teams.get("away", {}).get("name", "Away"),
            "home_goals": goals.get("home"),
            "away_goals": goals.get("away"),
            "elapsed": status.get("elapsed")
        })

    return jsonify({"matches": result})


def run_web():
    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN hin jiru. Render Environment Variables keessatti galchi.")

    # Flask website starts in background.
    threading.Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("BEST BET BOT + WEBSITE started")
    print(f"PORT={PORT}")

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
