import os
import json
import logging
from datetime import datetime, timezone
from threading import Thread

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

# Europe bookmakers
REGIONS = os.getenv("ODDS_REGIONS", "eu").strip()

# Main markets
MARKETS = "h2h,totals,spreads"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("bestbet")

app = Flask(__name__)


# =========================================================
# WEB APP HTML
# =========================================================

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta
    name="viewport"
    content="width=device-width,
             initial-scale=1.0,
             maximum-scale=1.0,
             user-scalable=no"
>

<title>Best Bet</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0d1b2a;
    color: #ffffff;
    font-family: Arial, Helvetica, sans-serif;
}

.header {
    text-align: center;
    padding: 30px 15px 25px;
    background: #14273a;
}

.logo {
    font-size: 38px;
    font-weight: 900;
    color: #ffd400;
}

.subtitle {
    color: #aab8c5;
    font-size: 17px;
    margin-top: 8px;
}

.tabs {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    padding: 16px;
    background: #0d1b2a;
    position: sticky;
    top: 0;
    z-index: 10;
}

.tab {
    border: 0;
    border-radius: 18px;
    padding: 15px 5px;
    background: #23384c;
    color: white;
    font-size: 15px;
    font-weight: 700;
}

.tab.active {
    background: #ffd400;
    color: #111;
}

.container {
    padding: 10px 16px 40px;
}

.section {
    background: #14273a;
    border-radius: 25px;
    padding: 20px;
    margin-bottom: 18px;
}

.section-title {
    font-size: 28px;
    font-weight: 900;
    margin-bottom: 8px;
}

.section-subtitle {
    color: #aab8c5;
    margin-bottom: 18px;
}

.match {
    background: #1a3045;
    border-radius: 22px;
    padding: 18px;
    margin-bottom: 16px;
}

.teams {
    font-size: 20px;
    font-weight: 800;
    line-height: 1.25;
}

.meta {
    color: #aab8c5;
    margin-top: 8px;
    font-size: 14px;
}

.odds-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-top: 16px;
}

.odd {
    background: #243c52;
    border-radius: 16px;
    padding: 15px 5px;
    text-align: center;
    font-size: 16px;
    font-weight: 800;
    border: 1px solid #314b62;
}

.odd span {
    display: block;
    font-size: 21px;
    margin-top: 5px;
}

.market-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
}

.market {
    background: #21394e;
    border-radius: 13px;
    padding: 9px 13px;
    color: #fff;
    font-size: 14px;
    font-weight: 700;
}

.market button {
    background: none;
    border: none;
    color: inherit;
    font-weight: inherit;
}

.actions {
    display: flex;
    gap: 9px;
    margin-top: 15px;
}

.action {
    flex: 1;
    border: none;
    border-radius: 14px;
    padding: 13px 7px;
    background: #ffd400;
    color: #111;
    font-weight: 900;
}

.action.secondary {
    background: #263e53;
    color: #fff;
}

.loading {
    text-align: center;
    padding: 40px 10px;
    color: #b8c4ce;
}

.error {
    background: #52272a;
    color: #ffd9d9;
    padding: 15px;
    border-radius: 15px;
}

.empty {
    text-align: center;
    color: #aab8c5;
    padding: 30px 10px;
}

.slip-item {
    background: #1c3449;
    padding: 15px;
    border-radius: 15px;
    margin-bottom: 10px;
}

.slip-item strong {
    display: block;
    margin-bottom: 6px;
}

.big-button {
    width: 100%;
    border: 0;
    border-radius: 16px;
    padding: 16px;
    background: #ffd400;
    color: #111;
    font-size: 17px;
    font-weight: 900;
    margin-top: 10px;
}

.back {
    background: #263e53;
    color: white;
}

.league {
    width: 100%;
    background: #1b3348;
    border: none;
    color: white;
    padding: 17px;
    border-radius: 16px;
    text-align: left;
    margin-bottom: 10px;
    font-size: 16px;
    font-weight: 800;
}

