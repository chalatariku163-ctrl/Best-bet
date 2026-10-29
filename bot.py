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
from io import BytesIO
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

# =====================================================
# CONFIGURATION
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DATABASE_PATH = os.getenv("DATABASE_PATH", "casino.db")

PAYMENT_INFO = {
    "Telebirr": os.getenv(
        "PAY_TELEBIRR",
        "Telebirr\nAccount Name: Chala\nPhone: 0902640434",
    ),
    "CBE": os.getenv("PAY_CBE", "Payment details will be announced"),
    "Awash": os.getenv("PAY_AWASH", "Payment details will be announced"),
}

BONUS = 20

SLOT_ICONS = [
    "🍊", "🍋", "🍇", "🍑", "🍒",
    "🍉", "🔔", "🟨", "7️⃣", "🎰"
]

MAX_RECEIPT_SIZE = 7 * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

db_lock = threading.RLock()
bot_app = None
bot_loop = None


# =====================================================
# DATABASE
# =====================================================

def connect():
    c = sqlite3.connect(DATABASE_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init_db():
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
            c.execute("""
                ALTER TABLE users
                ADD COLUMN bonus_given INTEGER NOT NULL DEFAULT 1
            """)


init_db()


# =====================================================
# FLASK ERRORS
# =====================================================

@app.errorhandler(413)
def file_too_large(error):
    return jsonify(ok=False, error="Screenshot baay'ee guddaa dha."), 413


@app.errorhandler(404)
def api_not_found(error):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="API endpoint hin argamne."), 404
    return error


@app.errorhandler(500)
def internal_error(error):
    logging.error("Flask internal server error: %s", error, exc_info=True)
    return jsonify(ok=False, error="Server error. Render Logs ilaali."), 500


# =====================================================
# TELEGRAM WEBAPP AUTHENTICATION
# =====================================================

def verify_init_data(raw):
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

        if age < 0 or age > 86400:
            return None

        return user

    except Exception:
        logging.exception("Telegram authentication validation failed")
        return None


def authorized():
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = verify_init_data(raw)
    if user and user.get("id"):
        return user
    return None


def ensure_user(c, user):
    uid = int(user["id"])
    username = user.get("username", "") or ""

    c.execute("""
        INSERT OR IGNORE INTO users(user_id, username, balance, bonus_given)
        VALUES(?,?,?,1)
    """, (uid, username, BONUS))

    c.execute("""
        UPDATE users SET username=? WHERE user_id=?
    """, (username, uid))

    return c.execute(
        "SELECT * FROM users WHERE user_id=?", (uid,)
    ).fetchone()


# =====================================================
# WEBSITE
# =====================================================

@app.get("/")
def index():
    return send_from_directory(".", "index.html")


@app.get("/health")
def health():
    return jsonify(ok=True, service="Multi Hot Megawin")


# =====================================================
# USER PROFILE
# =====================================================

@app.get("/api/me")
def me():
    user = authorized()
    if not user:
        return jsonify(error="Open this app from its Telegram bot."), 401

    uid = int(user["id"])

    with db_lock, connect() as c:
        row = ensure_user(c, user)

        history_rows = c.execute("""
            SELECT game,stake,payout,result,created
            FROM history WHERE user_id=? ORDER BY id DESC LIMIT 15
        """, (uid,)).fetchall()

        deposit_rows = c.execute("""
            SELECT id,method,amount,status,created
            FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 10
        """, (uid,)).fetchall()

    return jsonify(
        user={"id": uid, "name": user.get("first_name", "Player")},
        balance=row["balance"],
        bonus=BONUS,
        history=[dict(x) for x in history_rows],
        deposits=[dict(x) for x in deposit_rows],
    )


