"""Offline tests for the forced-join membership check -- no network,
no token.

Why this design: getChatMember('@BotsFamilyOfficial') ALWAYS fails
for this channel ("Member list is inaccessible", even for the
channel creator), so the check never relies on it alone and never
fails open:
  * member set seeded at startup from getChatAdministrators,
  * joins/leaves tracked from ChatMemberUpdated events, persisted
    in members.json,
  * getChatMember is only tried with the NUMERIC channel id,
  * any lookup failure -> NOT a member -> normal join prompt.
    There is no Admin-error message to users anywhere.

Run:  python test_membership.py
"""

import asyncio
import os
from pathlib import Path
import tempfile

# bots-family-apk-bot names the file bot.py, telegram-downloader-bot
# names it apk_bot.py (bot.py there is the OLD video bot). Pick by
# file name, not by catching ImportError, so a missing dependency
# inside the module is never silently hidden.
if os.path.exists(os.path.join(os.path.dirname(__file__), "apk_bot.py")):
    import apk_bot as apk_bot_mod
else:
    import bot as apk_bot_mod

# Point persistence at a temp file so tests never touch the real
# members.json (and never leave one behind in the repo).
_TMP = tempfile.TemporaryDirectory()
apk_bot_mod.MEMBERS_FILE = Path(_TMP.name) / "members.json"
apk_bot_mod._members = set()
apk_bot_mod._channel_id = -1001234567890


class FakeMember:
    def __init__(self, status):
        self.status = status


class FakeBot:
    def __init__(self, behaviour=None, chat_id=None, admins=None):
        # behaviour: a status string, or an Exception to raise
        self.behaviour = behaviour
        self.chat_id = chat_id
        self.admins = admins or []
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append(chat_id)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return FakeMember(self.behaviour)

    async def get_chat(self, username):
        return type("Chat", (), {"id": self.chat_id})()

    async def get_chat_administrators(self, chat_id):
        return [
            type("Admin", (), {"user": type("U", (), {"id": uid})()})()
            for uid in self.admins
        ]

    async def get_me(self):
        return type("Me", (), {"username": "BotsFamilyApkBot", "id": 1})()


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

    async def edit_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.markup = reply_markup


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeUpdate:
    def __init__(self, message, user_id=12345):
        self.message = message
        self.effective_user = FakeUser(user_id)
        self.chat_member = None


class FakeChatMember:
    def __init__(self, chat_id, user_id, status):
        self.chat = type("Chat", (), {"id": chat_id, "username": None})()
        self.new_chat_member = type(
            "CM", (), {"user": FakeUser(user_id), "status": status}
        )()


def _reset():
    apk_bot_mod._members = set()
    apk_bot_mod._channel_id = -1001234567890


def test_local_set_allows_without_api_call():
    _reset()
    apk_bot_mod._members = {12345}
    bot = FakeBot("left")  # would deny if it were called
    assert asyncio.run(apk_bot_mod.is_channel_member(FakeContext(bot), 12345)) is True
    assert bot.calls == [], bot.calls
    print("test_local_set_allows_without_api_call OK")


def test_numeric_id_lookup_member_allows_and_adds():
    _reset()
    bot = FakeBot("member", chat_id=-1001234567890)
    assert asyncio.run(apk_bot_mod.is_channel_member(FakeContext(bot), 777)) is True
    assert bot.calls == [-1001234567890], bot.calls
    assert 777 in apk_bot_mod._members
    print("test_numeric_id_lookup_member_allows_and_adds OK")


def test_lookup_error_is_not_member_join_prompt():
    """getChatMember raising 'Member list is inaccessible' -> the
    user gets the normal join prompt, NOT a download and NOT an
    Admin-error message."""
    _reset()
    bot = FakeBot(Exception("Bad Request: Member list is inaccessible"))
    assert asyncio.run(apk_bot_mod.is_channel_member(FakeContext(bot), 12345)) is False
    message = FakeMessage()
    update = FakeUpdate(message)
    asyncio.run(apk_bot_mod.start(update, FakeContext(bot)))
    assert len(message.replies) == 1, message.replies
    assert "join our channel" in message.replies[0].lower()
    assert "could not check" not in message.replies[0].lower()
    print("test_lookup_error_is_not_member_join_prompt OK")


def test_left_is_not_member():
    _reset()
    bot = FakeBot("left")
    assert asyncio.run(apk_bot_mod.is_channel_member(FakeContext(bot), 12345)) is False
    print("test_left_is_not_member OK")


def test_chat_member_join_event_adds():
    _reset()
    update = FakeUpdate(None)
    update.chat_member = FakeChatMember(-1001234567890, 555, "member")
    asyncio.run(apk_bot_mod.handle_chat_member(update, FakeContext(FakeBot())))
    assert 555 in apk_bot_mod._members
    assert asyncio.run(
        apk_bot_mod.is_channel_member(FakeContext(FakeBot("left")), 555)
    ) is True
    print("test_chat_member_join_event_adds OK")


def test_chat_member_leave_event_removes():
    _reset()
    apk_bot_mod._members = {555}
    update = FakeUpdate(None)
    update.chat_member = FakeChatMember(-1001234567890, 555, "left")
    asyncio.run(apk_bot_mod.handle_chat_member(update, FakeContext(FakeBot())))
    assert 555 not in apk_bot_mod._members
    print("test_chat_member_leave_event_removes OK")


def test_chat_member_other_chat_ignored():
    _reset()
    update = FakeUpdate(None)
    update.chat_member = FakeChatMember(-100999, 555, "member")
    asyncio.run(apk_bot_mod.handle_chat_member(update, FakeContext(FakeBot())))
    assert 555 not in apk_bot_mod._members
    print("test_chat_member_other_chat_ignored OK")


def test_post_init_seeds_admins():
    _reset()
    apk_bot_mod._channel_id = None
    bot = FakeBot(chat_id=-1001234567890, admins=[111, 222])
    app = type("App", (), {"bot": bot})()
    asyncio.run(apk_bot_mod.post_init(app))
    assert apk_bot_mod._channel_id == -1001234567890
    assert 111 in apk_bot_mod._members and 222 in apk_bot_mod._members
    print("test_post_init_seeds_admins OK")


def test_persistence_roundtrip():
    _reset()
    apk_bot_mod._add_member(4242)
    apk_bot_mod._load_members()
    assert 4242 in apk_bot_mod._members
    apk_bot_mod._remove_member(4242)
    apk_bot_mod._load_members()
    assert 4242 not in apk_bot_mod._members
    print("test_persistence_roundtrip OK")


if __name__ == "__main__":
    test_local_set_allows_without_api_call()
    test_numeric_id_lookup_member_allows_and_adds()
    test_lookup_error_is_not_member_join_prompt()
    test_left_is_not_member()
    test_chat_member_join_event_adds()
    test_chat_member_leave_event_removes()
    test_chat_member_other_chat_ignored()
    test_post_init_seeds_admins()
    test_persistence_roundtrip()
    print("ALL MEMBERSHIP TESTS PASSED")
