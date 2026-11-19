#!/usr/bin/env python3
"""
Run BOTH Telegram bots in ONE FadeHost free service.

  * Video bot (old):  bot.py      -- @tiktok_insta_dl_2026_bot
    TikTok / Instagram video downloader, forced join
    channel tiktok_insta_dl_2026bot, Adsterra ad gate.
  * APK bot (new):    apk_bot.py  -- @BotsFamilyApkBot ("All Apk Downloader")
    APKPure search, forced join channel @BotsFamilyOfficial.

Why one service: FadeHost's free plan is one 256 MB service, and a
second free service is not available -- a new service costs $20.
So both bots share this one service / one start command:
    python run_both.py

How it works:
  * Each bot runs as its own subprocess, so a crash in one does not
    kill the other.
  * Each subprocess gets its OWN environment: the two bots both read
    BOT_TOKEN / FORCE_CHANNEL from the environment, so the runner
    passes each one its own values:
        old bot:  BOT_TOKEN     = OLD_BOT_TOKEN
                  FORCE_CHANNEL = OLD_FORCE_CHANNEL (default tiktok_insta_dl_2026bot)
        apk bot:  BOT_TOKEN     = BOT_TOKEN
                  FORCE_CHANNEL = FORCE_CHANNEL (default BotsFamilyOfficial)
    No token is in this file, in any code file, or in logs.
    Tokens come ONLY from the environment (FadeHost env vars).
    load_dotenv() in the bots does not override env vars that are
    already set, so the runner's values win over any local .env.
  * If a bot crashes, the runner logs which bot failed and restarts
    it after a short backoff. If a bot exits quickly 5 times in a
    row (e.g. a bad token), the runner stops restarting it and keeps
    the other one running.
  * Exit code is non-zero only if BOTH bots are down / cannot start
    (e.g. both tokens missing).

Env vars (set in FadeHost, never in a chat):
  BOT_TOKEN         - token of @BotsFamilyApkBot (new APK bot) -- required
  OLD_BOT_TOKEN     - token of @tiktok_insta_dl_2026_bot (old video bot) -- required
  FORCE_CHANNEL     - APK bot channel, default BotsFamilyOfficial
  OLD_FORCE_CHANNEL - video bot channel, default tiktok_insta_dl_2026bot
  (the old bot's AD_TEXT / AD_BUTTON_URL / DAILY_FREE_LIMIT are
   passed through unchanged from the environment.)
"""

import os
import signal
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# (name, script, token env, channel env, default channel)
BOTS = [
    (
        "video-bot",
        "bot.py",
        "OLD_BOT_TOKEN",
        "OLD_FORCE_CHANNEL",
        "tiktok_insta_dl_2026bot",
    ),
    (
        "apk-bot",
        "apk_bot.py",
        "BOT_TOKEN",
        "FORCE_CHANNEL",
        "BotsFamilyOfficial",
    ),
]

MAX_QUICK_FAILURES = 5
QUICK_WINDOW_SECONDS = 60
RESTART_DELAY_SECONDS = 5

_stop = threading.Event()


def _make_env(token_var: str, channel_var: str, default_channel: str) -> dict:
    """Build the subprocess env: parent env + this bot's token/channel."""
    env = dict(os.environ)
    env["BOT_TOKEN"] = os.environ.get(token_var, "")
    env["FORCE_CHANNEL"] = os.environ.get(channel_var, default_channel)
    return env


def _supervise(name: str, script: str, token_var: str, channel_var: str,
               default_channel: str, results: dict) -> None:
    """Run one bot, restarting it on crash until told to stop."""
    token = os.environ.get(token_var, "").strip()
    if not token:
        print(f"[{name}] missing token: set {token_var} in FadeHost env vars. "
              f"This bot will not start.", flush=True)
        results[name] = "missing-token"
        return

    env = _make_env(token_var, channel_var, default_channel)
    failures: list[float] = []
    while not _stop.is_set():
        print(f"[{name}] starting {script} ...", flush=True)
        try:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, script)],
                env=env,
            )
        except Exception as exc:  # e.g. script file missing
            print(f"[{name}] failed to start {script}: {exc}", flush=True)
            results[name] = "start-failed"
            return

        # Wait for it to exit, or for a shutdown request.
        while proc.poll() is None and not _stop.is_set():
            time.sleep(0.5)
        if _stop.is_set():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            results[name] = "stopped"
            return

        code = proc.returncode
        print(f"[{name}] exited with code {code}; restarting ...", flush=True)
        now = time.time()
        failures = [t for t in failures if now - t < QUICK_WINDOW_SECONDS]
        failures.append(now)
        if len(failures) >= MAX_QUICK_FAILURES:
            print(f"[{name}] exited {MAX_QUICK_FAILURES} times in "
                  f"{QUICK_WINDOW_SECONDS}s -- giving up on this bot. "
                  f"Check its token ({token_var}) and logs.", flush=True)
            results[name] = "crashed-repeatedly"
            return
        _stop.wait(RESTART_DELAY_SECONDS)


def main() -> int:
    missing = [var for _, _, var, _, _ in BOTS
               if not os.environ.get(var, "").strip()]
    if len(missing) == len(BOTS):
        print("❌ No bot token is set.")
        print("Set BOT_TOKEN (new APK bot, @BotsFamilyApkBot) and "
              "OLD_BOT_TOKEN (old video bot, @tiktok_insta_dl_2026_bot)")
        print("as environment variables in FadeHost.")
        print("NEVER paste a token in a chat.")
        print("No bot was started.")
        return 1
    if missing:
        print(f"⚠️ Missing token(s): {', '.join(missing)} -- "
              f"that bot will not start; the other one will.", flush=True)

    results: dict = {}
    threads = []
    for name, script, token_var, channel_var, default_channel in BOTS:
        t = threading.Thread(
            target=_supervise,
            args=(name, script, token_var, channel_var, default_channel, results),
            daemon=True,
        )
        t.start()
        threads.append(t)

    def _shutdown(signum, frame):
        print("Shutting down both bots ...", flush=True)
        _stop.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Wait until every supervised bot has finished (missing token,
    # repeated crash, or shutdown). The process exits non-zero only
    # if no bot is running / none could start.
    for t in threads:
        t.join()

    running = [name for name, state in results.items() if state == "stopped"]
    if _stop.is_set():
        return 0
    print(f"Both bots are down: {results}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
