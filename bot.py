import os, json, hmac, hashlib, sqlite3, threading, logging, random, urllib.parse
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("BOT_TOKEN","").strip()
WEBAPP_URL=os.getenv("WEBAPP_URL","").strip().rstrip("/")
DB=os.getenv("DATABASE_PATH","casino.db")
app=Flask(__name__, static_folder=".")
db_lock=threading.Lock()

def connect():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    return c
def init_db():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER NOT NULL DEFAULT 1000)""")
        c.execute("""CREATE TABLE IF NOT EXISTS history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,game TEXT,stake INTEGER,
          payout INTEGER,result TEXT,created TEXT)""")
init_db()

def verify_init_data(raw):
    if not TOKEN or not raw: return None
    try:
        pairs=dict(urllib.parse.parse_qsl(raw,keep_blank_values=True))
        received=pairs.pop("hash",None)
        if not received: return None
        check="\n".join(f"{k}={v}" for k,v in sorted(pairs.items()))
        secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
        calculated=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated,received): return None
        user=json.loads(pairs["user"])
        auth_date=int(pairs["auth_date"])
        if datetime.now(timezone.utc).timestamp()-auth_date>86400: return None
        return user
    except Exception:
        return None

def authorized():
    raw=request.headers.get("X-Telegram-Init-Data","")
    user=verify_init_data(raw)
    if not user or not user.get("id"): return None
    return user

@app.get("/")
def index(): return send_from_directory(".", "index.html")
@app.get("/health")
def health(): return {"ok":True}

@app.get("/api/me")
def me():
    user=authorized()
    if not user: return jsonify(error="Open this app from its Telegram bot."),401
    uid=int(user["id"])
    with db_lock,connect() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,username,balance) VALUES(?,?,1000)",
                  (uid,user.get("username","")))
        c.execute("UPDATE users SET username=? WHERE user_id=?",(user.get("username",""),uid))
        row=c.execute("SELECT balance FROM users WHERE user_id=?",(uid,)).fetchone()
        recent=c.execute("SELECT game,stake,payout,result,created FROM history WHERE user_id=? ORDER BY id DESC LIMIT 15",(uid,)).fetchall()
    return jsonify(user={"id":uid,"name":user.get("first_name","Player")},
                   balance=row["balance"],history=[dict(r) for r in recent])

@app.post("/api/play")
def play():
    user=authorized()
    if not user: return jsonify(error="Authentication failed. Open via Telegram."),401
    body=request.get_json(silent=True) or {}
    game=body.get("game")
    try: stake=int(body.get("stake",0))
    except: return jsonify(error="Invalid stake."),400
    if game not in ("slots","blackjack","roulette") or stake<1 or stake>100:
        return jsonify(error="Choose a valid game and stake (1–100 demo points)."),400
    uid=int(user["id"])
    # All results and balance updates happen server-side. Demo points only.
    if game=="slots":
        symbols=["🍒","🍋","🔔","⭐","7️⃣"]
        reels=[random.choice(symbols) for _ in range(3)]
        payout=stake*10 if reels[0]==reels[1]==reels[2] else (stake*2 if len(set(reels))==2 else 0)
        result=" ".join(reels)
    elif game=="roulette":
        number=random.randint(0,36)
        pick=body.get("pick")
        if pick not in ("red","black","green"): return jsonify(error="Pick red, black, or green."),400
        reds={1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
        color="green" if number==0 else ("red" if number in reds else "black")
        payout=stake*14 if color=="green" and pick=="green" else (stake*2 if color==pick else 0)
        result=f"{number} ({color}); you picked {pick}"
    else:
        # Simple blackjack-inspired single round vs dealer, simulated for demo only.
        deck=[2,3,4,5,6,7,8,9,10,10,10,11]
        player=random.choice(deck)+random.choice(deck)
        dealer=random.choice(deck)+random.choice(deck)
        payout=stake*2 if player>dealer else (stake if player==dealer else 0)
        result=f"You: {player} | Dealer: {dealer}"
    with db_lock,connect() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,username,balance) VALUES(?,?,1000)",
                  (uid,user.get("username","")))
        row=c.execute("SELECT balance FROM users WHERE user_id=?",(uid,)).fetchone()
        if row["balance"]<stake: return jsonify(error="Not enough demo points."),400
        balance=row["balance"]-stake+payout
        c.execute("UPDATE users SET balance=? WHERE user_id=?",(balance,uid))
        c.execute("INSERT INTO history(user_id,game,stake,payout,result,created) VALUES(?,?,?,?,?,?)",
                  (uid,game,stake,payout,result,datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return jsonify(balance=balance,stake=stake,payout=payout,result=result,net=payout-stake)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL:
        await update.message.reply_text("Bot is installed, but WEBAPP_URL is not configured in Render.")
        return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🎰 PLAY BEST BET CASINO",web_app=WebAppInfo(url=WEBAPP_URL))]])
    await update.message.reply_text(
      "Welcome to BEST BET CASINO! 🎰\nDemo points only — no real-money deposits or withdrawals.\nTap below to play.",
      reply_markup=kb)
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /start to open BEST BET CASINO.")

def run_bot():
    if not TOKEN:
        logging.error("BOT_TOKEN is missing; web server will still start.")
        return
    async def runner():
        tg=Application.builder().token(TOKEN).build()
        tg.add_handler(CommandHandler("start",start))
        tg.add_handler(CommandHandler("help",help_cmd))
        await tg.initialize()
        await tg.start()
        await tg.updater.start_polling()
        logging.info("Telegram polling started")
        # Keep service process alive
        import asyncio
        while True: await asyncio.sleep(3600)
    import asyncio
    asyncio.run(runner())

if __name__=="__main__":
    threading.Thread(target=run_bot,daemon=True).start()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
