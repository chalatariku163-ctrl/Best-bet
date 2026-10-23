import os
import json
import hmac
import hashlib
import sqlite3
import threading
import logging
import random
import urllib.parse

from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# CONFIGURATION
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    ""
).strip().rstrip("/")

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "casino.db"
)

app = Flask(__name__)

db_lock = threading.Lock()


# =========================
# DATABASE
# =========================

def connect():
    conn = sqlite3.connect(
        DATABASE_PATH,
        timeout=20
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with connect() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER NOT NULL DEFAULT 1000
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game TEXT,
                stake INTEGER,
                payout INTEGER,
                result TEXT,
                created TEXT
            )
        """)


init_db()


# =========================
# TELEGRAM WEBAPP SECURITY
# =========================

def verify_init_data(raw):

    if not BOT_TOKEN or not raw:
        return None

    try:

        pairs = dict(
            urllib.parse.parse_qsl(
                raw,
                keep_blank_values=True
            )
        )

        received_hash = pairs.pop("hash", None)

        if not received_hash:
            return None

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(pairs.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user = json.loads(pairs["user"])

        auth_date = int(pairs["auth_date"])

        now = datetime.now(
            timezone.utc
        ).timestamp()

        # Reject old authentication data
        if now - auth_date > 86400:
            return None

        return user

    except Exception:

        logging.exception(
            "Telegram authentication failed"
        )

        return None


def authorized():

    raw = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user = verify_init_data(raw)

    if not user or not user.get("id"):
        return None

    return user


# =========================
# HOME PAGE
# =========================

@app.get("/")
def index():

    return send_from_directory(
        ".",
        "index.html"
    )


@app.get("/health")
def health():

    return jsonify(
        ok=True,
        service="BEST BET CASINO"
    )


# =========================
# USER PROFILE / BALANCE
# =========================

@app.get("/api/me")
def me():

    user = authorized()

    if not user:

        return jsonify(
            error="Open this app from its Telegram bot."
        ), 401

    uid = int(user["id"])

    username = user.get(
        "username",
        ""
    )

    with db_lock, connect() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO users
            (user_id, username, balance)
            VALUES (?, ?, 1000)
        """, (
            uid,
            username
        ))

        conn.execute("""
            UPDATE users
            SET username = ?
            WHERE user_id = ?
        """, (
            username,
            uid
        ))

        row = conn.execute("""
            SELECT balance
            FROM users
            WHERE user_id = ?
        """, (
            uid,
        )).fetchone()

        history = conn.execute("""
            SELECT game, stake, payout, result, created
            FROM history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 15
        """, (
            uid,
        )).fetchall()

    return jsonify(
        user={
            "id": uid,
            "name": user.get(
                "first_name",
                "Player"
            )
        },
        balance=row["balance"],
        history=[
            dict(item)
            for item in history
        ]
    )


# =========================
# PLAY GAME
# =========================

