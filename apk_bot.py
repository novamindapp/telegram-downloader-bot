#!/usr/bin/env python3
"""
All Apk Downloader -- @BotsFamilyApkBot  (v1)
Channel: @BotsFamilyOfficial (t.me/BotsFamilyOfficial)

v1 scope ONLY:
  * User sends an app name (e.g. WhatsApp, Facebook Lite)
  * Bot searches APKPure, shows the top results (name, package)
  * User picks one, bot gives the APK download link / app page
  * BEFORE any download works, the user MUST be a member of
    @BotsFamilyOfficial. Checked on /start and on every search and
    every pick. If not joined: Join button + "I joined, check again"
    button. The pending search / pick is stored per user, so after
    joining the request continues without retyping.

  * NO YouTube downloads, NO YouTube search, NO AI chat in v1.

IMPORTANT:
  * The bot MUST be an Admin of @BotsFamilyOfficial, otherwise
    Telegram will not reliably tell it who joined.
  * The membership check is exactly like the TikTok bot's
    (@tiktok_insta_dl_2026_bot): getChatMember, True only for
    member/administrator/creator, and if Telegram itself fails
    the check it returns True (fail open) so the user is not
    blocked -- the user asked for it to work like that bot.
  * BOT_TOKEN comes ONLY from the environment (FadeHost env var or
    a local .env file). It is NEVER in this file, NEVER in logs.

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then put your BOT_TOKEN in .env
  python bot.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from apkpure import (
    ApkTooBigError,
    download_apk_file,
    get_app_details,
    search_apkpure,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bots-family-apk-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FORCE_CHANNEL = (
    os.getenv("FORCE_CHANNEL", "BotsFamilyOfficial").strip().lstrip("@")
)

# In-memory state, per user id:
#   {"type": "search", "query": "..."}  -- search waiting for a join
#   {"type": "pick", "app": {...}}      -- picked app waiting for a join
_pending: dict[int, dict] = {}
# Last search results, per user id (for the pick buttons).
_results: dict[int, list[dict]] = {}
# Last picked app details, per user id (for the download button).
_details: dict[int, dict] = {}

WELCOME = (
    "👋 Welcome to All Apk Downloader\n\n"
    "Send me the name of an app, for example:\n"
    "• WhatsApp\n"
    "• Facebook Lite\n"
    "• TikTok\n\n"
    "I will search APKPure and send you the APK file here in this chat.\n\n"
    "⚠️ First, you must join our channel:\n"
    f"https://t.me/{FORCE_CHANNEL}"
)

HELP_TEXT = (
    "How to use this bot:\n\n"
    "1. Join our channel first (required):\n"
    f"   https://t.me/{FORCE_CHANNEL}\n"
    "2. Send me the name of the app you want\n"
    "3. Pick the right app from the list\n"
    "4. Tap 'Download APK' to get the APK file here in this chat\n\n"
    "The APK comes from APKPure, not from the Play Store -- "
    "the Play Store does not give APK files directly."
)


async def is_channel_member(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    """If FORCE_CHANNEL is set, check that the user joined the channel.

    Same behaviour as the TikTok bot (@tiktok_insta_dl_2026_bot):
    True only for member/administrator/creator, and if Telegram
    itself fails the lookup, return True so the user is not
    blocked.
    """
    if not FORCE_CHANNEL:
        return True
    try:
        member = await context.bot.get_chat_member(
            f"@{FORCE_CHANNEL}", user_id
        )
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("check channel failed: %s", e)
        return True  # If Telegram itself fails, do not block the user


def join_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if FORCE_CHANNEL:
        buttons.append(
            [
                InlineKeyboardButton(
                    "📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL}"
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                "✅ I joined, check again", callback_data="join_ok"
            )
        ]
    )
    return InlineKeyboardMarkup(buttons)


async def send_join_prompt(message, user_id: int, pending: dict):
    """Store the pending request and ask the user to join the channel."""
    _pending[user_id] = pending
    await message.reply_text(
        "⚠️ Before you can download, please join our channel first:\n"
        f"https://t.me/{FORCE_CHANNEL}\n\n"
        "Then tap 'I joined, check again' below.",
        reply_markup=join_keyboard(),
    )


async def do_search(message, user_id: int, query: str):
    """Run the APKPure search and show the top results as buttons."""
    status = await message.reply_text(f"🔍 Searching APKPure for '{query}'...")
    try:
        results = await asyncio.to_thread(search_apkpure, query, 5)
    except Exception:
        logger.exception("APKPure search failed for %r", query)
        await status.edit_text(
            "I could not reach APKPure right now. Please try again in a minute."
        )
        return

    if not results:
        await status.edit_text(
            f"No apps found for '{query}' on APKPure.\n"
            "Check the spelling, or try another name."
        )
        return

    _results[user_id] = results
    _pending.pop(user_id, None)

    lines = [f"Found on APKPure for '{query}':\n"]
    buttons = []
    for i, app in enumerate(results, start=1):
        lines.append(f"{i}. {app['name']}  ({app['package']})")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{i}. {app['name']}", callback_data=f"pick:{i - 1}"
                )
            ]
        )
    lines.append("\nTap the right app 👇")
    await status.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
    )


async def do_pick(message, user_id: int, app: dict):
    """Fetch the app details and offer the APK file download."""
    status = await message.reply_text(
        f"📦 Getting {app['name']} from APKPure..."
    )
    try:
        details = await asyncio.to_thread(
            get_app_details, app["slug"], app["package"]
        )
    except Exception:
        logger.exception("APKPure details failed for %s", app["package"])
        await status.edit_text(
            "I could not open that app on APKPure right now.\n"
            "Try again, or open it yourself:\n"
            f"{app['detail_url']}"
        )
        return

    _pending.pop(user_id, None)

    lines = [f"📱 {details['name']}"]
    if details.get("version"):
        lines.append(f"Version: {details['version']}")
    if details.get("size"):
        lines.append(f"Size: {details['size']}")
    lines.append(f"Package: {details['package']}")
    lines.append("")
    lines.append("Tap 'Download APK' below and I will send you the APK file here in this chat.")

    _details[user_id] = details
    buttons = [
        [
            InlineKeyboardButton(
                "⬇️ Download APK", callback_data="dl"
            )
        ],
        [
            InlineKeyboardButton(
                "📄 APKPure page", url=details["detail_url"]
            )
        ],
    ]
    await status.edit_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
    )


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


async def do_download(message, user_id: int, details: dict):
    """Download the APK and send the FILE in this chat.

    Files over 48 MB cannot be sent by a Telegram bot (50 MB limit):
    in that case say so plainly and give the download link instead.
    On any download failure, say so and give the link as fallback.
    The temp file is always deleted after sending.
    """
    status = await message.reply_text(
        f"⬇️ Downloading {details['name']}... please wait."
    )
    path = None
    try:
        result = await asyncio.to_thread(
            download_apk_file,
            details["package"],
            details.get("slug", ""),
            details.get("version", ""),
        )
        path = result["path"]
    except ApkTooBigError as e:
        logger.warning("APK too big for %s: %s bytes", details["package"], e.size_bytes)
        await status.edit_text(
            f"⚠️ {details['name']} is too big for Telegram "
            f"({_fmt_size(e.size_bytes)} -- bots can only send files up to 50 MB).\n\n"
            "You can still download it here:\n"
            f"{details['download_url']}"
        )
        return
    except Exception:
        logger.exception("APK download failed for %s", details["package"])
        await status.edit_text(
            "I could not download that APK right now. Please try again in a minute.\n\n"
            "Or download it yourself here:\n"
            f"{details['download_url']}"
        )
        return

    try:
        caption_lines = [f"📱 {details['name']}"]
        if details.get("version"):
            caption_lines.append(f"Version: {details['version']}")
        caption_lines.append(f"Size: {_fmt_size(result['size'])}")
        caption_lines.append(f"Package: {details['package']}")
        with open(path, "rb") as f:
            await message.reply_document(
                document=f,
                filename=result["filename"],
                caption="\n".join(caption_lines),
            )
        await status.delete()
    except Exception:
        logger.exception("Sending APK file failed for %s", details["package"])
        await status.edit_text(
            "I downloaded the APK but could not send it here. "
            "Please try again, or download it yourself:\n"
            f"{details['download_url']}"
        )
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if FORCE_CHANNEL and not await is_channel_member(context, user_id):
        await update.message.reply_text(
            "👋 Welcome! Before you can download, please join our channel first:\n"
            f"https://t.me/{FORCE_CHANNEL}\n\n"
            "Then tap 'I joined, check again' below.",
            reply_markup=join_keyboard(),
        )
        return
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (update.message.text or "").strip()
    if not query:
        return
    if len(query) > 100:
        await update.message.reply_text("Please send a shorter app name.")
        return
    user_id = update.effective_user.id
    if not await is_channel_member(context, user_id):
        await send_join_prompt(
            update.message, user_id, {"type": "search", "query": query}
        )
        return
    await do_search(update.message, user_id, query)


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "join_ok":
        pending = _pending.get(user_id)
        if not pending:
            await query.edit_message_text(
                "Please send the name of the app you want first."
            )
            return
        if not await is_channel_member(context, user_id):
            await query.edit_message_text(
                "You have not joined the channel yet.\n"
                f"Please join first: https://t.me/{FORCE_CHANNEL}\n\n"
                "Then tap the button again.",
                reply_markup=join_keyboard(),
            )
            return
        await query.edit_message_text("✅ Thanks for joining!")
        if pending["type"] == "search":
            await do_search(query.message, user_id, pending["query"])
        elif pending["type"] == "pick":
            await do_pick(query.message, user_id, pending["app"])
        return

    if query.data.startswith("pick:"):
        try:
            idx = int(query.data.split(":", 1)[1])
            app = _results.get(user_id, [])[idx]
        except (ValueError, IndexError):
            await query.edit_message_text(
                "That choice expired. Please send the app name again."
            )
            return
        if not await is_channel_member(context, user_id):
            _pending[user_id] = {"type": "pick", "app": app}
            await query.edit_message_text(
                "⚠️ Before you can download, please join our channel first:\n"
                f"https://t.me/{FORCE_CHANNEL}\n\n"
                "Then tap 'I joined, check again' below.",
                reply_markup=join_keyboard(),
            )
            return
        await do_pick(query.message, user_id, app)
        return

    if query.data == "dl":
        details = _details.get(user_id)
        if not details:
            await query.edit_message_text(
                "That download expired. Please send the app name again."
            )
            return
        if not await is_channel_member(context, user_id):
            await query.edit_message_text(
                "⚠️ Before you can download, please join our channel first:\n"
                f"https://t.me/{FORCE_CHANNEL}\n\n"
                "Then tap 'I joined, check again' below.",
                reply_markup=join_keyboard(),
            )
            return
        await do_download(query.message, user_id, details)
        return


async def post_init(app):
    """Log which bot and channel the membership check uses.

    No token and no user data -- just the bot's own id/username
    and the channel name, so the FadeHost console shows exactly
    what is being checked if the join gate ever misbehaves.
    """
    try:
        me = await app.bot.get_me()
        logger.info(
            "Bot @%s (id %s) checking membership of @%s",
            me.username,
            me.id,
            FORCE_CHANNEL,
        )
    except Exception as e:
        logger.error("could not read bot identity at startup: %s", e)


def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN is missing.")
        print("Set it as an environment variable (FadeHost) or in a .env file.")
        print("Get it from @BotFather on Telegram with /newbot.")
        print("NEVER paste the token in a chat.")
        raise SystemExit(1)

    logger.info("Force-join channel: @%s", FORCE_CHANNEL)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search)
    )

    print("✅ Bot is running... Send /start to @BotsFamilyApkBot on Telegram")
    app.run_polling()


if __name__ == "__main__":
    main()
