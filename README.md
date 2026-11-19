# Telegram Downloader Bots — both bots in one service

Two Telegram bots run together from this one repo, on one FadeHost
free service (256 MB). A second free FadeHost service is not
available — a new service costs $20 — so both bots share this one.

## The two bots

| Bot | File | What it does | Forced-join channel |
|---|---|---|---|
| Video Downloader — @tiktok_insta_dl_2026_bot (old) | `bot.py` | User sends a TikTok / Instagram link, bot downloads the video (TikTok without watermark) and sends it back. Adsterra ad gate + 10 free downloads/day. | `tiktok_insta_dl_2026bot` |
| All Apk Downloader — @BotsFamilyApkBot (new) | `apk_bot.py` + `apkpure.py` | User sends an app name, bot searches APKPure, shows top 5 results, and on 'Download APK' sends the APK FILE in the chat (up to 48 MB; bigger APKs get the download link instead). No YouTube, no AI chat in v1. | `@BotsFamilyOfficial` |

Channel for both bots: **Our Bots Family | Наша семья ботов**
— https://t.me/BotsFamilyOfficial — but the OLD video bot still
forces joining its own old channel `tiktok_insta_dl_2026bot`
(see env vars below).

## Both bots in one service

`run_both.py` starts both bots as separate subprocesses:

- Each bot gets its OWN token / channel in its subprocess env.
  Both bots read `BOT_TOKEN` / `FORCE_CHANNEL` from the env, so
  the runner passes each one its own values:
  - video bot: `BOT_TOKEN` = `OLD_BOT_TOKEN`,
    `FORCE_CHANNEL` = `OLD_FORCE_CHANNEL` (default `tiktok_insta_dl_2026bot`)
  - APK bot: `BOT_TOKEN` = `BOT_TOKEN`,
    `FORCE_CHANNEL` = `FORCE_CHANNEL` (default `BotsFamilyOfficial`)
- No token is in any code file or in logs. Tokens come ONLY from
  the environment (FadeHost env vars).
- If a bot crashes, the runner logs which bot failed and restarts
  it after 5 seconds. If a bot exits quickly 5 times in a row
  (e.g. a bad token), the runner stops restarting it and keeps the
  other one running. The service exits non-zero only if BOTH bots
  are down.

## Start command (FadeHost)

```
pip install -r requirements.txt
python run_both.py
```

## Env vars to set in FadeHost

| Var | Value |
|---|---|
| `BOT_TOKEN` | Token of @BotsFamilyApkBot (new APK bot) |
| `OLD_BOT_TOKEN` | Token of @tiktok_insta_dl_2026_bot (old video bot) |
| `FORCE_CHANNEL` | `BotsFamilyOfficial` (APK bot channel) |
| `OLD_FORCE_CHANNEL` | `tiktok_insta_dl_2026bot` (video bot channel) |

Old bot extras (passed through unchanged):
`DAILY_FREE_LIMIT` (default 10), `AD_TEXT`, `AD_BUTTON_TEXT`,
`AD_BUTTON_URL` (Adsterra Direct Link).

**NEVER paste a token in a chat.** Put it in FadeHost env vars only.

## Each bot must be an Admin of its channel

- Add **@BotsFamilyApkBot** as Admin of **@BotsFamilyOfficial**.
- Add **@tiktok_insta_dl_2026_bot** as Admin of
  **tiktok_insta_dl_2026bot**.

Otherwise Telegram will not reliably tell the bot who joined, and
the membership check fails open (the bot lets the download through).

## Test both bots (from a second account)

1. From an account that has NOT joined either channel:
   send `/start` to @BotsFamilyApkBot — it must ask you to join
   @BotsFamilyOfficial first. Join, tap "I joined, check again",
   send `Facebook Lite` → pick the app → tap "Download APK".
2. Same account, send `/start` to @tiktok_insta_dl_2026_bot —
   it must ask you to join tiktok_insta_dl_2026bot first, then
   show the ad gate, then download a TikTok link.

## 256 MB limit — plain warning

Two bots share one 256 MB service. The APK bot is light (search
only, sends links). The video bot downloads videos (up to 48 MB
each) and runs yt-dlp, so it uses much more memory. They *should*
fit together in 256 MB most of the time, but this is NOT proven —
if the video bot is downloading a large video while the APK bot
is busy, the service can run out of memory and restart. If that
happens, the fix is a bigger (paid) plan, or splitting the bots
again. Watch the FadeHost logs / memory for the first days.

## شرح بالدارجة — شنو خاصك دير نتا

البوتين بجوج خدامين في نفس السيرفيس الفابور، حيت فابور
ما كاينش لسيرفيس تاني.

1. في FadeHost، في السيرفيس ديال **telegram-downloader-bot**،
   بدل Start Command لـ `python run_both.py`.
2. زيد في Environment Variables:
   - `BOT_TOKEN` = توكن البوت الجديد (@BotsFamilyApkBot)
   - `OLD_BOT_TOKEN` = توكن البوت القديم (@tiktok_insta_dl_2026_bot)
   - `FORCE_CHANNEL` = `BotsFamilyOfficial`
   - `OLD_FORCE_CHANNEL` = `tiktok_insta_dl_2026bot`
3. دير Redeploy.
4. زيد كل بوت أدمن في القناة ديالو (الفوق).
5. جرب البوتين من حساب آخر ما مشتركش في القنوات.

**التوكن عندك، ما تصيفطوش في الشات.**