.bet-selected {
    border: 2px solid #ffd400;
}

.small {
    font-size: 12px;
    color: #91a4b4;
}

@media (max-width: 380px) {
    .tabs {
        gap: 6px;
        padding: 10px;
    }

    .tab {
        font-size: 12px;
        padding: 12px 2px;
    }

    .logo {
        font-size: 31px;
    }

    .section-title {
        font-size: 24px;
    }
}
</style>
</head>

<body>

<div class="header">
    <div class="logo">🎯 BEST BET</div>
    <div class="subtitle">
        Football odds • predictions • bet slip
    </div>
</div>

<div class="tabs">

    <button
        class="tab active"
        id="todayTab"
        onclick="showToday()">
        📅<br>Today
    </button>

    <button
        class="tab"
        onclick="showBestBet()">
        🎯<br>Best Bet
    </button>

    <button
        class="tab"
        onclick="showLive()">
        🔴<br>Live
    </button>

    <button
        class="tab"
        onclick="showSlip()">
        🎟️<br>Bet Slip
    </button>

</div>

<div class="container" id="content">

    <div class="loading">
        ⏳ Loading football matches...
    </div>

</div>

<script>

const content = document.getElementById("content");

let matches = [];
let slip = JSON.parse(
    localStorage.getItem("bestbet_slip") || "[]"
);

let currentTab = "today";


// ========================================================
// HELPERS
// ========================================================

function escapeHtml(value) {

    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function money(value) {

    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "-";
    }

    return Number(value).toFixed(2);
}


function saveSlip() {

    localStorage.setItem(
        "bestbet_slip",
        JSON.stringify(slip)
    );
}


function setActive(index) {

    document
        .querySelectorAll(".tab")
        .forEach((x, i) => {
            x.classList.toggle(
                "active",
                i === index
            );
        });
}


// ========================================================
// LOAD MATCHES
// ========================================================

async function loadMatches() {

    content.innerHTML = `
        <div class="loading">
            ⏳ Loading Today Matches...
        </div>
    `;

    try {

        const response = await fetch("/api/matches");

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "API error"
            );
        }

        matches = data.matches || [];

        renderToday();

    } catch (error) {

        content.innerHTML = `
            <div class="section">
                <div class="section-title">
                    ⚠️ Error
                </div>

                <div class="error">
                    ${escapeHtml(error.message)}
                </div>
            </div>
        `;
    }
}


// ========================================================
// MATCH CARD
// ========================================================

function matchCard(match, index) {

    const home = escapeHtml(
        match.home_team
    );

    const away = escapeHtml(
        match.away_team
    );

    const league = escapeHtml(
        match.league || "Football"
    );

    const time = escapeHtml(
        match.time || ""
    );

    const h2h = match.h2h || {};

    const homeOdd = h2h.home;
    const drawOdd = h2h.draw;
    const awayOdd = h2h.away;

    const totals = match.totals || {};

    const over = totals.over;
    const under = totals.under;

    const btts = match.btts || {};

    return `
        <div class="match">

            <div class="teams">
                ${home}
                <span style="color:#8da1b1;">vs</span>
                ${away}
            </div>

            <div class="meta">
                🏆 ${league}
                ${time ? " • 🕐 " + time : ""}
            </div>

            <div class="odds-grid">

                <div class="odd">
                    1
                    <span>${money(homeOdd)}</span>
                </div>

                <div class="odd">
                    X
                    <span>${money(drawOdd)}</span>
                </div>

                <div class="odd">
                    2
                    <span>${money(awayOdd)}</span>
                </div>

            </div>

            <div class="market-row">

                <div class="market">
                    O2.5:
                    ${money(over)}
                </div>

                <div class="market">
                    U2.5:
                    ${money(under)}
                </div>

                <div class="market">
                    BTTS:
                    ${money(btts.yes)}
                </div>

            </div>

            <div class="actions">

                <button
                    class="action"
                    onclick="openMatch(${index})">
                    📊 Markets
                </button>

                <button
                    class="action secondary"
                    onclick="addMainBet(${index})">
                    🎟️ Add
                </button>

            </div>

        </div>
    `;
}


