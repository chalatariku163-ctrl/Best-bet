import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

BOT_TOKEN = os.getenv("BOT_TOKEN")


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🎯 BEST BET",
                callback_data="best_bet"
            ),
        ],

        [
            InlineKeyboardButton(
                "⚡ KENO FAST",
                callback_data="keno_fast"
            ),
            InlineKeyboardButton(
                "⚽ FOOTBALL",
                callback_data="football"
            ),
        ],

        [
            InlineKeyboardButton(
                "💰 DEPOSIT",
                callback_data="deposit"
            ),
            InlineKeyboardButton(
                "💳 BALANCE",
                callback_data="balance"
            ),
        ],

        [
            InlineKeyboardButton(
                "💸 WITHDRAW",
                callback_data="withdraw"
            ),
            InlineKeyboardButton(
                "📜 MY HISTORY",
                callback_data="history"
            ),
        ],

        [
            InlineKeyboardButton(
                "🏆 WINNERS",
                callback_data="winners"
            ),
            InlineKeyboardButton(
                "ℹ️ HOW TO PLAY",
                callback_data="how_to_play"
            ),
        ],

        [
            InlineKeyboardButton(
                "📞 SUPPORT",
                callback_data="support"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# FOOTBALL MENU
# =========================================================

def football_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "📅 MATCHES",
                callback_data="football_matches"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔴 LIVE",
                callback_data="football_live"
            ),
        ],

        [
            InlineKeyboardButton(
                "🏆 LEAGUES",
                callback_data="football_leagues"
            ),
        ],

        [
            InlineKeyboardButton(
                "📊 STANDINGS",
                callback_data="football_standings"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔎 TEAMS",
                callback_data="football_teams"
            ),
        ],

        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="back_main"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# MATCHES MENU
# =========================================================

def matches_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "⚽ Arsenal vs Chelsea",
                callback_data="match_arsenal_chelsea"
            ),
        ],

        [
            InlineKeyboardButton(
                "⚽ Barcelona vs Real Madrid",
                callback_data="match_barca_real"
            ),
        ],

        [
            InlineKeyboardButton(
                "⚽ Man City vs Liverpool",
                callback_data="match_city_liverpool"
            ),
        ],

        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="football"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# PREDICTION MENU
# =========================================================

