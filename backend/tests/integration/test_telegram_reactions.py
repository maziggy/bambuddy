"""Answering the outcome prompt with a Telegram reaction (#3046).

The Bot API is stood in for at the httpx layer (the same ``_http_client``
swap the forum-topic tests use), the database is the real test engine: the
poller opens its own sessions, so the tests hand it the test session
factory instead of the module-level one.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.notification import TelegramPendingVerdict
from backend.app.models.print_log import PrintLogEntry
from backend.app.services.notification_service import TELEGRAM_REACTION_HINT, NotificationService
from backend.app.services.print_confirmation import apply_outcome_verdict
from backend.app.services.telegram_reactions import (
    CONFLICT_COOLDOWN,
    MAX_BACKOFF,
    TelegramReactionPoller,
    verdict_from_reaction,
)
from backend.app.utils.local_time import utcnow_naive

BOT_TOKEN = "123456:AAbbCC"
CHAT_ID = "-1002520100736"
TELEGRAM_CONFIG = {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
THUMBS_UP = "\U0001f44d"
THUMBS_DOWN = "\U0001f44e"


class _FakeBotApi:
    """Stand-in for httpx.AsyncClient with scripted responses, in call order."""

    def __init__(self, responses=None):
        self.is_closed = False
        self.calls: list[dict] = []
        self.responses = list(responses or [])

    async def post(self, url, json=None, data=None, files=None):
        self.calls.append({"url": url, "json": json, "data": data, "files": files})
        if self.responses:
            nxt = self.responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return httpx.Response(200, json={"ok": True, "result": []})

    async def aclose(self):
        self.is_closed = True

    def urls(self) -> list[str]:
        return [c["url"].rsplit("/", 1)[-1] for c in self.calls]


def _updates(*updates: dict) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": list(updates)})


def _reaction(update_id: int, message_id: int, emoji: str | None, chat_id: str = CHAT_ID, kind: str = "emoji") -> dict:
    new_reaction = [] if emoji is None else [{"type": kind, "emoji": emoji}]
    return {
        "update_id": update_id,
        "message_reaction": {
            "chat": {"id": int(chat_id)},
            "message_id": message_id,
            "user": {"id": 42},
            "date": 1_700_000_000,
            "old_reaction": [],
            "new_reaction": new_reaction,
        },
    }


@pytest.fixture
async def poller(test_engine):
    p = TelegramReactionPoller()
    p._session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    p._http_client = _FakeBotApi()
    yield p
    await p.aclose()


def _track(poller: TelegramReactionPoller, *providers, token: str = BOT_TOKEN):
    """What sync() would record for these providers, without starting a task."""
    poller._providers.setdefault(token, set()).update(p.id for p in providers)


@pytest.fixture
def telegram_provider(notification_provider_factory):
    async def _create(**kwargs):
        defaults = {
            "name": "Telegram",
            "provider_type": "telegram",
            "config": TELEGRAM_CONFIG,
            "telegram_verdict_mode": "reactions",
        }
        defaults.update(kwargs)
        return await notification_provider_factory(**defaults)

    return _create


@pytest.fixture
def pending_factory(db_session):
    async def _create(provider_id: int, archive_id: int, message_id: int = 777, **kwargs):
        defaults = {
            "provider_id": provider_id,
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "archive_id": archive_id,
            "has_caption": False,
            "message_text": "*Outcome?*\nTest_Print on X1C",
        }
        defaults.update(kwargs)
        row = TelegramPendingVerdict(**defaults)
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _create


async def _pending_count(db_session) -> int:
    return len((await db_session.execute(select(TelegramPendingVerdict))).scalars().all())


# ---------------------------------------------------------------------------
# Shared verdict helper
# ---------------------------------------------------------------------------


class TestApplyOutcomeVerdict:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_first_verdict_wins_and_mirrors(self, db_session, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="tok")

        assert await apply_outcome_verdict(db_session, archive, "reject", reason="warping") is True
        await db_session.commit()
        assert archive.user_verdict == "reject"
        assert archive.failure_reason == "warping"
        assert archive.confirm_token is None
        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry.user_verdict == "reject"
        assert entry.failure_reason == "warping"

        # A later verdict from any path is a no-op.
        assert await apply_outcome_verdict(db_session, archive, "good") is False
        assert archive.user_verdict == "reject"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_rejects_unknown_verdict(self, db_session, printer_factory, archive_factory):
        printer = await printer_factory()
        archive = await archive_factory(printer.id)
        with pytest.raises(ValueError):
            await apply_outcome_verdict(db_session, archive, "meh")


# ---------------------------------------------------------------------------
# Sending the prompt
# ---------------------------------------------------------------------------


class _SendCapture:
    def __init__(self, message_id=555):
        self.is_closed = False
        self.calls: list[dict] = []
        self.message_id = message_id

    async def post(self, url, data=None, files=None, json=None):
        self.calls.append({"url": url, "data": data, "files": files, "json": json})
        result = {"message_id": self.message_id, "chat": {"id": int(CHAT_ID)}}
        return httpx.Response(200, json={"ok": True, "result": result})


CONFIRM_VARIABLES = {"good_url": "http://bambuddy.local/good", "reject_url": "http://bambuddy.local/reject"}


class TestSendingThePrompt:
    async def _send(self, provider, db_session, archive_id, image_data=None):
        service = NotificationService()
        client = _SendCapture()
        service._http_client = client
        ok, _ = await service._send_to_provider(
            provider,
            "Outcome?",
            "How did it go",
            db_session,
            image_data=image_data,
            event_type="print_confirm_request",
            variables={**CONFIRM_VARIABLES, "archive_id": archive_id},
        )
        assert ok
        return client

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reactions_mode_drops_buttons_and_records_message(
        self, db_session, printer_factory, archive_factory, telegram_provider
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider(telegram_verdict_mode="reactions")

        client = await self._send(provider, db_session, archive.id)

        body = client.calls[0]["json"]
        assert "reply_markup" not in body
        assert TELEGRAM_REACTION_HINT in body["text"]

        row = await db_session.scalar(select(TelegramPendingVerdict))
        assert row is not None
        assert row.provider_id == provider.id
        assert row.archive_id == archive.id
        assert row.message_id == 555
        assert row.chat_id == CHAT_ID
        assert row.has_caption is False
        assert row.message_text and TELEGRAM_REACTION_HINT in row.message_text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_both_mode_keeps_buttons_and_records_message(
        self, db_session, printer_factory, archive_factory, telegram_provider
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider(telegram_verdict_mode="both")

        client = await self._send(provider, db_session, archive.id, image_data=b"\x89PNG")

        call = client.calls[0]
        assert call["url"].endswith("/sendPhoto")
        assert "inline_keyboard" in call["data"]["reply_markup"]
        row = await db_session.scalar(select(TelegramPendingVerdict))
        assert row is not None and row.has_caption is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_buttons_mode_is_unchanged(self, db_session, printer_factory, archive_factory, telegram_provider):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider(telegram_verdict_mode="buttons")

        client = await self._send(provider, db_session, archive.id)

        body = client.calls[0]["json"]
        assert body["reply_markup"] == {
            "inline_keyboard": [
                [
                    {"text": f"{THUMBS_UP} Good", "url": CONFIRM_VARIABLES["good_url"]},
                    {"text": f"{THUMBS_DOWN} Reject", "url": CONFIRM_VARIABLES["reject_url"]},
                ]
            ]
        }
        assert TELEGRAM_REACTION_HINT not in body["text"]
        assert await _pending_count(db_session) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_missing_message_id_records_nothing(
        self, db_session, printer_factory, archive_factory, telegram_provider
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider(telegram_verdict_mode="reactions")

        service = NotificationService()
        client = _SendCapture(message_id=0)
        service._http_client = client
        ok, _ = await service._send_to_provider(
            provider,
            "Outcome?",
            "How did it go",
            db_session,
            event_type="print_confirm_request",
            variables={**CONFIRM_VARIABLES, "archive_id": archive.id},
        )
        assert ok
        assert await _pending_count(db_session) == 0


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


class TestVerdictFromReaction:
    def test_thumbs_map_and_others_ignored(self):
        assert verdict_from_reaction([{"type": "emoji", "emoji": THUMBS_UP}]) == "good"
        assert verdict_from_reaction([{"type": "emoji", "emoji": THUMBS_DOWN}]) == "reject"
        assert verdict_from_reaction([{"type": "emoji", "emoji": "❤"}]) is None
        assert verdict_from_reaction([{"type": "custom_emoji", "custom_emoji_id": "1"}]) is None
        assert verdict_from_reaction([]) is None
        assert verdict_from_reaction([{"type": "emoji", "emoji": "❤"}, {"type": "emoji", "emoji": THUMBS_UP}]) == "good"


class TestPoller:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_thumbs_up_marks_good_deletes_row_and_edits_message(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, confirm_token="tok")
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=777)
        poller._http_client = _FakeBotApi([_updates(_reaction(10, 777, THUMBS_UP))])

        assert await poller.poll_once(BOT_TOKEN) == "ok"

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert archive.confirm_token is None
        entry = await db_session.scalar(
            select(PrintLogEntry).where(PrintLogEntry.archive_id == archive.id).order_by(PrintLogEntry.id.desc())
        )
        assert entry.user_verdict == "good"
        assert await _pending_count(db_session) == 0

        assert poller._http_client.urls() == ["getUpdates", "editMessageText"]
        edit = poller._http_client.calls[1]["json"]
        assert edit["chat_id"] == CHAT_ID and edit["message_id"] == 777
        assert edit["text"].endswith("✅ marked as good")
        assert "Test\\_Print" in edit["text"], "stored body is re-escaped for Markdown"
        assert "reply_markup" not in edit
        # The getUpdates that follows confirms the update we handled.
        assert poller._offsets[BOT_TOKEN] == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_getupdates_request_shape(self, poller, telegram_provider):
        provider = await telegram_provider()
        _track(poller, provider)
        poller._offsets[BOT_TOKEN] = 41
        await poller.poll_once(BOT_TOKEN)
        call = poller._http_client.calls[0]
        assert call["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        assert call["json"] == {"timeout": 50, "allowed_updates": ["message_reaction"], "offset": 42}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_offset_belongs_to_the_bot_not_the_provider(self, poller, telegram_provider):
        """update_ids are a per-bot sequence: after a provider is re-pointed at
        another bot, the first getUpdates for it must not carry the old bot's
        offset, or the new bot's lower ids are confirmed away unseen."""
        provider = await telegram_provider()
        _track(poller, provider)
        poller._offsets[BOT_TOKEN] = 900_000_123

        _track(poller, provider, token="999:newtoken")
        await poller.poll_once("999:newtoken")

        call = poller._http_client.calls[0]
        assert call["url"].startswith("https://api.telegram.org/bot999:newtoken/")
        assert "offset" not in call["json"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_thumbs_down_rejects(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=778)
        poller._http_client = _FakeBotApi([_updates(_reaction(11, 778, THUMBS_DOWN))])

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"
        assert poller._http_client.calls[1]["json"]["text"].endswith("❌ marked as reject")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_caption_message_is_edited_with_editMessageCaption(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=779, has_caption=True)
        poller._http_client = _FakeBotApi([_updates(_reaction(12, 779, THUMBS_UP))])

        await poller.poll_once(BOT_TOKEN)

        assert poller._http_client.urls() == ["getUpdates", "editMessageCaption"]
        assert poller._http_client.calls[1]["json"]["caption"].endswith("✅ marked as good")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_message_is_ignored(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=780)
        poller._http_client = _FakeBotApi(
            [_updates(_reaction(13, 999, THUMBS_UP), _reaction(14, 780, THUMBS_UP, chat_id="-100999"))]
        )

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert await _pending_count(db_session) == 1
        assert poller._http_client.urls() == ["getUpdates"]
        assert poller._offsets[BOT_TOKEN] == 14

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_other_emoji_and_removed_reaction_are_ignored(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=781)
        poller._http_client = _FakeBotApi([_updates(_reaction(15, 781, "❤"), _reaction(16, 781, None))])

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert await _pending_count(db_session) == 1, "the prompt stays open for a later thumbs"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reaction_after_a_verdict_is_a_noop(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        """Someone already answered in the web UI: the late reaction neither
        flips the verdict nor edits the message, but the row is retired."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True, user_verdict="reject")
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=782)
        poller._http_client = _FakeBotApi([_updates(_reaction(17, 782, THUMBS_UP))])

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict == "reject"
        assert await _pending_count(db_session) == 0
        assert poller._http_client.urls() == ["getUpdates"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_second_reaction_on_same_message_is_a_noop(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=783)
        poller._http_client = _FakeBotApi(
            [_updates(_reaction(18, 783, THUMBS_UP)), _updates(_reaction(19, 783, THUMBS_DOWN))]
        )

        await poller.poll_once(BOT_TOKEN)
        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert poller._http_client.urls() == ["getUpdates", "editMessageText", "getUpdates"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_failed_edit_does_not_lose_the_verdict(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=784)
        poller._http_client = _FakeBotApi(
            [_updates(_reaction(20, 784, THUMBS_UP)), httpx.ConnectError("telegram gone")]
        )

        assert await poller.poll_once(BOT_TOKEN) == "ok"

        await db_session.refresh(archive)
        assert archive.user_verdict == "good"
        assert await _pending_count(db_session) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_conflict_is_surfaced_and_cooled_down(self, poller, db_session, telegram_provider):
        """409 = webhook set or a second poller: report it on the provider and
        wait the cooldown instead of looping on the next getUpdates."""
        provider = await telegram_provider()
        _track(poller, provider)
        conflict = httpx.Response(
            409,
            json={"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"},
        )
        poller._http_client = _FakeBotApi([conflict, conflict])

        assert await poller.poll_once(BOT_TOKEN) == "conflict"

        await db_session.refresh(provider)
        assert "Conflict: terminated by other getUpdates request" in provider.last_error
        assert "webhook" in provider.last_error
        assert provider.last_error_at is not None

        sleeps: list[float] = []

        async def _sleep(seconds):
            sleeps.append(seconds)
            raise CancelledForTest

        poller._sleep = _sleep
        with pytest.raises(CancelledForTest):
            await poller._run(BOT_TOKEN)
        assert sleeps == [CONFLICT_COOLDOWN]
        assert len(poller._http_client.calls) == 2, "one getUpdates per cooldown, not a tight loop"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bad_bot_token_is_surfaced_and_cooled_down(self, poller, db_session, telegram_provider):
        """401/404 = wrong or deleted bot token: it never fixes itself, so it
        lands on the provider card and waits the cooldown instead of a warning
        every minute forever."""
        provider = await telegram_provider()
        _track(poller, provider)
        unauthorized = httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        poller._http_client = _FakeBotApi([unauthorized, unauthorized])

        assert await poller.poll_once(BOT_TOKEN) == "rejected"

        await db_session.refresh(provider)
        assert "HTTP 401" in provider.last_error
        assert "Unauthorized" in provider.last_error
        assert "bot token" in provider.last_error
        assert provider.last_error_at is not None

        sleeps: list[float] = []

        async def _sleep(seconds):
            sleeps.append(seconds)
            raise CancelledForTest

        poller._sleep = _sleep
        with pytest.raises(CancelledForTest):
            await poller._run(BOT_TOKEN)
        assert sleeps == [CONFLICT_COOLDOWN]
        assert len(poller._http_client.calls) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_conflict_is_recorded_on_every_provider_of_the_bot(self, poller, db_session, telegram_provider):
        first = await telegram_provider(name="X1C")
        second = await telegram_provider(name="P1S")
        _track(poller, first, second)
        conflict = httpx.Response(409, json={"ok": False, "error_code": 409, "description": "Conflict"})
        poller._http_client = _FakeBotApi([conflict])

        assert await poller.poll_once(BOT_TOKEN) == "conflict"

        for provider in (first, second):
            await db_session.refresh(provider)
            assert provider.last_error and "Conflict" in provider.last_error

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_network_errors_back_off_exponentially(self, poller, telegram_provider):
        provider = await telegram_provider()
        _track(poller, provider)
        poller._http_client = _FakeBotApi([httpx.ConnectError("down")] * 10)
        sleeps: list[float] = []

        async def _sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 8:
                raise CancelledForTest

        poller._sleep = _sleep
        with pytest.raises(CancelledForTest):
            await poller._run(BOT_TOKEN)
        assert sleeps == [1, 2, 4, 8, 16, 32, 60, 60]
        assert max(sleeps) == MAX_BACKOFF

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_prune_drops_rows_older_than_a_week(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        _track(poller, provider)
        await pending_factory(provider.id, archive.id, message_id=1, created_at=utcnow_naive() - timedelta(days=8))
        fresh = await pending_factory(provider.id, archive.id, message_id=2)

        await poller.prune_stale()

        rows = (await db_session.execute(select(TelegramPendingVerdict))).scalars().all()
        assert [r.id for r in rows] == [fresh.id]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_sync_follows_provider_mode_enabled_and_token(self, poller, db_session, telegram_provider):
        never = _NeverAnswers()
        poller._http_client = never
        reactions = await telegram_provider(name="R", telegram_verdict_mode="reactions")
        buttons = await telegram_provider(name="B", telegram_verdict_mode="buttons")
        disabled = await telegram_provider(name="D", telegram_verdict_mode="both", enabled=False)

        await poller.sync()
        assert poller.is_polling(reactions.id)
        assert not poller.is_polling(buttons.id)
        assert not poller.is_polling(disabled.id)
        assert set(poller._tasks) == {BOT_TOKEN}

        # The bot keeps its single poll while any provider wants it; the
        # provider set behind it follows the edits.
        first_task = poller._tasks[BOT_TOKEN]
        reactions.telegram_verdict_mode = "buttons"
        buttons.telegram_verdict_mode = "both"
        await db_session.commit()
        await poller.sync()
        assert not poller.is_polling(reactions.id)
        assert poller.is_polling(buttons.id)
        assert poller._tasks[BOT_TOKEN] is first_task
        assert poller._providers[BOT_TOKEN] == {buttons.id}

        # A token change moves the provider to a new bot: the old bot's poll
        # stops because nobody uses it any more, a fresh one starts.
        buttons.config = '{"bot_token": "999:newtoken", "chat_id": "1"}'
        await db_session.commit()
        await poller.sync()
        assert first_task.cancelled() or first_task.cancelling()
        assert set(poller._tasks) == {"999:newtoken"}
        assert poller.is_polling(buttons.id)

        await poller.aclose()
        assert poller._tasks == {}
        assert never.is_closed

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_providers_sharing_a_bot_share_one_poll(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        """The usual farm layout: one bot, one provider per printer. Telegram
        serves a single getUpdates consumer per bot, so both providers ride
        one task and a reaction to either prompt lands on its archive."""
        poller._http_client = _NeverAnswers()
        x1c = await printer_factory(name="X1C")
        p1s = await printer_factory(name="P1S")
        first = await telegram_provider(name="X1C", printer_id=x1c.id, config={"bot_token": BOT_TOKEN, "chat_id": "1"})
        second = await telegram_provider(name="P1S", printer_id=p1s.id, config={"bot_token": BOT_TOKEN, "chat_id": "2"})

        await poller.sync()
        assert len(poller._tasks) == 1
        assert poller.is_polling(first.id) and poller.is_polling(second.id)
        poller.stop()
        _track(poller, first, second)

        archive_a = await archive_factory(x1c.id, confirm_requested=True)
        archive_b = await archive_factory(p1s.id, confirm_requested=True)
        await pending_factory(first.id, archive_a.id, message_id=100, chat_id="1")
        await pending_factory(second.id, archive_b.id, message_id=100, chat_id="2")
        poller._http_client = _FakeBotApi(
            [_updates(_reaction(30, 100, THUMBS_DOWN, chat_id="2"), _reaction(31, 100, THUMBS_UP, chat_id="1"))]
        )

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive_a)
        await db_session.refresh(archive_b)
        assert archive_a.user_verdict == "good"
        assert archive_b.user_verdict == "reject"
        assert await _pending_count(db_session) == 0
        assert poller._http_client.urls() == ["getUpdates", "editMessageText", "editMessageText"]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_reaction_for_another_bots_provider_is_not_matched(
        self, poller, db_session, printer_factory, archive_factory, telegram_provider, pending_factory
    ):
        """Two bots in private chat with the same person number their
        messages independently, so a match is scoped to the bot's providers."""
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        other = await telegram_provider(name="other bot", config={"bot_token": "999:other", "chat_id": CHAT_ID})
        mine = await telegram_provider(name="mine")
        _track(poller, mine)
        _track(poller, other, token="999:other")
        await pending_factory(other.id, archive.id, message_id=5)
        poller._http_client = _FakeBotApi([_updates(_reaction(40, 5, THUMBS_UP))])

        await poller.poll_once(BOT_TOKEN)

        await db_session.refresh(archive)
        assert archive.user_verdict is None
        assert await _pending_count(db_session) == 1


class CancelledForTest(BaseException):
    """Escape hatch out of the endless poll loop without tripping its handlers."""


class _NeverAnswers:
    """getUpdates that stays open forever, like a quiet chat would."""

    def __init__(self):
        self.is_closed = False

    async def post(self, url, json=None, data=None, files=None):
        import asyncio

        await asyncio.Event().wait()

    async def aclose(self):
        self.is_closed = True


# ---------------------------------------------------------------------------
# Provider API round trip
# ---------------------------------------------------------------------------


class TestProviderApi:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_mode_round_trips_and_defaults_to_buttons(self, async_client: AsyncClient):
        with patch("backend.app.api.routes.notifications.telegram_reaction_poller.sync", new=AsyncMock()) as sync:
            created = await async_client.post(
                "/api/v1/notifications/",
                json={
                    "name": "TG",
                    "provider_type": "telegram",
                    "config": TELEGRAM_CONFIG,
                    "telegram_verdict_mode": "reactions",
                },
            )
            assert created.status_code == 200, created.text
            assert created.json()["telegram_verdict_mode"] == "reactions"
            provider_id = created.json()["id"]
            assert sync.await_count == 1

            listed = await async_client.get("/api/v1/notifications/")
            assert [p["telegram_verdict_mode"] for p in listed.json() if p["id"] == provider_id] == ["reactions"]

            patched = await async_client.patch(
                f"/api/v1/notifications/{provider_id}", json={"telegram_verdict_mode": "both"}
            )
            assert patched.status_code == 200
            assert patched.json()["telegram_verdict_mode"] == "both"
            assert sync.await_count == 2

            invalid = await async_client.patch(
                f"/api/v1/notifications/{provider_id}", json={"telegram_verdict_mode": "webhook"}
            )
            assert invalid.status_code == 422

            other = await async_client.post(
                "/api/v1/notifications/",
                json={"name": "ntfy", "provider_type": "ntfy", "config": {"topic": "t"}},
            )
            assert other.json()["telegram_verdict_mode"] == "buttons"

            deleted = await async_client.delete(f"/api/v1/notifications/{provider_id}")
            assert deleted.status_code == 200
            assert sync.await_count == 4

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_legacy_null_mode_reads_as_buttons(
        self, async_client: AsyncClient, notification_provider_factory, db_session
    ):
        provider = await notification_provider_factory(name="Legacy")
        # The ORM default fills a None at INSERT; a pre-#3046 row holds a real NULL.
        await db_session.execute(
            text("UPDATE notification_providers SET telegram_verdict_mode = NULL WHERE id = :id"), {"id": provider.id}
        )
        await db_session.commit()
        stored = await db_session.scalar(
            text("SELECT telegram_verdict_mode FROM notification_providers WHERE id = :id"), {"id": provider.id}
        )
        assert stored is None, "row under test must actually hold NULL"

        response = await async_client.get(f"/api/v1/notifications/{provider.id}")
        assert response.status_code == 200
        assert response.json()["telegram_verdict_mode"] == "buttons"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_a_provider_cascades_its_pending_rows(
        self,
        async_client: AsyncClient,
        db_session,
        printer_factory,
        archive_factory,
        telegram_provider,
        pending_factory,
    ):
        printer = await printer_factory()
        archive = await archive_factory(printer.id, confirm_requested=True)
        provider = await telegram_provider()
        await pending_factory(provider.id, archive.id)

        with patch("backend.app.api.routes.notifications.telegram_reaction_poller.sync", new=AsyncMock()):
            assert (await async_client.delete(f"/api/v1/notifications/{provider.id}")).status_code == 200

        assert await _pending_count(db_session) == 0
