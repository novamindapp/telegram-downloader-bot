#!/usr/bin/env python3
"""
Telegram bot to download TikTok and Instagram videos
- User sends a link, the bot downloads the video and returns it without watermark (TikTok)
- Includes space for ads + a free daily limit so you can add premium later

How to run:
  1. pip install -r requirements.txt
  2. Set BOT_TOKEN in the .env file (get it from @BotFather)
  3. python bot.py

Important note:
  Instagram in 2025-2026 often asks for login.
  If Instagram does not work without cookies, you need a cookies.txt file from your browser.
  TikTok usually works without cookies.
"""

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("downloader-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Forced channel (optional): e.g. mychannel (without @)
# If left empty, no one is forced to join.
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "").strip().lstrip("@")
# Ad sent with every video (optional)
AD_TEXT = os.getenv("AD_TEXT", "")
AD_BUTTON_TEXT = os.getenv("AD_BUTTON_TEXT", "")
AD_BUTTON_URL = os.getenv("AD_BUTTON_URL", "")

# Free daily limit (so you can add premium later)
DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "10"))

# Simple in-memory counter: {user_id: downloads today}
# If the bot restarts, the counter resets. Good enough to start.
from collections import defaultdict
from datetime import date
_usage = defaultdict(int)
_usage_day = date.today()

URL_RE = re.compile(r"https?://[^\s]+")

WELCOME = (
    "👋 Welcome to the Video Downloader Bot\n\n"
    "Send me a video link from:\n"
    "• TikTok\n"
    "• Instagram (Reels / Post)\n\n"
    "I will send the video back here, TikTok without watermark.\n\n"
    f"You have {DAILY_FREE_LIMIT} free downloads per day.\n"
    "Send a link now 👇"
)

HELP_TEXT = (
    "How to use the bot?\n\n"
    "1. Go to TikTok or Instagram\n"
    "2. Share / Copy the link of the video\n"
    "3. Send it to me here\n"
    "4. I will send you the video back\n\n"
    "If the video is too large (over 45MB) I cannot send it, Telegram does not allow bots to send more than 50MB."
)


def is_supported(url: str) -> bool:
    url = url.lower()
    return any(
        d in url
        for d in [
            "tiktok.com",
            "vt.tiktok.com",
            "vm.tiktok.com",
            "instagram.com",
            "instagr.am",
        ]
    )


def download_video(url: str, tmpdir: str) -> tuple[str, str]:
    """Download the video with yt-dlp. Returns (file path, title)."""
    import yt_dlp

    # If a cookies.txt file exists next to bot.py, yt-dlp will use it (important for Instagram)
    cookiefile = "cookies.txt" if Path("cookies.txt").exists() else None

    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "cookiefile": cookiefile,
        # TikTok: yt-dlp returns the version without watermark when available
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Could not get video info")
        filename = ydl.prepare_filename(info)
        # If the video was merged, prepare_filename may not match the real file
        # Look for the first file in tmpdir
        if not os.path.exists(filename):
            files = list(Path(tmpdir).glob("*"))
            if not files:
                raise RuntimeError("Download failed, no file found")
            filename = str(files[0])
        title = info.get("title") or "Video"
        return filename, title


async def check_channel_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If FORCE_CHANNEL is set, check that the user joined the channel."""
    if not FORCE_CHANNEL:
        return True
    try:
        member = await context.bot.get_chat_member(f"@{FORCE_CHANNEL}", update.effective_user.id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("check channel failed: %s", e)
        return True  # If there is a problem, do not block the user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _usage_day
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text("Please send a valid video link from TikTok or Instagram.")
        return

    url = m.group(0)
    if not is_supported(url):
        await update.message.reply_text(
            "This link is not supported yet.\n"
            "This bot only supports TikTok and Instagram in this version."
        )
        return

    # Daily limit
    today = date.today()
    if today != _usage_day:
        _usage.clear()
        _usage_day = today
    user_id = update.effective_user.id
    if _usage[user_id] >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            f"You reached today's free limit ({DAILY_FREE_LIMIT} videos).\n"
            "Come back tomorrow, or contact the bot owner for the paid version."
        )
        return

    # Forced channel
    if not await check_channel_member(update, context):
        await update.message.reply_text(
            f"Before I download for you, please join our channel first:\n"
            f"https://t.me/{FORCE_CHANNEL}\n"
            "Then send the link again."
        )
        return

    status = await update.message.reply_text("⏳ Downloading the video... Please wait a moment")
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    try:
        filepath, title = await asyncio.to_thread(download_video, url, tmpdir)

        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if size_mb > 48:
            await status.edit_text(
                f"The video is too large ({size_mb:.1f}MB). Telegram does not allow a bot to send more than 50MB."
            )
            return

        _usage[user_id] += 1

        caption = title[:900] if title else ""
        with open(filepath, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption=caption,
                supports_streaming=True,
            )

        await status.delete()

        # Ad after every video (if set)
        if AD_TEXT:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            reply_markup = None
            if AD_BUTTON_URL and AD_BUTTON_TEXT:
                reply_markup = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(AD_BUTTON_TEXT, url=AD_BUTTON_URL)]]
                )
            await update.message.reply_text(AD_TEXT, reply_markup=reply_markup)

    except Exception as e:
        logger.exception("download failed for %s", url)
        msg = str(e)
        # Clear messages for the user instead of the technical error
        if "login" in msg.lower() or "cookies" in msg.lower() or "private" in msg.lower():
            await status.edit_text(
                "This video is private or requires Instagram login.\n"
                "Try a public video, or a TikTok video."
            )
        elif "unsupported url" in msg.lower() or "no video" in msg.lower():
            await status.edit_text("I could not find a video in this link. Make sure the link is correct and public.")
        else:
            await status.edit_text(
                "I could not download this video right now, please try again or try another link.\n"
                "If the problem continues, the site may have changed its protection."
            )
    finally:
        # Clean up temporary files
        try:
            for p in Path(tmpdir).glob("*"):
                p.unlink(missing_ok=True)
            Path(tmpdir).rmdir()
        except Exception:
            pass


def main():
    if not BOT_TOKEN:
        print("❌ You need BOT_TOKEN in the .env file")
        print("Get it from @BotFather on Telegram, use /newbot")
        raise SystemExit(1)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    print("✅ Bot is running... Send /start on Telegram")
    app.run_polling()


if __name__ == "__main__":
    main()