// ========================================================
// TODAY
// ========================================================

function showToday() {

    currentTab = "today";

    setActive(0);

    renderToday();
}


function renderToday() {

    if (!matches.length) {

        content.innerHTML = `
            <div class="section">
                <div class="section-title">
                    📅 Today Matches
                </div>

                <div class="empty">
                    ⚠️ No football matches found.
                </div>
            </div>
        `;

        return;
    }

    let html = `
        <div class="section">

            <div class="section-title">
                📅 Today Matches
            </div>

            <div class="section-subtitle">
                Football • Odds API
            </div>

    `;

    matches.forEach((match, index) => {

        html += matchCard(
            match,
            index
        );

    });

    html += `</div>`;

    content.innerHTML = html;
}


// ========================================================
// MATCH DETAILS
// ========================================================

function openMatch(index) {

    const match = matches[index];

    const h2h = match.h2h || {};
    const totals = match.totals || {};
    const spreads = match.spreads || {};
    const btts = match.btts || {};

    content.innerHTML = `

        <div class="section">

            <div class="section-title">
                ⚽ Match Markets
            </div>

            <div class="teams">
                ${escapeHtml(match.home_team)}
                <span style="color:#8da1b1;">vs</span>
                ${escapeHtml(match.away_team)}
            </div>

            <div class="meta">
                🏆 ${escapeHtml(match.league || "")}
            </div>

        </div>


        <div class="section">

            <div class="section-title">
                🎯 1X2
            </div>

            ${marketButton(
                "1",
                h2h.home,
                match,
                "1X2"
            )}

            ${marketButton(
                "X",
                h2h.draw,
                match,
                "1X2"
            )}

            ${marketButton(
                "2",
                h2h.away,
                match,
                "1X2"
            )}

        </div>


        <div class="section">

            <div class="section-title">
                ⚽ Over / Under
            </div>

            ${marketButton(
                "Over 2.5",
                totals.over,
                match,
                "Over/Under"
            )}

            ${marketButton(
                "Under 2.5",
                totals.under,
                match,
                "Over/Under"
            )}

        </div>


        <div class="section">

            <div class="section-title">
                🔥 BTTS
            </div>

            ${marketButton(
                "BTTS Yes",
                btts.yes,
                match,
                "BTTS"
            )}

            ${marketButton(
                "BTTS No",
                btts.no,
                match,
                "BTTS"
            )}

        </div>


        <div class="section">

            <div class="section-title">
                📊 Handicap
            </div>

            ${
                renderSpreadOptions(
                    spreads,
                    match
                )
            }

        </div>


        <button
            class="big-button back"
            onclick="renderToday()">
            ⬅️ Back to Matches
        </button>

    `;
}


function marketButton(
    selection,
    odd,
    match,
    market
) {

    if (
        odd === null ||
        odd === undefined
    ) {
        odd = "-";
    }

    return `
        <button
            class="league"
            onclick='addSelection(
                ${JSON.stringify({
                    home_team: match.home_team,
                    away_team: match.away_team,
                    league: match.league,
                    market: market,
                    selection: selection,
                    odds: odd
                })}
            )'>

            ${escapeHtml(selection)}

            <span
                style="
                    float:right;
                    color:#ffd400;
                ">
                ${money(odd)}
            </span>

        </button>
    `;
}


function renderSpreadOptions(
    spreads,
    match
) {

    if (
        !spreads ||
        !spreads.options ||
        !spreads.options.length
    ) {

        return `
            <div class="empty">
                Handicap odds not available.
            </div>
        `;
    }

    return spreads.options.map(
        option => {

            return marketButton(
                option.name,
                option.price,
                match,
                "Handicap"
            );

        }
    ).join("");
}