@app.post("/api/play")
def play():

    user = authorized()

    if not user:

        return jsonify(
            error="Authentication failed. Open via Telegram."
        ), 401

    body = request.get_json(
        silent=True
    ) or {}

    game = body.get("game")

    try:

        stake = int(
            body.get("stake", 0)
        )

    except (ValueError, TypeError):

        return jsonify(
            error="Invalid stake."
        ), 400

    if game not in (
        "slots",
        "blackjack",
        "roulette"
    ):

        return jsonify(
            error="Invalid game."
        ), 400

    if stake < 1 or stake > 100:

        return jsonify(
            error="Stake must be between 1 and 100 demo points."
        ), 400

    uid = int(user["id"])

    # =====================
    # SLOT MACHINE
    # =====================

    if game == "slots":

        symbols = [
            "🍒",
            "🍋",
            "🔔",
            "⭐",
            "7️⃣"
        ]

        reels = [
            random.choice(symbols)
            for _ in range(3)
        ]

        if reels[0] == reels[1] == reels[2]:

            payout = stake * 10

        elif len(set(reels)) == 2:

            payout = stake * 2

        else:

            payout = 0

        result = " ".join(reels)

    # =====================
    # ROULETTE
    # =====================

    elif game == "roulette":

        number = random.randint(
            0,
            36
        )

        pick = body.get("pick")

        if pick not in (
            "red",
            "black",
            "green"
        ):

            return jsonify(
                error="Choose red, black, or green."
            ), 400

        reds = {
            1, 3, 5, 7, 9,
            12, 14, 16, 18,
            19, 21, 23, 25,
            27, 30, 32, 34, 36
        }

        if number == 0:

            color = "green"

        elif number in reds:

            color = "red"

        else:

            color = "black"

        if color == pick:

            if color == "green":

                payout = stake * 14

            else:

                payout = stake * 2

        else:

            payout = 0

        result = (
            f"{number} ({color}); "
            f"you picked {pick}"
        )

    # =====================
    # BLACKJACK DEMO
    # =====================

    else:

        deck = [
            2, 3, 4, 5, 6,
            7, 8, 9, 10, 10,
            10, 11
        ]

        player = (
            random.choice(deck)
            + random.choice(deck)
        )

        dealer = (
            random.choice(deck)
            + random.choice(deck)
        )

        if player > dealer:

            payout = stake * 2

        elif player == dealer:

            payout = stake

        else:

            payout = 0

        result = (
            f"You: {player} | "
            f"Dealer: {dealer}"
        )

    # =====================
    # UPDATE BALANCE
    # =====================

    with db_lock, connect() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO users
            (user_id, username, balance)
            VALUES (?, ?, 1000)
        """, (
            uid,
            user.get("username", "")
        ))

        row = conn.execute("""
            SELECT balance
            FROM users
            WHERE user_id = ?
        """, (
            uid,
        )).fetchone()

        if row["balance"] < stake:

            return jsonify(
                error="Not enough demo points."
            ), 400

        balance = (
            row["balance"]
            - stake
            + payout
        )

        conn.execute("""
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
        """, (
            balance,
            uid
        ))

        conn.execute("""
            INSERT INTO history (
                user_id,
                game,
                stake,
                payout,
                result,
                created
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            uid,
            game,
            stake,
            payout,
            result,
            datetime.now(
                timezone.utc
            ).isoformat(
                timespec="seconds"
            )
        ))

    return jsonify(
        balance=balance,
        stake=stake,
        payout=payout,
        result=result,
        net=payout - stake,
        symbols=reels if game == "slots" else None
    )


# =========================
# TELEGRAM /START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not WEBAPP_URL:

        await update.message.reply_text(
            "Bot is installed, but WEBAPP_URL "
            "is not configured in Render."
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎰 PLAY BEST BET CASINO",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ]
    ])

    await update.message.reply_text(
        "👑 Welcome to BEST BET CASINO!\n\n"
        "🎰 Slots\n"
        "🃏 Blackjack\n"
        "🎡 Roulette\n\n"
        "Demo points only.\n"
        "No real-money deposits or withdrawals.\n\n"
        "👇 Tap below to play.",
        reply_markup=keyboard
    )


# =========================
# HELP COMMAND
# =========================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎰 BEST BET CASINO\n\n"
        "Use /start to open the casino.\n"
        "Demo points only."
    )


# =========================
# TELEGRAM BOT RUNNER
# =========================

def run_bot():

    if not BOT_TOKEN:

        logging.error(
            "BOT_TOKEN is missing."
        )

        return

    async def runner():

        telegram_app = (
            Application.builder()
            .token(BOT_TOKEN)
            .build()
        )

        telegram_app.add_handler(
            CommandHandler(
                "start",
                start
            )
        )

        telegram_app.add_handler(
            CommandHandler(
                "help",
                help_cmd
            )
        )

        await telegram_app.initialize()

        await telegram_app.start()

        await telegram_app.updater.start_polling()

        logging.info(
            "Telegram polling started."
        )

        import asyncio

        while True:

            await asyncio.sleep(3600)

    import asyncio

    asyncio.run(
        runner()
    )


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    threading.Thread(
        target=run_bot,
        daemon=True
    ).start()

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )
        )