def prediction_menu(match_id):

    keyboard = [

        [
            InlineKeyboardButton(
                "1️⃣ HOME",
                callback_data=f"prediction_home_{match_id}"
            ),

            InlineKeyboardButton(
                "❌ DRAW",
                callback_data=f"prediction_draw_{match_id}"
            ),

            InlineKeyboardButton(
                "2️⃣ AWAY",
                callback_data=f"prediction_away_{match_id}"
            ),
        ],

        [
            InlineKeyboardButton(
                "⬅️ BACK",
                callback_data="football_matches"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    text = (
        f"👋 Baga nagaan dhuftan, "
        f"{user.first_name}!\n\n"
        "🎯 *BEST BET*\n\n"
        "Menu armaan gadii keessaa filannoo kee godhi."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()


    # =====================================================
    # BEST BET
    # =====================================================

    if query.data == "best_bet":

        await query.edit_message_text(
            "🎯 *BEST BET*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO FAST
    # =====================================================

    elif query.data == "keno_fast":

        await query.edit_message_text(
            "⚡ *KENO FAST*",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL
    # =====================================================

    elif query.data == "football":

        await query.edit_message_text(
            " ",
            reply_markup=football_menu()
        )


    # =====================================================
    # MATCHES
    # =====================================================

    elif query.data == "football_matches":

        await query.edit_message_text(
            "📅 *MATCHES*\n\n"
            "Match tokko filadhu:",
            reply_markup=matches_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # ARSENAL VS CHELSEA
    # =====================================================

    elif query.data == "match_arsenal_chelsea":

        await query.edit_message_text(
            "⚽ *ARSENAL vs CHELSEA*\n\n"
            "🔮 Prediction kee filadhu:",
            reply_markup=prediction_menu(
                "arsenal_chelsea"
            ),
            parse_mode="Markdown"
        )


    # =====================================================
    # BARCELONA VS REAL MADRID
    # =====================================================

    elif query.data == "match_barca_real":

        await query.edit_message_text(
            "⚽ *BARCELONA vs REAL MADRID*\n\n"
            "🔮 Prediction kee filadhu:",
            reply_markup=prediction_menu(
                "barca_real"
            ),
            parse_mode="Markdown"
        )


    # =====================================================
    # MAN CITY VS LIVERPOOL
    # =====================================================

    elif query.data == "match_city_liverpool":

        await query.edit_message_text(
            "⚽ *MAN CITY vs LIVERPOOL*\n\n"
            "🔮 Prediction kee filadhu:",
            reply_markup=prediction_menu(
                "city_liverpool"
            ),
            parse_mode="Markdown"
        )


    # =====================================================
    # PREDICTION HOME
    # =====================================================

    elif query.data.startswith("prediction_home_"):

        await query.edit_message_text(
            "🔮 *PREDICTION*\n\n"
            "1️⃣ HOME filatameera.\n\n"
            "🧪 Kun demo prediction qofa.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # PREDICTION DRAW
    # =====================================================

    elif query.data.startswith("prediction_draw_"):

        await query.edit_message_text(
            "🔮 *PREDICTION*\n\n"
            "❌ DRAW filatameera.\n\n"
            "🧪 Kun demo prediction qofa.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # PREDICTION AWAY
    # =====================================================

    elif query.data.startswith("prediction_away_"):

        await query.edit_message_text(
            "🔮 *PREDICTION*\n\n"
            "2️⃣ AWAY filatameera.\n\n"
            "🧪 Kun demo prediction qofa.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # LIVE
    # =====================================================

    elif query.data == "football_live":

        await query.edit_message_text(
            "🔴 *LIVE*\n\n"
            "Live football data asitti mul'ata.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # LEAGUES
    # =====================================================

    elif query.data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "⚽ Premier League\n"
            "⚽ Champions League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # STANDINGS
    # =====================================================

    elif query.data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "Gabatee sadarkaa league asitti ilaalla.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # TEAMS
    # =====================================================

    elif query.data == "football_teams":

        await query.edit_message_text(
            "🔎 *TEAMS*\n\n"
            "Gareewwan football asitti ilaalla.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BACK TO MAIN
    # =====================================================

    elif query.data == "back_main":

        await query.edit_message_text(
            "🎯 *BEST BET*\n\n"
            "Menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # DEPOSIT
    # =====================================================

    elif query.data == "deposit":

        await query.edit_message_text(
            "💰 *DEPOSIT*\n\n"
            "Deposit system yeroo ammaa hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            "Balance system yeroo ammaa hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WITHDRAW
    # =====================================================

    elif query.data == "withdraw":

        await query.edit_message_text(
            "💸 *WITHDRAW*\n\n"
            "Withdrawal system yeroo ammaa hin jiru.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        await query.edit_message_text(
            "📜 *MY HISTORY*\n\n"
            "Prediction history demo as keessatti "
            "mul'ata.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WINNERS
    # =====================================================

    elif query.data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Demo winners as keessatti mul'atu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif query.data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ FOOTBALL filadhu.\n"
            "2️⃣ MATCHES bani.\n"
            "3️⃣ Match filadhu.\n"
            "4️⃣ HOME, DRAW ykn AWAY keessaa "
            "prediction filadhu.\n\n"
            "🧪 Kun demo qofa; qarshii dhugaa hin qabu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # SUPPORT
    # =====================================================

    elif query.data == "support":

        await query.edit_message_text(
            "📞 *SUPPORT*\n\n"
            "Yoo gargaarsa barbaadde, "
            "admin/support qunnami.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN environment variable hin jiru."
        )

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )


    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )


    print(
        "🤖 BEST BET BOT started..."
    )


    app.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