// ========================================================
// ADD SELECTION
// ========================================================

function addSelection(selection) {

    slip.push(selection);

    saveSlip();

    alert(
        "✅ Added to Bet Slip"
    );
}


function addMainBet(index) {

    const match = matches[index];

    let selection = null;

    if (
        match.h2h &&
        match.h2h.home
    ) {

        selection = {
            home_team: match.home_team,
            away_team: match.away_team,
            league: match.league,
            market: "1X2",
            selection: "Home",
            odds: match.h2h.home
        };
    }

    if (selection) {

        slip.push(selection);

        saveSlip();

        alert(
            "🎟️ Home selection added"
        );
    }
}


// ========================================================
// BEST BET
// ========================================================

function showBestBet() {

    currentTab = "best";

    setActive(1);

    if (!matches.length) {

        content.innerHTML = `
            <div class="section">
                <div class="section-title">
                    🎯 Best Bet
                </div>

                <div class="empty">
                    No matches available.
                </div>
            </div>
        `;

        return;
    }

    let candidates = [];

    matches.forEach(match => {

        if (
            match.best_bet &&
            match.best_bet.odds
        ) {

            candidates.push(match);

        }

    });

    candidates.sort(
        (a, b) =>
            Number(a.best_bet.odds) -
            Number(b.best_bet.odds)
    );

    candidates = candidates.slice(0, 10);

    let html = `
        <div class="section">

            <div class="section-title">
                🎯 Best Bet
            </div>

            <div class="section-subtitle">
                Strong available selections
            </div>
    `;

    candidates.forEach(
        (match, index) => {

            const pick =
                match.best_bet;

            html += `
                <div class="match">

                    <div class="teams">
                        ${escapeHtml(match.home_team)}
                        <span style="color:#8da1b1;">
                            vs
                        </span>
                        ${escapeHtml(match.away_team)}
                    </div>

                    <div class="meta">
                        ${escapeHtml(match.league || "")}
                    </div>

                    <div
                        class="market"
                        style="
                            margin-top:15px;
                            font-size:16px;
                        ">

                        🎯 ${escapeHtml(
                            pick.selection
                        )}

                        <span
                            style="
                                float:right;
                                color:#ffd400;
                            ">

                            ${money(pick.odds)}

                        </span>

                    </div>

                    <button
                        class="big-button"
                        onclick='addSelection(
                            ${JSON.stringify({
                                home_team: match.home_team,
                                away_team: match.away_team,
                                league: match.league,
                                market: pick.market,
                                selection: pick.selection,
                                odds: pick.odds
                            })}
                        )'>

                        🎟️ Add Best Bet

                    </button>

                </div>
            `;
        }
    );

    html += `</div>`;

    content.innerHTML = html;
}


// ========================================================
// LIVE
// ========================================================

async function showLive() {

    currentTab = "live";

    setActive(2);

    content.innerHTML = `
        <div class="section">
            <div class="loading">
                🔴 Loading live matches...
            </div>
        </div>
    `;

    try {

        const response =
            await fetch("/api/live");

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "Live API error"
            );
        }

        const games =
            data.matches || [];

        if (!games.length) {

            content.innerHTML = `
                <div class="section">

                    <div class="section-title">
                        🔴 Live
                    </div>

                    <div class="empty">
                        No live football matches
                        available now.
                    </div>

                </div>
            `;

            return;
        }

        let html = `
            <div class="section">

                <div class="section-title">
                    🔴 Live
                </div>

        `;

        games.forEach(game => {

            html += `
                <div class="match">

                    <div class="teams">
                        ${escapeHtml(
                            game.home_team
                        )}

                        <span
                            style="
                                color:#ff4d4d;
                            ">
                            ${game.home_score ?? "-"}
                        </span>

                        -

                        <span
                            style="
                                color:#ff4d4d;
                            ">
                            ${game.away_score ?? "-"}
                        </span>

                        ${escapeHtml(
                            game.away_team
                        )}
                    </div>

                    <div class="meta">
                        🔴 LIVE
                        •
                        ${escapeHtml(
                            game.league || ""
                        )}
                    </div>

                </div>
            `;

        });

        html += `</div>`;

        content.innerHTML = html;

    } catch (error) {

        content.innerHTML = `
            <div class="section">

                <div class="section-title">
                    🔴 Live
                </div>

                <div class="error">
                    ${escapeHtml(
                        error.message
                    )}
                </div>

            </div>
        `;
    }
}


