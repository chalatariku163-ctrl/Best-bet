import os, json, hmac, hashlib, sqlite3, threading, logging, random, urllib.parse, asyncio
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
WEBAPP_URL=os.getenv("WEBAPP_URL","").strip().rstrip("/")
ADMIN_ID=int(os.getenv("ADMIN_ID","0") or 0)
DATABASE_PATH=os.getenv("DATABASE_PATH","casino.db")
# Fill these later; do not publish private financial details in public source.
PAYMENT_INFO={
 "Telebirr": os.getenv("PAY_TELEBIRR","Payment details will be announced"),
 "CBE": os.getenv("PAY_CBE","Payment details will be announced"),
 "Awash": os.getenv("PAY_AWASH","Payment details will be announced"),
}
BONUS=20
app=Flask(__name__)
db_lock=threading.Lock()

def connect():
 c=sqlite3.connect(DATABASE_PATH,timeout=25)
 c.row_factory=sqlite3.Row
 return c

def init_db():
 with connect() as c:
  c.execute("""CREATE TABLE IF NOT EXISTS users(
   user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER NOT NULL DEFAULT 20,
   bonus_given INTEGER NOT NULL DEFAULT 1)""")
  c.execute("""CREATE TABLE IF NOT EXISTS history(
   id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,game TEXT,stake INTEGER,
   payout INTEGER,result TEXT,created TEXT)""")
  c.execute("""CREATE TABLE IF NOT EXISTS deposits(
   id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,method TEXT,
   amount INTEGER,reference TEXT,status TEXT NOT NULL DEFAULT 'PENDING',
   created TEXT,reviewed TEXT,reviewed_by INTEGER)""")
  # Upgrade an existing users table without resetting existing balances.
  cols={r["name"] for r in c.execute("PRAGMA table_info(users)")}
  if "bonus_given" not in cols:
   c.execute("ALTER TABLE users ADD COLUMN bonus_given INTEGER NOT NULL DEFAULT 1")
  # Repair legacy accounts that have no credits at all: grant the welcome
  # bonus once. Accounts with a positive balance are left untouched.
  c.execute("UPDATE users SET balance=?, bonus_given=1 WHERE balance<=0",
            (BONUS,))
init_db()

def verify_init_data(raw):
 if not BOT_TOKEN or not raw:return None
 try:
  pairs=dict(urllib.parse.parse_qsl(raw,keep_blank_values=True))
  got=pairs.pop("hash",None)
  if not got:return None
  check="\n".join(f"{k}={v}" for k,v in sorted(pairs.items()))
  secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
  want=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(want,got):return None
  user=json.loads(pairs["user"])
  age=datetime.now(timezone.utc).timestamp()-int(pairs["auth_date"])
  if age<0 or age>86400:return None
  return user
 except Exception:
  logging.exception("WebApp authentication failed");return None

def authorized():
 u=verify_init_data(request.headers.get("X-Telegram-Init-Data",""))
 return u if u and u.get("id") else None

def ensure_user(c,u):
 uid=int(u["id"]); name=u.get("username","")
 # New accounts receive the one-time 20 demo-credit bonus.
 c.execute("INSERT OR IGNORE INTO users(user_id,username,balance,bonus_given) VALUES(?,?,?,1)",(uid,name,BONUS))
 c.execute("UPDATE users SET username=? WHERE user_id=?",(name,uid))
 # Legacy zero-balance account recovery; does not affect accounts with credits.
 c.execute("UPDATE users SET balance=?, bonus_given=1 WHERE user_id=? AND balance<=0",
           (BONUS,uid))
 return c.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone()

@app.get("/")
def index():return send_from_directory(".","index.html")
@app.get("/health")
def health():return jsonify(ok=True,service="Multi Hot Megawin")

@app.get("/api/me")
def me():
 u=authorized()
 if not u:return jsonify(error="Open this app from its Telegram bot."),401
 with db_lock,connect() as c:
  row=ensure_user(c,u)
  hist=c.execute("SELECT game,stake,payout,result,created FROM history WHERE user_id=? ORDER BY id DESC LIMIT 15",(int(u["id"]),)).fetchall()
  deps=c.execute("SELECT id,method,amount,status,created FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 10",(int(u["id"]),)).fetchall()
 return jsonify(user={"id":int(u["id"]),"name":u.get("first_name","Player")},
  balance=row["balance"],bonus=BONUS,history=[dict(x) for x in hist],deposits=[dict(x) for x in deps])

@app.get("/api/payment-methods")
def payment_methods():
 return jsonify(methods=[{"name":k,"details":v} for k,v in PAYMENT_INFO.items()])

