import os
import json
import hmac
import hashlib
import sqlite3
import threading
import logging
import random
import urllib.parse
import asyncio
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
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "casino.db")

# Payment details:
# You can override these values with Render environment variables.
PAYMENT_INFO = {
    "Telebirr": os.getenv(
        "PAY_TELEBIRR",
        "Telebirr\nAccount Name: Chala\nPhone: 0902640434",
    ),
    "CBE": os.getenv(
        "PAY_CBE",
        "Payment details will be announced",
    ),
    "Awash": os.getenv(
        "PAY_AWASH",
        "Payment details will be announced",
    ),
}

BONUS = 20
SLOT_ICONS = ["🍊", "🍋", "🍇", "🍑", "🍒", "🍉", "🔔", "🟨", "7️⃣", "🎰"]

app = Flask(__name__)
db_lock = threading.Lock()

bot_app = None
bot_loop = None


def connect():
    c = sqlite3.connect(DATABASE_PATH, timeout=25)
    c.row_factory = sqlite3.Row
    # Enforce foreign keys if added in future schema updates.
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with connect() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER NOT NULL DEFAULT 20,
                bonus_given INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game TEXT,
                stake INTEGER,
                payout INTEGER,
                result TEXT,
                created TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS deposits(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                method TEXT,
                amount INTEGER,
                reference TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created TEXT,
                reviewed TEXT,
                reviewed_by INTEGER
            )
        """)

        # Add the bonus flag to older databases without resetting balances.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "bonus_given" not in cols:
            c.execute(
                "ALTER TABLE users ADD COLUMN bonus_given INTEGER NOT NULL DEFAULT 1"
            )

        # Legacy recovery: only zero/negative accounts get the demo bonus.
        # Positive balances are not changed.
        c.execute(
            "UPDATE users SET balance=?, bonus_given=1 WHERE balance<=0",
            (BONUS,),
        )


init_db()


def verify_init_data(raw):
    """Verify Telegram WebApp initData using the bot token."""
    if not BOT_TOKEN or not raw:
        return None

    try:
        pairs = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        got = pairs.pop("hash", None)
        if not got:
            return None

        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()
        want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(want, got):
            return None

        user = json.loads(pairs["user"])
        auth_date = int(pairs["auth_date"])
        age = datetime.now(timezone.utc).timestamp() - auth_date

        # Reject expired or future-dated initData.
        if age < 0 or age > 86400:
            return None

        return user
    except Exception:
        logging.exception("WebApp authentication failed")
        return None


def authorized():
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = verify_init_data(raw)
    return user if user and user.get("id") else None


def ensure_user(c, user):
    uid = int(user["id"])
    username = user.get("username", "")

    # New users get the one-time promotional demo credit.
    c.execute(
        """
        INSERT OR IGNORE INTO users(user_id, username, balance, bonus_given)
        VALUES(?,?,?,1)
        """,
        (uid, username, BONUS),
    )
    c.execute(
        "UPDATE users SET username=? WHERE user_id=?",
        (username, uid),
    )

    # Legacy zero-balance recovery.
    c.execute(
        """
        UPDATE users SET balance=?, bonus_given=1
        WHERE user_id=? AND balance<=0
        """,
        (BONUS, uid),
    )

    return c.execute(
        "SELECT * FROM users WHERE user_id=?",
        (uid,),
    ).fetchone()


@app.get("/")
def index():
    return send_from_directory(".", "index.html")


@app.get("/health")
def health():
    return jsonify(ok=True, service="Multi Hot Megawin")


@app.get("/api/me")
def me():
    user = authorized()
    if not user:
        return jsonify(error="Open this app from its Telegram bot."), 401

    uid = int(user["id"])
    with db_lock, connect() as c:
        row = ensure_user(c, user)
        history_rows = c.execute(
            """
            SELECT game,stake,payout,result,created
            FROM history WHERE user_id=?
            ORDER BY id DESC LIMIT 15
            """,
            (uid,),
        ).fetchall()
        deposit_rows = c.execute(
            """
            SELECT id,method,amount,status,created
            FROM deposits WHERE user_id=?
            ORDER BY id DESC LIMIT 10
            """,
            (uid,),
        ).fetchall()

    return jsonify(
        user={
            "id": uid,
            "name": user.get("first_name", "Player"),
        },
        balance=row["balance"],
        bonus=BONUS,
        history=[dict(x) for x in history_rows],
        deposits=[dict(x) for x in deposit_rows],
    )


@app.post("/api/spin")
def spin_api():
    user = authorized()
    if not user:
        return jsonify(
            error="Telegram keessaa app bani; authentication hin jiru."
        ), 401

    body = request.get_json(silent=True) or {}
    try:
        stake = int(body.get("bet", 0))
        multiplier = int(body.get("multiplier", 1))
    except (TypeError, ValueError):
        return jsonify(error="Bet ykn multiplier sirrii miti."), 400

    if stake < 1 or stake > 100 or multiplier not in (1, 2, 5):
        return jsonify(
            error="Bet 1–100, multiplier 1x, 2x ykn 5x ta'uu qaba."
        ), 400

    # Server generates symbols and calculates results.
    grid = [random.choice(SLOT_ICONS) for _ in range(15)]
    payout = 0
    wins = []

    for row_index in range(3):
        first = grid[row_index * 5]
        count = 1
        for col in range(1, 5):
            if grid[row_index * 5 + col] == first:
                count += 1
            else:
                break

        if count >= 3:
            factor = 2 if count == 3 else 5 if count == 4 else 12
            payout += stake * multiplier * factor
            wins.append({"row": row_index, "count": count})

    # Demo consolation outcome, generated on the server.
    if payout == 0 and random.random() < 0.13:
        payout = stake * multiplier
        wins.append({
            "row": random.randrange(3),
            "count": 3,
            "bonus": True,
        })

    uid = int(user["id"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db_lock, connect() as c:
        ensure_user(c, user)

        # Conditional debit avoids spending below zero during concurrent calls.
        cur = c.execute(
            """
            UPDATE users SET balance=balance-?
            WHERE user_id=? AND balance>=?
            """,
            (stake, uid, stake),
        )
        if cur.rowcount != 1:
            return jsonify(error="Balance gahaa'aa miti."), 400

        if payout:
            c.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (payout, uid),
            )

        new_balance = c.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (uid,),
        ).fetchone()["balance"]

        c.execute(
            """
            INSERT INTO history(user_id,game,stake,payout,result,created)
            VALUES(?,?,?,?,?,?)
            """,
            (
                uid,
                "MULTI HOT MEGAWIN",
                stake,
                payout,
                json.dumps({"grid": grid, "wins": wins}),
                now,
            ),
        )

    return jsonify(
        ok=True,
        grid=grid,
        payout=payout,
        balance=new_balance,
        wins=wins,
    )


@app.get("/api/payment-methods")
def payment_methods():
    return jsonify(
        methods=[
            {"name": name, "details": details}
            for name, details in PAYMENT_INFO.items()
        ]
    )


@app.post("/api/deposit")
def deposit():
    user = authorized()
    if not user:
        return jsonify(error="Telegram authentication failed."), 401

    body = request.get_json(silent=True) or {}
    method = str(body.get("method", "")).strip()
    reference = str(body.get("reference", "")).strip()[:240]

    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify(error="Amount invalid."), 400

    if method not in PAYMENT_INFO:
        return jsonify(error="Select a payment method."), 400
    if amount < 1 or amount > 100000:
        return jsonify(error="Amount must be 1–100000."), 400
    if len(reference) < 4:
        return jsonify(
            error="Enter transaction reference or receipt details."
        ), 400

    uid = int(user["id"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db_lock, connect() as c:
        ensure_user(c, user)
        cur = c.execute(
            """
            INSERT INTO deposits(
                user_id,username,method,amount,reference,status,created
            )
            VALUES(?,?,?,?,?,'PENDING',?)
            """,
            (
                uid,
                user.get("username", ""),
                method,
                amount,
                reference,
                now,
            ),
        )
        deposit_id = cur.lastrowid

    # Notify admin; the deposit remains recorded if notification fails.
    try:
        if bot_loop and bot_app:
            future = asyncio.run_coroutine_threadsafe(
                notify_admin(
                    deposit_id,
                    uid,
                    user.get("username", ""),
                    method,
                    amount,
                    reference,
                ),
                bot_loop,
            )
            # Do not block the web request waiting for Telegram.
            future.add_done_callback(
                lambda f: logging.error(
                    "Admin notification failed: %s", f.exception()
                ) if f.exception() else None
            )
        else:
            logging.warning("Bot is not ready; deposit #%s is pending.", deposit_id)
    except Exception:
        logging.exception("Could not notify admin")

    return jsonify(
        ok=True,
        deposit_id=deposit_id,
        status="PENDING",
        message="Request received. Balance changes only after admin review.",
    )


async def notify_admin(deposit_id, uid, username, method, amount, reference):
    if not bot_app or not ADMIN_ID:
        logging.warning("ADMIN_ID is not configured; deposit awaits manual review.")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ APPROVE",
            callback_data=f"dep:approve:{deposit_id}",
        ),
        InlineKeyboardButton(
            "❌ REJECT",
            callback_data=f"dep:reject:{deposit_id}",
        ),
    ]])

    await bot_app.bot.send_message(
        ADMIN_ID,
        (
            f"💳 Deposit request #{deposit_id}\n"
            f"User: {uid} (@{username or 'none'})\n"
            f"Method: {method}\n"
            f"Amount: {amount} ETB\n"
            f"Reference: {reference}\n"
            f"Status: PENDING\n\n"
            "Verify the actual payment before approving."
        ),
        reply_markup=keyboard,
    )


async def review_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    if (
        not ADMIN_ID
        or not update.effective_user
        or update.effective_user.id != ADMIN_ID
    ):
        await query.answer("Not authorized.", show_alert=True)
        return

    try:
        _, action, deposit_id_text = query.data.split(":")
        deposit_id = int(deposit_id_text)
    except Exception:
        await query.answer("Invalid request.")
        return

    if action not in ("approve", "reject"):
        await query.answer("Invalid action.")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db_lock, connect() as c:
        row = c.execute(
            "SELECT * FROM deposits WHERE id=?",
            (deposit_id,),
        ).fetchone()

        if not row:
            await query.answer("Request not found.", show_alert=True)
            return

        if row["status"] != "PENDING":
            await query.answer(
                "Already reviewed: " + row["status"],
                show_alert=True,
            )
            return

        status = "APPROVED" if action == "approve" else "REJECTED"

        # Conditional status change ensures one-time processing.
        cur = c.execute(
            """
            UPDATE deposits
            SET status=?,reviewed=?,reviewed_by=?
            WHERE id=? AND status='PENDING'
            """,
            (status, now, update.effective_user.id, deposit_id),
        )
        if cur.rowcount != 1:
            await query.answer("Already processed.", show_alert=True)
            return

        if action == "approve":
            credited = c.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (row["amount"], row["user_id"]),
            )
            if credited.rowcount != 1:
                # Raise so the transaction rolls back rather than approve
                # a deposit without crediting the user's wallet.
                raise RuntimeError("Deposit owner account was not found.")

    await query.answer(status)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.message.reply_text(f"Deposit #{deposit_id}: {status}.")

    # Tell the customer their request has been reviewed.
    try:
        if bot_app:
            await bot_app.bot.send_message(
                int(row["user_id"]),
                (
                    f"Deposit request #{deposit_id}: {status}.\n"
                    + (
                        f"{row['amount']} ETB has been credited to your game balance."
                        if action == "approve"
                        else "No balance was added."
                    )
                ),
            )
    except Exception:
        logging.exception("Could not send deposit result to player")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL:
        await update.effective_message.reply_text(
            "WEBAPP_URL is not configured."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎰 OPEN MULTI HOT MEGAWIN",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )
    ]])

    await update.effective_message.reply_text(
        (
            "🎰 MULTI HOT MEGAWIN\n\n"
            "New account: one-time 20 demo-credit bonus.\n"
            "Deposits remain pending until an admin reviews them.\n"
            "Demo credits are not cash. Please check local laws and age requirements."
        ),
        reply_markup=keyboard,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Use /start to open the game."
    )


async def run_bot():
    global bot_app, bot_loop

    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is missing.")
        return

    bot_loop = asyncio.get_running_loop()
    bot_app = Application.builder().token(BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("help", help_cmd))
    bot_app.add_handler(
        CallbackQueryHandler(
            review_deposit,
            pattern=r"^dep:(approve|reject):\d+$",
        )
    )

    await bot_app.initialize()
    await bot_app.start()
    if bot_app.updater:
        await bot_app.updater.start_polling()

    logging.info("Telegram polling started.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    threading.Thread(
        target=lambda: asyncio.run(run_bot()),
        daemon=True,
    ).start()

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
