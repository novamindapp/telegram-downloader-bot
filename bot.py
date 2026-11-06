#!/usr/bin/env python3
"""
Telegram bot to download TikTok and Instagram videos
- User sends a link, the bot downloads the video and returns it without watermark (TikTok)
- MANDATORY: user must join FORCE_CHANNEL before any download.
  Every link from a non-member gets the join prompt again, no download.
- MANDATORY AD GATE: before the download, the user must tap "Watch Ad"
  (Adsterra link, set in AD_BUTTON_URL) and then "Continue" to get the video.
  This mimics the MadBots / Download IT flow: "To continue: short ad (5 sec)."

How to run:
  1. pip install -r requirements.txt
  2. Set BOT_TOKEN in the .env file (get it from @BotFather)
  3. python bot.py

Env vars (FadeHost / .env):
  BOT_TOKEN         - required
  FORCE_CHANNEL     - default tiktok_insta_dl_2026bot (without @). Set to "" to disable.
  DAILY_FREE_LIMIT  - default 10
  AD_TEXT           - ad-gate text, default "To continue: short ad (5 sec)."
  AD_BUTTON_TEXT    - default "Watch Ad"
  AD_BUTTON_URL     - Adsterra Direct Link / Smartlink. Default is the user's
                      NovaMind site, which itself carries an Adsterra banner.
                      For direct Adsterra revenue, replace it in FadeHost
                      with your Adsterra Direct Link URL.
                      Set to "" to disable the ad gate.
                      NOTE: Telegram cannot show an Adsterra banner <script>
                      inside a chat, so the ad must be a clickable link.

Important note:
  Instagram in 2025-2026 often asks for login.
  If Instagram does not work without cookies, you need a cookies.txt file from your browser.
  TikTok usually works without cookies.
  The bot MUST be an Admin in FORCE_CHANNEL, otherwise Telegram will not
  tell it who joined, and the membership check fails open (allows download).
"""

import asyncio
import logging
import os
import re
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("downloader-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Forced channel - MANDATORY by default for this bot.
# os.getenv returns the env value when set; when FadeHost has no such
# variable at all it falls back to the channel below.
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "tiktok_insta_dl_2026bot").strip().lstrip("@")
# Ad gate - MANDATORY by default for this bot.
# AD_BUTTON_URL: your Adsterra Direct Link. Default: NovaMind site (has Adsterra banner).
AD_TEXT = os.getenv("AD_TEXT", "⚠️ To continue: short ad (5 sec).")
AD_BUTTON_TEXT = os.getenv("AD_BUTTON_TEXT", "📺 Watch Ad")
AD_BUTTON_URL = os.getenv(
    "AD_BUTTON_URL", "https://novamind-ai-2cz.pages.dev/"
).strip()

# Free daily limit (so you can add premium later)
try:
    DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "10"))
except ValueError:
    DAILY_FREE_LIMIT = 10

# Simple in-memory state: {user_id: downloads today}, {user_id: pending url}
_usage = defaultdict(int)
_usage_day = date.today()
_pending_url = {}

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
    "1. Join our channel first (required)\n"
    "2. Go to TikTok or Instagram\n"
    "3. Share / Copy the link of the video\n"
    "4. Send it to me here\n"
    "5. Watch the short ad, tap Continue, and I will send you the video back\n\n"
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


def _download_via_tikwm(url: str, tmpdir: str) -> tuple[str, str]:
    """Fallback for TikTok when yt-dlp is blocked by TikTok.
    Uses the TikWM API to get the no-watermark video URL, then downloads it
    with requests (User-Agent + Referer are required by the TikTok CDN).
    Tested 2026-11-05 in Muse VM: returns code 0 and a 200 download for ZPLx76411.
    """
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    r = requests.get(
        "https://www.tikwm.com/api/",
        params={"url": url},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0 or not data.get("data"):
        raise RuntimeError(f"TikWM API failed: {data.get('msg')}")
    info = data["data"]
    play_url = info.get("play")
    if not play_url:
        raise RuntimeError("TikWM API returned no video URL")
    out_path = os.path.join(tmpdir, f"{info.get('id', 'tiktok')}.mp4")
    with requests.get(play_url, headers=headers, timeout=60, stream=True) as v:
        v.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in v.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
        raise RuntimeError("TikWM download produced no file")
    title = info.get("title") or "Video"
    logger.info("TikWM fallback succeeded for %s", url)
    return out_path, title


def download_video(url: str, tmpdir: str) -> tuple[str, str]:
    """Download the video with yt-dlp. Returns (file path, title).
    TikTok fallback: if yt-dlp fails (TikTok blocks the server IP with
    'Unexpected response from webpage request'), try TikWM."""
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
        # Note: "impersonate" was tried and made yt-dlp fail in our test venv,
        # so it is NOT set here - the TikWM fallback below covers blocked IPs.
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        },
    }

    try:
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
    except Exception as e:
        logger.warning("yt-dlp failed for %s: %s - trying TikWM fallback", url, e)
        if "tiktok.com" in url.lower():
            return _download_via_tikwm(url, tmpdir)
        raise


