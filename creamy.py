import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

import aiohttp
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ================= LOGGING SETUP =================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "8768395001:AAEQgiRBFSzYE3lmIv726mOScQTs98QlnmE"  # Replace with token from @BotFather
BASE_URL = "https://creamyverse.com/api"
APP_SECRET = "6bb9bf539edf2ca7c15f801e8c67c7157f3e743b94088f0aeb51a2c8cfa7a062"
HEADERS = {"Content-Type": "application/json", "x-app-secret": APP_SECRET}

LEVEL_GRIDS = {1: (5, 5), 2: (6, 6), 3: (6, 7)}
LEVEL_STARTS = {1: (2, 0), 2: (5, 3), 3: (5, 2)}
DURATION_MS = 6000

TARGET_HOUR = 5
TARGET_MINUTE = 30
TARGET_TZ = "Asia/Kolkata"

# Conversation states
NAME, PHONE, EMAIL, OTP, TRAIT, RUN_CHOICE = range(6)


# ================= TIME / SCHEDULER =================
def ist_now():
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(TARGET_TZ))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def seconds_until_fire():
    now = ist_now()
    target = now.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)
    if target < now - timedelta(seconds=10):
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ================= PATH SOLVER =================
def solve_path(w, h, start, seed, max_steps=300000):
    rng = random.Random(seed)
    sx, sy = start
    visited = [[False] * h for _ in range(w)]
    path = []
    budget = [0]

    def degree(x, y):
        n = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not visited[nx][ny]:
                n += 1
        return n

    def dfs(x, y):
        budget[0] += 1
        if budget[0] > max_steps:
            return False
        visited[x][y] = True
        path.append((x, y))
        if len(path) == w * h:
            return True
        moves = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not visited[nx][ny]:
                moves.append((nx, ny))
        moves.sort(key=lambda cell: (degree(*cell), rng.random()))
        for nx, ny in moves:
            if dfs(nx, ny):
                return True
        visited[x][y] = False
        path.pop()
        return False

    if dfs(sx, sy):
        return [{"x": x, "y": y} for x, y in path]
    return None


# ================= API HELPERS =================
async def api_post(endpoint, payload, token=None):
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{BASE_URL}{endpoint}", json=payload, headers=h, timeout=15) as r:
            try:
                data = await r.json()
            except Exception:
                data = {"_raw": await r.text()}
            return r.status, data


async def api_get(endpoint, token=None, params=None):
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}{endpoint}", headers=h, params=params, timeout=15) as r:
            try:
                data = await r.json()
            except Exception:
                data = {"_raw": await r.text()}
            return r.status, data


# ================= GAME EXECUTION =================
async def execute_game_run(chat_id, token, user_id, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=chat_id, text="🚀 *Starting Game Sequence...*", parse_mode="Markdown")

    for level in (1, 2, 3):
        w, h = LEVEL_GRIDS[level]
        start = LEVEL_STARTS[level]

        await context.bot.send_message(chat_id=chat_id, text=f"🎯 *Level {level}*: Requesting seed...", parse_mode="Markdown")
        await asyncio.sleep(random.uniform(0.5, 1.2))

        code, sr = await api_post("/game/start", {"level": level}, token)
        game_key = sr.get("gameKey")
        seed = sr.get("seed")

        if not game_key:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Level {level} start failed: `{sr}`", parse_mode="Markdown")
            continue

        path = solve_path(w, h, start, seed)
        if not path:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Solver failed to find Hamiltonian path for Level {level}.")
            continue

        # Human-like delay simulation
        play_delay = random.uniform(1.0, 2.0)
        await asyncio.sleep(play_delay)

        code, submit_r = await api_post(
            "/game/submit",
            {
                "duration_ms": DURATION_MS,
                "level": level,
                "gameKey": game_key,
                "solutionPath": path,
            },
            token,
        )

        rank = submit_r.get("rank", "?")
        total = submit_r.get("total", "?")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ *Level {level} Submitted!* \n⏱ Time: `{DURATION_MS}ms` | Rank: `#{rank}/{total}`",
            parse_mode="Markdown",
        )
        if level != 3:
            await asyncio.sleep(random.uniform(1.0, 2.0))

    # Leaderboard Check
    await asyncio.sleep(1.0)
    _, lb = await api_get("/game/leaderboard", token, {"limit": 10, "userId": user_id})
    board = lb.get("board", [])
    caller = lb.get("caller")

    lb_text = "📊 *Leaderboard Top 10:*\n"
    for i, p in enumerate(board[:10], 1):
        lb_text += f"`#{p.get('rank', i)}` {p.get('name')} ({p.get('best_ms', '?')}ms)\n"

    if caller:
        lb_text += f"\n📍 *Your Final Rank:* `#{caller.get('rank', '?')}` ({caller.get('best_ms', '?')}ms)"

    await context.bot.send_message(chat_id=chat_id, text=lb_text, parse_mode="Markdown")