# =====================================================
# SLOT GAME
# 3x3 grid. Winning lines: 3 horizontals + 2 diagonals.
# Vertical lines do not count.
# 3 matching = 2x; pair + one 7 wild = 2x;
# 777 = BIG WIN 10x. No random bonus.
# =====================================================

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

    if stake < 1 or stake > 100:
        return jsonify(error="Bet 1-100 ta'uu qaba."), 400
    if multiplier not in (1, 2, 5):
        return jsonify(error="Multiplier 1x, 2x ykn 5x ta'uu qaba."), 400

    # Exactly 9 symbols: three rows and three columns.
    grid = [random.choice(SLOT_ICONS) for _ in range(9)]

    # Horizontal lines and both diagonals only.
    winning_lines = [
        (0, [0, 1, 2]),
        (1, [3, 4, 5]),
        (2, [6, 7, 8]),
        (3, [0, 4, 8]),
        (4, [2, 4, 6]),
    ]

    payout = 0
    wins = []

    for line_id, indexes in winning_lines:
        line = [grid[i] for i in indexes]

        # Triple 7 is the top-priority jackpot.
        if line == ["7️⃣", "7️⃣", "7️⃣"]:
            factor = 10
            payout += stake * multiplier * factor
            wins.append({
                "line": line_id,
                "symbols": line,
                "type": "BIG_WIN",
                "factor": factor,
            })
            continue

        # Exclude 7 wild when checking the two base symbols.
        normal_symbols = [symbol for symbol in line if symbol != "7️⃣"]

        # Three same non-wild symbols.
        if (
            len(normal_symbols) == 3
            and normal_symbols[0] == normal_symbols[1] == normal_symbols[2]
        ):
            factor = 2
            payout += stake * multiplier * factor
            wins.append({
                "line": line_id,
                "symbols": line,
                "type": "WIN",
                "factor": factor,
            })
            continue

        # Exactly two same non-wild symbols and one 7 wild.
        if (
            len(line) == 3
            and line.count("7️⃣") == 1
            and len(normal_symbols) == 2
            and normal_symbols[0] == normal_symbols[1]
        ):
            factor = 2
            payout += stake * multiplier * factor
            wins.append({
                "line": line_id,
                "symbols": line,
                "type": "WILD_WIN",
                "factor": factor,
            })

    uid = int(user["id"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db_lock, connect() as c:
        ensure_user(c, user)

        # Deduct stake only if the balance is sufficient.
        cur = c.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=? AND balance>=?
        """, (stake, uid, stake))

        if cur.rowcount != 1:
            return jsonify(error="Balance gahaa'aa miti."), 400

        # Payout is credited only from verified winning lines above.
        if payout:
            c.execute("""
                UPDATE users SET balance=balance+? WHERE user_id=?
            """, (payout, uid))

        new_balance = c.execute("""
            SELECT balance FROM users WHERE user_id=?
        """, (uid,)).fetchone()["balance"]

        c.execute("""
            INSERT INTO history(user_id,game,stake,payout,result,created)
            VALUES(?,?,?,?,?,?)
        """, (
            uid,
            "MULTI HOT MEGAWIN",
            stake,
            payout,
            json.dumps({"grid": grid, "wins": wins}),
            now,
        ))

    return jsonify(
        ok=True,
        grid=grid,
        payout=payout,
        balance=new_balance,
        wins=wins,
    )


# =====================================================
# PAYMENT METHODS
# =====================================================

@app.get("/api/payment-methods")
def payment_methods():
    return jsonify(methods=[
        {"name": name, "details": details}
        for name, details in PAYMENT_INFO.items()
    ])


# =====================================================
# CREATE DEPOSIT
# =====================================================

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

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db_lock, connect() as c:
        ensure_user(c, user)
        cur = c.execute("""
            INSERT INTO deposits(
                user_id,username,method,amount,reference,status,created
            )
            VALUES(?,?,?,?,?,'PENDING',?)
        """, (
            int(user["id"]),
            user.get("username", "") or "",
            method,
            amount,
            reference,
            now,
        ))
        deposit_id = cur.lastrowid

    return jsonify(
        ok=True,
        deposit_id=deposit_id,
        status="WAITING_RECEIPT",
        message="Request created. Upload your payment screenshot.",
    )


# =====================================================
# SEND RECEIPT TO TELEGRAM ADMIN
# =====================================================

async def notify_admin_receipt(
    deposit_id,
    uid,
    username,
    method,
    amount,
    reference,
    image_data,
    extension,
):
    if not bot_app or not ADMIN_ID:
        raise RuntimeError("ADMIN_ID or Telegram bot is not configured.")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ APPROVE", callback_data=f"dep:approve:{deposit_id}"
        ),
        InlineKeyboardButton(
            "❌ REJECT", callback_data=f"dep:reject:{deposit_id}"
        ),
    ]])

    caption = (
        f"📸 DEPOSIT RECEIPT #{deposit_id}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"User ID: {uid}\n"
        f"Username: @{username or 'none'}\n"
        f"Method: {method}\n"
        f"Amount: {amount} ETB\n"
        f"Reference: {reference}\n"
        f"Status: PENDING\n\n"
        f"Verify actual payment before approving."
    )

    photo = BytesIO(image_data)
    photo.name = f"deposit_{deposit_id}{extension}"

    await bot_app.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo,
        caption=caption,
        reply_markup=keyboard,
    )


# =====================================================
# UPLOAD RECEIPT
# =====================================================

@app.post("/api/deposit/<int:deposit_id>/receipt")
def upload_deposit_receipt(deposit_id):
    user = authorized()
    if not user:
        return jsonify(
            ok=False, error="Telegram authentication failed."
        ), 401

    proof = request.files.get("receipt")
    if not proof or not proof.filename:
        return jsonify(
            ok=False, error="Choose a screenshot image first."
        ), 400

    accepted = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    ext = accepted.get(proof.mimetype)
    if not ext:
        return jsonify(
            ok=False, error="Choose JPG, PNG or WEBP image."
        ), 400

    try:
        image_data = proof.read()
        if not image_data:
            return jsonify(ok=False, error="Image is empty."), 400
        if len(image_data) > MAX_RECEIPT_SIZE:
            return jsonify(ok=False, error="Image exceeds 7 MB."), 413

        uid = int(user["id"])

        with db_lock, connect() as c:
            row = c.execute("""
                SELECT * FROM deposits WHERE id=? AND user_id=?
            """, (deposit_id, uid)).fetchone()

            if not row:
                return jsonify(
                    ok=False, error="Deposit request not found."
                ), 404
            if row["status"] != "PENDING":
                return jsonify(
                    ok=False, error="Request already reviewed."
                ), 409
            if row["receipt_path"]:
                return jsonify(
                    ok=False,
                    error="Receipt already submitted or processing.",
                ), 409

            cur = c.execute("""
                UPDATE deposits SET receipt_path='SENDING'
                WHERE id=? AND user_id=? AND status='PENDING'
                AND receipt_path IS NULL
            """, (deposit_id, uid))

            if cur.rowcount != 1:
                return jsonify(
                    ok=False, error="Receipt already processing."
                ), 409

            args = (
                uid,
                row["username"] or "",
                row["method"],
                int(row["amount"]),
                row["reference"],
            )

        if not bot_loop or not bot_app or not ADMIN_ID:
            with db_lock, connect() as c:
                c.execute("""
                    UPDATE deposits SET receipt_path=NULL
                    WHERE id=? AND receipt_path='SENDING'
                """, (deposit_id,))
            return jsonify(
                ok=False, error="Bot/Admin not ready. Try again."
            ), 503

        try:
            future = asyncio.run_coroutine_threadsafe(
                notify_admin_receipt(
                    deposit_id,
                    *args,
                    image_data,
                    ext,
                ),
                bot_loop,
            )
            future.result(timeout=45)
        except Exception:
            logging.exception(
                "Admin receipt delivery failed: #%s", deposit_id
            )
            with db_lock, connect() as c:
                c.execute("""
                    UPDATE deposits SET receipt_path=NULL
                    WHERE id=? AND status='PENDING'
                    AND receipt_path='SENDING'
                """, (deposit_id,))
            return jsonify(
                ok=False, error="Receipt sending failed. Please retry."
            ), 502

        with db_lock, connect() as c:
            cur = c.execute("""
                UPDATE deposits SET receipt_path='TELEGRAM_SENT'
                WHERE id=? AND user_id=? AND status='PENDING'
                AND receipt_path='SENDING'
            """, (deposit_id, uid))

            if cur.rowcount != 1:
                logging.error(
                    "Receipt sent to admin but DB finalization failed: #%s",
                    deposit_id,
                )
                return jsonify(
                    ok=False,
                    error="Admin received receipt. Contact support before retrying.",
                ), 500

        logging.info("Deposit receipt delivered to admin: #%s", deposit_id)
        return jsonify(
            ok=True,
            deposit_id=deposit_id,
            status="PENDING_REVIEW",
            message="Receipt sent to admin. Await review.",
        ), 200

    except Exception:
        logging.exception("Deposit receipt upload failed: #%s", deposit_id)
        return jsonify(
            ok=False, error="Upload failed. Please check Render Logs."
        ), 500


# =====================================================
# APPROVE / REJECT DEPOSIT
# =====================================================

async def review_deposit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
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
    status = "APPROVED" if action == "approve" else "REJECTED"

    try:
        with db_lock, connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM deposits WHERE id=?", (deposit_id,)
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

            if action == "approve" and row["receipt_path"] != "TELEGRAM_SENT":
                await query.answer(
                    "Receipt not confirmed. Do not approve.",
                    show_alert=True,
                )
                return

            cur = c.execute("""
                UPDATE deposits
                SET status=?, reviewed=?, reviewed_by=?
                WHERE id=? AND status='PENDING'
            """, (status, now, update.effective_user.id, deposit_id))

            if cur.rowcount != 1:
                await query.answer("Already processed.", show_alert=True)
                return

            if action == "approve":
                credited = c.execute("""
                    UPDATE users SET balance=balance+? WHERE user_id=?
                """, (int(row["amount"]), int(row["user_id"])))

                if credited.rowcount != 1:
                    raise RuntimeError("Deposit owner account missing.")

        # Transaction has committed.

    except Exception:
        logging.exception("Deposit review failed: #%s", deposit_id)
        try:
            await query.answer(
                "Review failed. Check server logs.", show_alert=True
            )
        except Exception:
            pass
        return

    try:
        await query.answer(status)
    except Exception:
        pass

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logging.exception("Could not remove review buttons.")

    try:
        await query.message.reply_text(
            f"Deposit #{deposit_id}: {status}."
        )
    except Exception:
        logging.exception("Could not post review result.")

    try:
        if action == "approve":
            message = (
                f"✅ Deposit #{deposit_id} APPROVED.\n"
                f"{row['amount']} ETB has been credited to your game balance."
            )
        else:
            message = (
                f"❌ Deposit #{deposit_id} REJECTED.\n"
                f"No balance was added."
            )

        await bot_app.bot.send_message(
            chat_id=int(row["user_id"]),
            text=message,
        )
    except Exception:
        logging.exception(
            "Could not notify user about deposit decision."
        )


# =====================================================
# TELEGRAM START
# =====================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
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
            "Demo credits are not cash. "
            "Check local laws and age requirements."
        ),
        reply_markup=keyboard,
    )


async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Use /start to open the game."
    )


# =====================================================
# START BOT
# =====================================================

async def run_bot():
    global bot_app, bot_loop

    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is missing.")
        return

    if not ADMIN_ID:
        logging.warning("ADMIN_ID is not configured.")

    bot_loop = asyncio.get_running_loop()
    bot_app = Application.builder().token(BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("help", help_cmd))
    bot_app.add_handler(CallbackQueryHandler(
        review_deposit,
        pattern=r"^dep:(approve|reject):\d+$",
    ))

    await bot_app.initialize()
    await bot_app.start()

    if bot_app.updater:
        await bot_app.updater.start_polling()

    logging.info("Telegram polling started.")
    await asyncio.Event().wait()


# =====================================================
# RUN
# =====================================================

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
