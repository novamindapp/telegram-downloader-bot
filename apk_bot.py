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
  * The bot MUST be an Admin of @BotsFamilyOfficial.
  * getChatMember('@BotsFamilyOfficial') does NOT work for this
    channel: Telegram ALWAYS fails it with "Member list is
    inaccessible", even for the channel creator. So the check
    does NOT rely on that call alone (and never fails open):
      - At startup the bot resolves the channel to its numeric
        id and seeds the member set from getChatAdministrators
        (covers the owner/creator).
      - Joins/leaves are tracked from ChatMemberUpdated events
        and persisted in members.json next to this file.
      - A membership check first looks in that local set, then
        tries getChatMember with the NUMERIC channel id.
      - If the lookup still fails, the user gets the normal
        join prompt -- never an Admin-error message and never
        a free download.
  * BOT_TOKEN comes ONLY from the environment (FadeHost env var or
    a local .env file). It is NEVER in this file, NEVER in logs.

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then put your BOT_TOKEN in .env
  python bot.py
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
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

# --- Forced-join membership tracking -------------------------------------
# getChatMember('@BotsFamilyOfficial') always fails for this channel
# ("Member list is inaccessible", even for the channel creator), so
# the bot keeps its own member set instead of asking Telegram on
# every check:
#   * seeded at startup from getChatAdministrators (owner/creator),
#   * updated from ChatMemberUpdated events (join / leave),
#   * persisted in members.json next to this file (atomic writes),
#   * getChatMember with the NUMERIC channel id is only a fallback.
MEMBERS_FILE = Path(__file__).with_name("members.json")
_MEMBER_STATUSES = ("member", "administrator", "creator", "restricted")
_members: set[int] = set()
_channel_id: int | None = None


def _load_members() -> None:
    """Load the persisted member set (empty if the file is missing)."""
    global _members
    try:
        data = json.loads(Path(MEMBERS_FILE).read_text(encoding="utf-8"))
        _members = {int(uid) for uid in data}
    except FileNotFoundError:
        _members = set()
    except Exception as e:
        logger.warning("could not load %s: %s", MEMBERS_FILE, e)
        _members = set()


def _save_members() -> None:
    """Persist the member set atomically (temp file + replace)."""
    try:
        path = Path(MEMBERS_FILE)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(sorted(_members)), encoding="utf-8"
        )
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("could not save %s: %s", MEMBERS_FILE, e)


def _add_member(user_id: int) -> None:
    if user_id not in _members:
        _members.add(user_id)
        _save_members()


def _remove_member(user_id: int) -> None:
    if user_id in _members:
        _members.discard(user_id)
        _save_members()


_load_members()

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
    """True if the user is a member of FORCE_CHANNEL. Never fails open.

    Order: (a) the locally tracked member set, (b) getChatMember
    with the NUMERIC channel id, (c) any lookup failure means
    "not a member" -- the caller shows the normal join prompt.
    A successful lookup adds the user to the local set.
    """
    if not FORCE_CHANNEL:
        return True
    if user_id in _members:
        return True
    chat_id = _channel_id if _channel_id is not None else f"@{FORCE_CHANNEL}"
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.warning("channel membership check failed: %s", e)
        return False
    if member.status in _MEMBER_STATUSES:
        _add_member(user_id)
        return True
    return False


async def handle_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Track channel joins/leaves from ChatMemberUpdated events.

    Only updates for FORCE_CHANNEL are used. A join (member /
    administrator / creator / restricted) adds the user to the
    local member set; a leave / kick removes them. This is what
    makes the join gate work even though getChatMember with the
    @username always fails for this channel.
    """
    cm = update.chat_member
    if cm is None:
        return
    chat = cm.chat
    if _channel_id is not None:
        if chat.id != _channel_id:
            return
    elif getattr(chat, "username", None) != FORCE_CHANNEL:
        return
    new_member = cm.new_chat_member
    user_id = new_member.user.id
    if new_member.status in _MEMBER_STATUSES:
        _add_member(user_id)
    elif new_member.status in ("left", "kicked"):
        _remove_member(user_id)


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
    """Log which bot and channel the membership check uses, resolve
    the channel to its numeric id, and seed the member set from
    the channel administrators (this covers the owner/creator,
    whose join event will never arrive).

    No token and no user data in the logs -- just the bot's own
    id/username, the channel name/id, and how many admins were
    seeded, so the FadeHost console shows exactly what is being
    checked if the join gate ever misbehaves.
    """
    global _channel_id
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
    if not FORCE_CHANNEL:
        return
    try:
        chat = await app.bot.get_chat(f"@{FORCE_CHANNEL}")
        _channel_id = chat.id
        logger.info(
            "Force-join channel @%s resolved to id %s",
            FORCE_CHANNEL,
            _channel_id,
        )
    except Exception as e:
        logger.error(
            "could not resolve @%s to a channel id: %s", FORCE_CHANNEL, e
        )
        return
    try:
        admins = await app.bot.get_chat_administrators(_channel_id)
        seeded = 0
        for admin in admins:
            _add_member(admin.user.id)
            seeded += 1
        logger.info("Seeded %d channel administrators as members", seeded)
    except Exception as e:
        logger.error(
            "could not list administrators of @%s: %s", FORCE_CHANNEL, e
        )


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
    # ChatMemberUpdated events are how joins/leaves are tracked --
    # getChatMember('@username') does not work for this channel.
    # NOTE: the default ChatMemberHandler only receives MY_CHAT_MEMBER
    # updates (the bot's OWN status changes), so other users joining
    # the channel never reached handle_chat_member -- that is why
    # joins were never recorded. CHAT_MEMBER is required to receive
    # other members' join/leave updates for the channel.
    app.add_handler(
        ChatMemberHandler(
            handle_chat_member,
            chat_member_types=ChatMemberHandler.CHAT_MEMBER,
        )
    )

    print("✅ Bot is running... Send /start to @BotsFamilyApkBot on Telegram")
    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member"]
    )


if __name__ == "__main__":
    main()