// ========================================================
// BET SLIP
// ========================================================

function showSlip() {

    currentTab = "slip";

    setActive(3);

    if (!slip.length) {

        content.innerHTML = `
            <div class="section">

                <div class="section-title">
                    🎟️ Bet Slip
                </div>

                <div class="empty">
                    Your Bet Slip is empty.
                </div>

            </div>
        `;

        return;
    }

    let html = `
        <div class="section">

            <div class="section-title">
                🎟️ Bet Slip
            </div>

            <div class="section-subtitle">
                ${slip.length} selection(s)
            </div>
    `;

    slip.forEach((item, index) => {

        html += `
            <div class="slip-item">

                <strong>
                    ${escapeHtml(
                        item.home_team
                    )}
                    vs
                    ${escapeHtml(
                        item.away_team
                    )}
                </strong>

                <div class="small">
                    ${escapeHtml(
                        item.league || ""
                    )}
                </div>

                <div
                    style="
                        margin-top:8px;
                        color:#ffd400;
                        font-weight:800;
                    ">

                    ${escapeHtml(
                        item.market
                    )}

                    —
                    ${escapeHtml(
                        item.selection
                    )}

                    —
                    ${money(item.odds)}

                </div>

                <button
                    class="big-button"
                    style="
                        background:#52272a;
                        color:white;
                    "
                    onclick="removeSlip(${index})">

                    🗑 Remove

                </button>

            </div>
        `;
    });

    html += `

        <button
            class="big-button"
            onclick="clearSlip()">

            🗑 Clear Bet Slip

        </button>

    </div>
    `;

    content.innerHTML = html;
}


function removeSlip(index) {

    slip.splice(index, 1);

    saveSlip();

    showSlip();
}


function clearSlip() {

    slip = [];

    saveSlip();

    showSlip();
}


// ========================================================
// START
// ========================================================

loadMatches();

</script>

