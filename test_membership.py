"""Offline tests for the forced-join membership check -- no network,
no token.

The check must FAIL CLOSED: if Telegram cannot answer the lookup
(e.g. the bot is not an Admin of the channel), a non-member must
NOT get through. Regression test for 2026-11-19, when a second
number that never joined @BotsFamilyOfficial still got the APK
because the old check returned True on any exception.

Run:  python test_membership.py
"""

import asyncio

import os

# bots-family-apk-bot names the file bot.py, telegram-downloader-bot
# names it apk_bot.py (bot.py there is the OLD video bot). Pick by
# file name, not by catching ImportError, so a missing dependency
# inside the module is never silently hidden.
if os.path.exists(os.path.join(os.path.dirname(__file__), "apk_bot.py")):
    import apk_bot as apk_bot_mod
else:
    import bot as apk_bot_mod


class FakeMember:
    def __init__(self, status):
        self.status = status


class FakeBot:
    def __init__(self, behaviour):
        # behaviour: a status string, or an Exception to raise
        self.behaviour = behaviour

    async def get_chat_member(self, chat_id, user_id):
        assert chat_id == f"@{apk_bot_mod.FORCE_CHANNEL}", chat_id
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return FakeMember(self.behaviour)


class FakeContext:
    def __init__(self, bot):
        self.bot = bot


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.markup = None

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.markup = reply_markup


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeUpdate:
    def __init__(self, message, user_id=12345):
        self.message = message
        self.effective_user = FakeUser(user_id)


def test_member_allowed():
    ctx = FakeContext(FakeBot("member"))
    status = asyncio.run(
        apk_bot_mod.check_channel_membership(ctx, 12345)
    )
    assert status == "member", status
    assert asyncio.run(apk_bot_mod.is_channel_member(ctx, 12345)) is True
    print("test_member_allowed OK")


def test_left_is_not_member():
    ctx = FakeContext(FakeBot("left"))
    status = asyncio.run(
        apk_bot_mod.check_channel_membership(ctx, 12345)
    )
    assert status == "not_member", status
    assert asyncio.run(apk_bot_mod.is_channel_member(ctx, 12345)) is False
    print("test_left_is_not_member OK")


def test_lookup_error_fails_closed():
    """get_chat_member raising must NOT let the user through."""
    ctx = FakeContext(
        FakeBot(Exception("Forbidden: bot is not a member of the channel"))
    )
    status = asyncio.run(
        apk_bot_mod.check_channel_membership(ctx, 12345)
    )
    assert status == "error", status
    assert asyncio.run(apk_bot_mod.is_channel_member(ctx, 12345)) is False
    print("test_lookup_error_fails_closed OK")


def test_start_shows_error_not_welcome():
    """On a lookup error /start must show the Admin error, not WELCOME."""
    ctx = FakeContext(
        FakeBot(Exception("Bad Request: chat not found"))
    )
    message = FakeMessage()
    update = FakeUpdate(message)
    asyncio.run(apk_bot_mod.start(update, ctx))
    assert len(message.replies) == 1, message.replies
    assert "could not check your channel membership" in message.replies[0]
    assert "Admin of the channel" in message.replies[0]
    assert "Welcome to All Apk Downloader" not in message.replies[0]
    print("test_start_shows_error_not_welcome OK")


def test_start_non_member_gets_join_prompt():
    ctx = FakeContext(FakeBot("left"))
    message = FakeMessage()
    update = FakeUpdate(message)
    asyncio.run(apk_bot_mod.start(update, ctx))
    assert len(message.replies) == 1, message.replies
    assert "join our channel first" in message.replies[0]
    assert message.markup is not None
    print("test_start_non_member_gets_join_prompt OK")


if __name__ == "__main__":
    test_member_allowed()
    test_left_is_not_member()
    test_lookup_error_fails_closed()
    test_start_shows_error_not_welcome()
    test_start_non_member_gets_join_prompt()
    print("All membership tests passed.")