async def is_channel_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """If FORCE_CHANNEL is set, check that the user joined the channel."""
    if not FORCE_CHANNEL:
        return True
    try:
        member = await context.bot.get_chat_member(f"@{FORCE_CHANNEL}", user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("check channel failed: %s", e)
        return True  # If Telegram itself fails, do not block the user


def join_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if FORCE_CHANNEL:
        buttons.append(
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL}")]
        )
    buttons.append([InlineKeyboardButton("✅ I joined, check again", callback_data="join_ok")])
    return InlineKeyboardMarkup(buttons)


def ad_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if AD_BUTTON_URL:
        buttons.append(
            [InlineKeyboardButton(AD_BUTTON_TEXT or "📺 Watch Ad", url=AD_BUTTON_URL)]
        )
    buttons.append([InlineKeyboardButton("✅ Continue to download", callback_data="ad_ok")])
    return InlineKeyboardMarkup(buttons)


async def send_join_prompt(message, user_id: int, url: str):
    """Save the pending link and ask the user to join, with a Join button.
    Called on every link from a non-member, so the prompt keeps coming
    back until they join - no download is ever sent before that."""
    _pending_url[user_id] = url
    await message.reply_text(
        "⚠️ Before I download for you, please join our channel first:\n"
        f"https://t.me/{FORCE_CHANNEL}\n\n"
        "Then tap 'I joined, check again' below.",
        reply_markup=join_keyboard(),
    )


async def send_ad_gate(message, user_id: int, url: str):
    """Save the pending link and show the Adsterra ad gate.
    The user must tap Watch Ad, then Continue. No download before that."""
    _pending_url[user_id] = url
    text = AD_TEXT or "⚠️ To continue: short ad (5 sec)."
    await message.reply_text(text, reply_markup=ad_keyboard())


def _limit_reached(user_id: int) -> bool:
    global _usage_day
    today = date.today()
    if today != _usage_day:
        _usage.clear()
        _usage_day = today
    return _usage[user_id] >= DAILY_FREE_LIMIT


async def do_download(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, url: str):
    """Actually download and send the video. Called only after the
    channel check and the ad gate have both passed."""
    status = await message.reply_text("⏳ Downloading the video... Please wait a moment")
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
        _pending_url.pop(user_id, None)

        caption = title[:900] if title else ""
        with open(filepath, "rb") as f:
            await message.reply_video(
                video=f,
                caption=caption,
                supports_streaming=True,
            )

        await status.delete()

    except Exception as e:
        logger.exception("download failed for %s", url)
        msg = str(e)
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
        try:
            for p in Path(tmpdir).glob("*"):
                p.unlink(missing_ok=True)
            Path(tmpdir).rmdir()
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Mandatory join even on /start, so a new user sees it before anything else.
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


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    user_id = update.effective_user.id

    # Daily limit first, so an ad watch is not wasted on a blocked user.
    if _limit_reached(user_id):
        await update.message.reply_text(
            f"You reached today's free limit ({DAILY_FREE_LIMIT} videos).\n"
            "Come back tomorrow, or contact the bot owner for the paid version."
        )
        return

    # 1) MANDATORY channel join - every link, until they join.
    if not await is_channel_member(context, user_id):
        await send_join_prompt(update.message, user_id, url)
        return

    # 2) MANDATORY Adsterra ad gate - every download, until they tap Continue.
    if AD_BUTTON_URL:
        await send_ad_gate(update.message, user_id, url)
        return

    await do_download(update.message, context, user_id, url)


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'I joined' and 'Continue' buttons. The pending link is kept per user,
    so the button resumes the same download instead of asking for the link again."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    url = _pending_url.get(user_id)
    if not url:
        await query.edit_message_text(
            "Please send the video link again, then follow the steps."
        )
        return

    if query.data == "join_ok":
        if not await is_channel_member(context, user_id):
            await query.edit_message_text(
                "You have not joined the channel yet.\n"
                f"Please join first: https://t.me/{FORCE_CHANNEL}\n\n"
                "Then tap the button again.",
                reply_markup=join_keyboard(),
            )
            return
        # Joined - now go to the ad gate, or straight to download.
        await query.edit_message_text("✅ Thanks for joining! Now the ad step:")
        if AD_BUTTON_URL:
            await send_ad_gate(query.message, user_id, url)
        else:
            await do_download(query.message, context, user_id, url)
        return

    if query.data == "ad_ok":
        # Re-check both gates, in case the user left and came back later.
        if _limit_reached(user_id):
            await query.edit_message_text(
                f"You reached today's free limit ({DAILY_FREE_LIMIT} videos).\nCome back tomorrow."
            )
            return
        if not await is_channel_member(context, user_id):
            await query.edit_message_text(
                "Please join our channel first:\n"
                f"https://t.me/{FORCE_CHANNEL}",
                reply_markup=join_keyboard(),
            )
            return
        await query.edit_message_text("✅ Thanks! Downloading your video now...")
        await do_download(query.message, context, user_id, url)
        return


def main():
    if not BOT_TOKEN:
        print("❌ You need BOT_TOKEN in the .env file")
        print("Get it from @BotFather on Telegram, use /newbot")
        raise SystemExit(1)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    print("✅ Bot is running... Send /start on Telegram")
    app.run_polling()


if __name__ == "__main__":
    main()