</body>
</html>
"""


# =========================================================
# ODDS API FUNCTIONS
# =========================================================

def api_request(path, params=None):

    if not ODDS_API_KEY:
        raise RuntimeError(
            "ODDS_API_KEY hin jiru."
        )

    if params is None:
        params = {}

    params["apiKey"] = ODDS_API_KEY

    response = requests.get(
        ODDS_BASE + path,
        params=params,
        timeout=30,
        headers={
            "Accept": "application/json",
            "User-Agent": "BestBet/1.0",
        },
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Odds API error {response.status_code}: "
            f"{response.text[:500]}"
        )

    return response.json()


def get_sports():

    return api_request(
        "/sports",
        {},
    )


def get_soccer_sports():

    sports = get_sports()

    result = []

    for sport in sports:

        key = sport.get("key", "")

        if (
            key.startswith("soccer_")
            and sport.get("active", False)
        ):
            result.append(sport)

    return result


def get_odds_for_sport(sport_key):

    return api_request(
        f"/sports/{sport_key}/odds",
        {
            "regions": REGIONS,
            "markets": MARKETS,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
    )


# =========================================================
# CONVERT EVENT
# =========================================================

def convert_event(event, sport):

    bookmakers = event.get(
        "bookmakers",
        [],
    )

    h2h = {}
    totals = {}
    spreads = {}
    btts = {}

    # -----------------------------------------------------
    # Gather all bookmaker markets
    # -----------------------------------------------------

    for bookmaker in bookmakers:

        for market in bookmaker.get(
            "markets",
            []
        ):

            key = market.get("key")

            for outcome in market.get(
                "outcomes",
                []
            ):

                name = outcome.get("name")
                price = outcome.get("price")

                if price is None:
                    continue

                try:
                    price = float(price)
                except:
                    continue

                # 1X2
                if key == "h2h":

                    if name == event.get(
                        "home_team"
                    ):
                        h2h.setdefault(
                            "home",
                            price
                        )

                    elif name == event.get(
                        "away_team"
                    ):
                        h2h.setdefault(
                            "away",
                            price
                        )

                    elif name == "Draw":
                        h2h.setdefault(
                            "draw",
                            price
                        )

                # Totals
                elif key == "totals":

                    point = outcome.get(
                        "point"
                    )

                    if (
                        point is None
                        or float(point) == 2.5
                    ):

                        if name == "Over":
                            totals.setdefault(
                                "over",
                                price
                            )

                        elif name == "Under":
                            totals.setdefault(
                                "under",
                                price
                            )

                # Spread
                elif key == "spreads":

                    point = outcome.get(
                        "point"
                    )

                    spreads.setdefault(
                        "options",
                        []
                    )

                    spreads["options"].append({
                        "name": (
                            f"{name} "
                            f"{point}"
                            if point is not None
                            else name
                        ),
                        "price": price,
                    })

    # -----------------------------------------------------
    # Best Bet
    # -----------------------------------------------------

    candidates = []

    if h2h.get("home"):
        candidates.append({
            "market": "1X2",
            "selection": "Home",
            "odds": h2h["home"],
        })

    if h2h.get("draw"):
        candidates.append({
            "market": "1X2",
            "selection": "Draw",
            "odds": h2h["draw"],
        })

    if h2h.get("away"):
        candidates.append({
            "market": "1X2",
            "selection": "Away",
            "odds": h2h["away"],
        })

    if totals.get("over"):
        candidates.append({
            "market": "Over/Under",
            "selection": "Over 2.5",
            "odds": totals["over"],
        })

    if totals.get("under"):
        candidates.append({
            "market": "Over/Under",
            "selection": "Under 2.5",
            "odds": totals["under"],
        })

    # Lowest odds = strongest implied probability
    # among available selections.
    candidates = [
        x for x in candidates
        if 1.10 <= x["odds"] <= 3.00
    ]

    candidates.sort(
        key=lambda x: x["odds"]
    )

    best_bet = (
        candidates[0]
        if candidates
        else None
    )

    # -----------------------------------------------------
    # Time
    # -----------------------------------------------------

    commence = event.get(
        "commence_time"
    )

    formatted_time = ""

    if commence:

        try:

            dt = datetime.fromisoformat(
                commence.replace(
                    "Z",
                    "+00:00"
                )
            )

            formatted_time = dt.strftime(
                "%H:%M"
            )

        except Exception:
            formatted_time = ""

    return {
        "id": event.get("id"),
        "sport_key": sport.get("key"),
        "league": sport.get("title"),
        "commence_time": commence,
        "time": formatted_time,
        "home_team": event.get(
            "home_team"
        ),
        "away_team": event.get(
            "away_team"
        ),
        "h2h": h2h,
        "totals": totals,
        "spreads": spreads,
        "btts": btts,
        "best_bet": best_bet,
    }


# =========================================================
# TODAY MATCHES
# =========================================================

def get_all_matches():

    sports = get_soccer_sports()

    all_matches = []

    # Avoid consuming too much quota.
    for sport in sports[:15]:

        try:

            events = get_odds_for_sport(
                sport["key"]
            )

            for event in events:

                all_matches.append(
                    convert_event(
                        event,
                        sport,
                    )
                )

        except Exception as e:

            logger.warning(
                "Skipping %s: %s",
                sport.get("key"),
                e,
            )

    # Sort by kick-off time
    all_matches.sort(
        key=lambda x: (
            x.get(
                "commence_time"
            )
            or ""
        )
    )

    return all_matches


# =========================================================
# WEB ROUTES
# =========================================================

@app.route("/")
def index():

    return render_template_string(
        HTML
    )


@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "bot": "Best Bet",
        "api": "The Odds API",
        "api_key_configured": bool(
            ODDS_API_KEY
        ),
    })


@app.route("/api/matches")
def matches_api():

    try:

        matches = get_all_matches()

        return jsonify({
            "success": True,
            "matches": matches,
        })

    except Exception as e:

        logger.exception(
            "Matches API failed"
        )

        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
        }), 500


# =========================================================
# LIVE SCORES
# =========================================================

@app.route("/api/live")
def live_api():

    live_matches = []

    try:

        sports = get_soccer_sports()

        for sport in sports[:10]:

            try:

                data = api_request(
                    f"/sports/{sport['key']}/scores",
                    {
                        "daysFrom": 1,
                        "dateFormat": "iso",
                    },
                )

            except Exception:
                continue

            for event in data:

                # The Odds API score response
                # can contain completed status.
                if event.get(
                    "completed",
                    False
                ):
                    continue

                scores = event.get(
                    "scores"
                )

                home_score = None
                away_score = None

                if scores:

                    for score in scores:

                        name = score.get(
                            "name"
                        )

                        value = score.get(
                            "score"
                        )

                        if name == event.get(
                            "home_team"
                        ):
                            home_score = value

                        elif name == event.get(
                            "away_team"
                        ):
                            away_score = value

                live_matches.append({
                    "id": event.get("id"),
                    "league": sport.get(
                        "title"
                    ),
                    "home_team": event.get(
                        "home_team"
                    ),
                    "away_team": event.get(
                        "away_team"
                    ),
                    "home_score": home_score,
                    "away_score": away_score,
                    "commence_time": event.get(
                        "commence_time"
                    ),
                })

        return jsonify({
            "success": True,
            "matches": live_matches,
        })

    except Exception as e:

        logger.exception(
            "Live API failed"
        )

        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
        }), 500


# =========================================================
# FLASK SERVER
# =========================================================

def run_web():

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# TELEGRAM BOT
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📅 Today",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            )
        ],

        [
            InlineKeyboardButton(
                "⚽ Football",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            )
        ],

        [
            InlineKeyboardButton(
                "🎯 Best Bet",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            ),

            InlineKeyboardButton(
                "🎟️ Bet Slip",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            ),
        ],
    ])

    await update.message.reply_text(
        "🎯 <b>BEST BET</b>\n\n"
        "Football odds • predictions • bet slip\n\n"
        "⚽ Football cuqaasi; Web App ni banama.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# OPTIONAL /WEBAPP
# =========================================================

async def webapp_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "⚽ <b>Best Bet Web App</b>\n\n"
        "Button kana cuqaasi:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚽ Open Football",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    ),
                )
            ]
        ]),
    )


# =========================================================
# TELEGRAM ERROR
# =========================================================

async def error_handler(
    update,
    context,
):

    logger.exception(
        "Telegram error",
        exc_info=context.error,
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

        logger.warning(
            "ODDS_API_KEY hin jiru."
        )

    # Start Flask
    Thread(
        target=run_web,
        daemon=True,
    ).start()

    # Telegram
    application = (
        Application
        .builder()
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
        CommandHandler(
            "webapp",
            webapp_command,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "BEST BET BOT STARTING"
    )

    logger.info(
        "WEB APP: %s",
        WEB_APP_URL,
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