# ================= HANDLERS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to Creamyverse Automation Bot!*\n\nPlease send your *Full Name*:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("📱 Enter your *Phone Number* (with country code if needed):", parse_mode="Markdown")
    return PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("✉️ Enter your *Email Address*:", parse_mode="Markdown")
    return EMAIL


async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["email"] = update.message.text.strip()
    await update.message.reply_text("🔄 Sending OTP request to server...")

    code, resp = await api_post(
        "/auth/register",
        {
            "name": context.user_data["name"],
            "phone": context.user_data["phone"],
            "email": context.user_data["email"],
        },
    )

    await update.message.reply_text(f"📩 OTP dispatched. Please reply with the *OTP Code* received:", parse_mode="Markdown")
    return OTP


async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    phone = context.user_data["phone"]

    await update.message.reply_text("🔑 Verifying OTP...")
    code, resp = await api_post("/auth/verify-otp", {"phone": phone, "code": otp})

    token = resp.get("token")
    if not token:
        await update.message.reply_text(f"❌ Verification failed: `{resp.get('message', 'Invalid OTP')}`. Type /start to retry.", parse_mode="Markdown")
        return ConversationHandler.END

    context.user_data["token"] = token
    user = resp.get("user", {})
    context.user_data["user_id"] = user.get("id")

    traits = [["music_paglu", "gamer"], ["chilled", "coder"]]
    reply_markup = ReplyKeyboardMarkup(traits, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(f"✅ Logged in as *{user.get('name')}*!\n\n🎭 Select your trait:", reply_markup=reply_markup, parse_mode="Markdown")
    return TRAIT


async def handle_trait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trait = update.message.text.strip()
    token = context.user_data["token"]

    await api_post("/auth/set-trait", {"trait": trait}, token)
    context.user_data["trait"] = trait

    choices = [["⚡ Run Now", "⏰ Schedule for 05:30 AM IST"]]
    reply_markup = ReplyKeyboardMarkup(choices, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(f"Trait set to *{trait}*.\n\nChoose execution mode:", reply_markup=reply_markup, parse_mode="Markdown")
    return RUN_CHOICE


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    chat_id = update.effective_chat.id
    token = context.user_data["token"]
    user_id = context.user_data["user_id"]

    if "Run Now" in choice:
        await update.message.reply_text("⚡ Starting immediately...", reply_markup=ReplyKeyboardRemove())
        asyncio.create_task(execute_game_run(chat_id, token, user_id, context))
    else:
        rem_sec = seconds_until_fire()
        hrs, mins = divmod(int(rem_sec) // 60, 60)
        await update.message.reply_text(
            f"⏰ Scheduled! Waiting `{hrs}h {mins}m` until *05:30:00 AM IST* to fire.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown",
        )

        async def delayed_fire():
            wait = seconds_until_fire()
            if wait > 0:
                await asyncio.sleep(wait)
            await execute_game_run(chat_id, token, user_id, context)

        asyncio.create_task(delayed_fire())

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ================= MAIN ENTRY =================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_cmd)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp)],
            TRAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_trait)],
            RUN_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_choice)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    print("Bot is up and polling...")
    app.run_polling()


if __name__ == "__main__":
    main()