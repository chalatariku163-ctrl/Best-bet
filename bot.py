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
            "🎯 *BEST BET*\n\n"
            "Baga nagaan dhuftan!\n"
            "Mee menu keessaa filannoo kee godhi.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # KENO FAST
    # =====================================================

    elif query.data == "keno_fast":

        await query.edit_message_text(
            "⚡ *KENO FAST*\n\n"
            "KENO FAST menu baname.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL MAIN MENU
    # =====================================================

    elif query.data == "football":

        await query.edit_message_text(
            "⚽ *FOOTBALL*\n\n"
            "Filannoo barbaadde keessaa tokko filadhu:",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL MATCHES
    # =====================================================

    elif query.data == "football_matches":

        await query.edit_message_text(
            "📅 *MATCHES*\n\n"
            "Taphoota football dhufan asitti ilaalla.\n\n"
            "🔄 Match data yeroo itti aanu keessatti "
            "itti dabalama.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL LIVE
    # =====================================================

    elif query.data == "football_live":

        await query.edit_message_text(
            "🔴 *LIVE*\n\n"
            "Taphoota yeroo ammaa jiran asitti ilaalla.\n\n"
            "🔄 Live data yeroo itti aanu keessatti "
            "itti dabalama.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL LEAGUES
    # =====================================================

    elif query.data == "football_leagues":

        await query.edit_message_text(
            "🏆 *LEAGUES*\n\n"
            "Leagues football adda addaa asitti ilaalla.\n\n"
            "⚽ Premier League\n"
            "⚽ Champions League\n"
            "⚽ La Liga\n"
            "⚽ Serie A\n"
            "⚽ Bundesliga",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL STANDINGS
    # =====================================================

    elif query.data == "football_standings":

        await query.edit_message_text(
            "📊 *STANDINGS*\n\n"
            "Gabatee sadarkaa league asitti ilaalla.\n\n"
            "🔄 Standings data yeroo itti aanu keessatti "
            "itti dabalama.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # FOOTBALL TEAMS
    # =====================================================

    elif query.data == "football_teams":

        await query.edit_message_text(
            "🔎 *TEAMS*\n\n"
            "Gareewwan football asitti barbaaduu "
            "fi ilaaluun ni danda'ama.\n\n"
            "🔄 Team data yeroo itti aanu keessatti "
            "itti dabalama.",
            reply_markup=football_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BACK TO MAIN MENU
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
            "Deposit system yeroo ammaa qophaa'aa jira.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # BALANCE
    # =====================================================

    elif query.data == "balance":

        await query.edit_message_text(
            "💳 *BALANCE*\n\n"
            "Balance kee ilaaluuf system account "
            "barbaachisa.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WITHDRAW
    # =====================================================

    elif query.data == "withdraw":

        await query.edit_message_text(
            "💸 *WITHDRAW*\n\n"
            "Withdrawal system qophaa'aa jira.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HISTORY
    # =====================================================

    elif query.data == "history":

        await query.edit_message_text(
            "📜 *MY HISTORY*\n\n"
            "History account kee as keessatti argita.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # WINNERS
    # =====================================================

    elif query.data == "winners":

        await query.edit_message_text(
            "🏆 *WINNERS*\n\n"
            "Winners yeroo dhiyoo as keessatti mul'atu.",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )


    # =====================================================
    # HOW TO PLAY
    # =====================================================

    elif query.data == "how_to_play":

        await query.edit_message_text(
            "ℹ️ *HOW TO PLAY*\n\n"
            "1️⃣ BEST BET filadhu.\n"
            "2️⃣ KENO FAST ykn FOOTBALL filadhu.\n"
            "3️⃣ Menu keessaa filannoo barbaadde godhi.\n"
            "4️⃣ Odeeffannoo taphaa ilaali.",
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


    # /start

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    # Buttons

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
