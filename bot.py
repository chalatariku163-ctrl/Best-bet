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

# For Render Persistent Disk, set DATABASE_PATH=/var/data/casino.db
DATABASE_PATH = os.getenv("DATABASE_PATH", "casino.db")
RECEIPT_DIR = os.getenv("RECEIPT_DIR", "/var/data/deposit_receipts")

PAYMENT_INFO = {
    "Telebirr": os.getenv(
        "PAY_TELEBIRR",
        "Telebirr\nAccount Name: Chala\nPhone: 0902640434",
    ),
    "CBE": os.getenv("PAY_CBE", "Payment details will be announced"),
    "Awash": os.getenv("PAY_AWASH", "Payment details will be announced"),
}

BONUS = 20
SLOT_ICONS = ["🍊", "🍋", "🍇", "🍑", "🍒", "🍉", "🔔", "🟨", "7️⃣", "🎰"]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

db_lock = threading.Lock()
bot_app = None
bot_loop = None


def connect():
    c = sqlite3.connect(DATABASE_PATH, timeout=25)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    # Ensure parent directory exists (especially /var/data on Render).
    parent = os.path.dirname(os.path.abspath(DATABASE_PATH))
    os.makedirs(parent, exist_ok=True)

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
                reviewed_by INTEGER,
                receipt_path TEXT
            )
        """)

        dep_cols = {
            r["name"] for r in c.execute("PRAGMA table_info(deposits)")
        }
        if "receipt_path" not in dep_cols:
            c.execute("ALTER TABLE deposits ADD COLUMN receipt_path TEXT")

        user_cols = {
            r["name"] for r in c.execute("PRAGMA table_info(users)")
        }
        if "bonus_given" not in user_cols:
            c.execute(
                "ALTER TABLE users ADD COLUMN bonus_given INTEGER NOT NULL DEFAULT 1"
            )



init_db()


@app.errorhandler(413)
def file_too_large(error):
    return jsonify(
        ok=False,
        error="Screenshot baay'ee guddaa dha. Suuraa xiqqeessi."
    ), 413


@app.errorhandler(404)
def api_not_found(error):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="API endpoint hin argamne."), 404
    return error


@app.errorhandler(500)
def internal_error(error):
    logging.exception("Flask internal server error")
    return jsonify(ok=False, error="Server error. Render Logs ilaali."), 500


def verify_init_data(raw):
    """Validate Telegram WebApp initData using the bot token."""
    if not BOT_TOKEN or not raw:
        return None

    try:
        pairs = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        got_hash = pairs.pop("hash", None)
        if not got_hash:
            return None

        check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(pairs.items())
        )
        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected = hmac.new(
            secret,
            check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, got_hash):
            return None

        user = json.loads(pairs["user"])
        auth_date = int(pairs["auth_date"])
        age = datetime.now(timezone.utc).timestamp() - auth_date

        # Reject future or stale authentication data.
        if age < 0 or age > 86400:
            return None

        return user
    except Exception:
        logging.exception("Telegram WebApp authentication validation failed")
        return None


def authorized():
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = verify_init_data(raw)
    return user if user and user.get("id") else None


def ensure_user(c, user):
    uid = int(user["id"])
    username = user.get("username", "") or ""

    c.execute(
        """
        INSERT OR IGNORE INTO users(user_id,username,balance,bonus_given)
        VALUES(?,?,?,1)
        """,
        (uid, username, BONUS),
    )
    c.execute(
        "UPDATE users SET username=? WHERE user_id=?",
        (username, uid),
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
        user={"id": uid, "name": user.get("first_name", "Player")},
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
        return jsonify(error="Enter transaction reference or receipt details."), 400

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db_lock, connect() as c:
        ensure_user(c, user)
        cur = c.execute(
            """
            INSERT INTO deposits(
                user_id,username,method,amount,reference,status,created
            ) VALUES(?,?,?,?,?,'PENDING',?)
            """,
            (
                int(user["id"]),
                user.get("username", "") or "",
                method,
                amount,
                reference,
                now,
            ),
        )
        deposit_id = cur.lastrowid

    return jsonify(
        ok=True,
        deposit_id=deposit_id,
        status="WAITING_RECEIPT",
        message="Request created. Upload your payment screenshot.",
    )


@app.post("/api/deposit/<int:deposit_id>/receipt")
def upload_deposit_receipt(deposit_id):
    user = authorized()
    if not user:
        return jsonify(
            ok=False,
            error="Telegram authentication failed."
        ), 401

    proof = request.files.get("receipt")
    if not proof or not proof.filename:
        return jsonify(
            ok=False,
            error="Choose a screenshot image first."
        ), 400

    accepted = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    ext = accepted.get(proof.mimetype)
    if not ext:
        return jsonify(
            ok=False,
            error="Choose JPG, PNG or WEBP image."
        ), 400

    uid = int(user["id"])
    path = None

    try:
        os.makedirs(RECEIPT_DIR, exist_ok=True)

        with db_lock, connect() as c:
            row = c.execute(
                "SELECT * FROM deposits WHERE id=?",
                (deposit_id,),
            ).fetchone()

            if not row or int(row["user_id"]) != uid:
                return jsonify(
                    ok=False,
                    error="Deposit request not found."
                ), 404

            if row["status"] != "PENDING":
                return jsonify(
                    ok=False,
                    error="Request already reviewed."
                ), 409

            if row["receipt_path"]:
                return jsonify(
                    ok=False,
                    error="Receipt already sent for this request."
                ), 409

            path = os.path.join(
                RECEIPT_DIR,
                f"deposit_{deposit_id}_{uid}{ext}",
            )
            proof.save(path)

            size = os.path.getsize(path)
            if size <= 0:
                os.remove(path)
                return jsonify(ok=False, error="Empty screenshot."), 400

            if size > 7 * 1024 * 1024:
                os.remove(path)
                return jsonify(
                    ok=False,
                    error="Image exceeds 7 MB."
                ), 413

            cur = c.execute(
                """
                UPDATE deposits SET receipt_path=?
                WHERE id=? AND user_id=?
                AND status='PENDING' AND receipt_path IS NULL
                """,
                (path, deposit_id, uid),
            )
            if cur.rowcount != 1:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return jsonify(
                    ok=False,
                    error="Request changed. Please check deposit history."
                ), 409

            args = (
                int(row["user_id"]),
                row["username"] or "",
                row["method"],
                int(row["amount"]),
                row["reference"],
            )

        # Receipt is stored. Notify admin if bot is available.
        if not bot_loop or not bot_app or not ADMIN_ID:
            logging.error(
                "Receipt saved but admin/bot unavailable for deposit #%s",
                deposit_id,
            )
            return jsonify(
                ok=True,
                deposit_id=deposit_id,
                status="PENDING_REVIEW",
                message="Screenshot saved. Admin notification unavailable.",
            ), 200

        try:
            future = asyncio.run_coroutine_threadsafe(
                notify_admin_receipt(deposit_id, *args, path),
                bot_loop,
            )
            future.result(timeout=35)
        except Exception:
            logging.exception(
                "Could not notify admin for deposit #%s",
                deposit_id,
            )
            # Receipt remains saved. Do not make user upload it again.
            return jsonify(
                ok=True,
                deposit_id=deposit_id,
                status="PENDING_REVIEW",
                message="Screenshot saved, but admin notification failed. Contact support.",
            ), 200

        return jsonify(
            ok=True,
            deposit_id=deposit_id,
            status="PENDING_REVIEW",
            message="Screenshot received. Await admin review.",
        ), 200

    except Exception:
        logging.exception("Deposit receipt upload failed: #%s", deposit_id)
        return jsonify(
            ok=False,
            error="Upload failed. Please check Render Logs."
        ), 500


async def notify_admin_receipt(
    deposit_id, uid, username, method, amount, reference, path
):
    if not bot_app or not ADMIN_ID:
        raise RuntimeError("ADMIN_ID or Telegram bot is not configured.")

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

    caption = (
        f"📸 DEPOSIT RECEIPT #{deposit_id}\n"
        f"User: {uid} (@{username or 'none'})\n"
        f"Method: {method}\n"
        f"Amount: {amount} ETB\n"
        f"Reference: {reference}\n"
        "Status: PENDING\n\n"
        "Verify actual payment before approving."
    )

    with open(path, "rb") as image_file:
        await bot_app.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=image_file,
            caption=caption,
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

        # Require screenshot to have been received before approving.
        if action == "approve" and not row["receipt_path"]:
            await query.answer(
                "Receipt is missing. Do not approve.",
                show_alert=True,
            )
            return

        status = "APPROVED" if action == "approve" else "REJECTED"

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
                raise RuntimeError(
                    "Deposit owner account was not found; transaction rolled back."
                )

    await query.answer(status)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logging.exception("Could not remove deposit review buttons")

    try:
        await query.message.reply_text(
            f"Deposit #{deposit_id}: {status}."
        )
    except Exception:
        logging.exception("Could not post review result")

    try:
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
        logging.exception("Could not notify player of deposit decision")


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
            "Deposits stay pending until admin review.\n"
            "Demo credits are not cash. Check local laws and age requirements."
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
        threaded=True,
    )