@app.post("/api/deposit")
def deposit():
 u=authorized()
 if not u:return jsonify(error="Telegram authentication failed."),401
 b=request.get_json(silent=True) or {}
 method=str(b.get("method",""))
 reference=str(b.get("reference","")).strip()[:240]
 try:amount=int(b.get("amount",0))
 except (TypeError,ValueError):return jsonify(error="Amount invalid."),400
 if method not in PAYMENT_INFO:return jsonify(error="Select a payment method."),400
 if amount<1 or amount>100000:return jsonify(error="Amount must be 1–100000."),400
 if len(reference)<4:return jsonify(error="Enter transaction reference or receipt details."),400
 uid=int(u["id"]); now=datetime.now(timezone.utc).isoformat(timespec="seconds")
 with db_lock,connect() as c:
  ensure_user(c,u)
  cur=c.execute("INSERT INTO deposits(user_id,username,method,amount,reference,status,created) VALUES(?,?,?,?,?,'PENDING',?)",
   (uid,u.get("username",""),method,amount,reference,now))
  did=cur.lastrowid
 # Notify admin via bot event loop handoff; notification failure does not erase request.
 try:
  asyncio.run_coroutine_threadsafe(notify_admin(did,uid,u.get("username",""),method,amount,reference), bot_loop)
 except Exception:logging.exception("Could not notify admin")
 return jsonify(ok=True,deposit_id=did,status="PENDING",message="Request received. Balance changes only after admin review.")

bot_app=None
bot_loop=None
async def notify_admin(did,uid,username,method,amount,reference):
 if not bot_app or not ADMIN_ID:return
 keyboard=InlineKeyboardMarkup([[
  InlineKeyboardButton("✅ APPROVE",callback_data=f"dep:approve:{did}"),
  InlineKeyboardButton("❌ REJECT",callback_data=f"dep:reject:{did}")
 ]])
 await bot_app.bot.send_message(ADMIN_ID,
  f"💳 Deposit request #{did}\nUser: {uid} (@{username or 'none'})\nMethod: {method}\nAmount: {amount} ETB\nReference: {reference}\nStatus: PENDING",
  reply_markup=keyboard)

async def review_deposit(update:Update,context:ContextTypes.DEFAULT_TYPE):
 q=update.callback_query
 if not q:return
 if not ADMIN_ID or not update.effective_user or update.effective_user.id!=ADMIN_ID:
  await q.answer("Not authorized.",show_alert=True);return
 try:
  _,action,did_s=q.data.split(":");did=int(did_s)
 except Exception:
  await q.answer("Invalid request.");return
 if action not in ("approve","reject"):
  await q.answer("Invalid action.");return
 now=datetime.now(timezone.utc).isoformat(timespec="seconds")
 with db_lock,connect() as c:
  row=c.execute("SELECT * FROM deposits WHERE id=?",(did,)).fetchone()
  if not row:
   await q.answer("Request not found.",show_alert=True);return
  if row["status"]!="PENDING":
   await q.answer("Already reviewed: "+row["status"],show_alert=True);return
  status="APPROVED" if action=="approve" else "REJECTED"
  # Transaction is atomic: a request can only be credited once.
  cur=c.execute("UPDATE deposits SET status=?,reviewed=?,reviewed_by=? WHERE id=? AND status='PENDING'",
    (status,now,update.effective_user.id,did))
  if cur.rowcount!=1:
   await q.answer("Already processed.",show_alert=True);return
  if action=="approve":
   c.execute("UPDATE users SET balance=balance+? WHERE user_id=?",(row["amount"],row["user_id"]))
 await q.answer(status)
 try:await q.edit_message_reply_markup(reply_markup=None)
 except Exception:pass
 await q.message.reply_text(f"Deposit #{did}: {status}.")

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
 if not WEBAPP_URL:
  await update.effective_message.reply_text("WEBAPP_URL is not configured.");return
 kb=InlineKeyboardMarkup([[InlineKeyboardButton("🎰 OPEN MULTI HOT MEGAWIN",web_app=WebAppInfo(url=WEBAPP_URL))]])
 await update.effective_message.reply_text(
  "🎰 MULTI HOT MEGAWIN\n\nNew account: one-time 20 demo-credit bonus.\nDeposits are pending until admin reviews them.\nPlease check local laws and age requirements.",
  reply_markup=kb)
async def help_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
 await update.effective_message.reply_text("Use /start to open the game.")
async def run_bot():
 global bot_app,bot_loop
 if not BOT_TOKEN:
  logging.error("BOT_TOKEN is missing.");return
 bot_loop=asyncio.get_running_loop()
 bot_app=Application.builder().token(BOT_TOKEN).build()
 bot_app.add_handler(CommandHandler("start",start))
 bot_app.add_handler(CommandHandler("help",help_cmd))
 bot_app.add_handler(CallbackQueryHandler(review_deposit,pattern=r"^dep:(approve|reject):\d+$"))
 await bot_app.initialize();await bot_app.start();await bot_app.updater.start_polling()
 logging.info("Telegram polling started.")
 await asyncio.Event().wait()

if __name__=="__main__":
 threading.Thread(target=lambda:asyncio.run(run_bot()),daemon=True).start()
 app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
