"""Answer the post-print outcome prompt with a Telegram reaction (#3046).

Reactions arrive as ``message_reaction`` updates, which the Bot API only
hands out through ``getUpdates`` (long polling) or a webhook. Bambuddy
polls: no inbound connectivity, no public URL, no certificate — the same
outbound-only footing every other notification provider works from. One
task per distinct bot token: several per-printer providers usually share a
bot, and Telegram allows a single getUpdates consumer per bot (a second one
gets a 409). The notifications routes resync the set whenever a provider
changes.

Reactions set by bots are never delivered by Telegram, and in groups the
bot has to be an administrator to receive them at all — both documented Bot
API behaviour, nothing to work around here.
"""

import asyncio
import json
import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

from backend.app.models.archive import PrintArchive
from backend.app.models.notification import NotificationProvider, TelegramPendingVerdict
from backend.app.services.notification_service import _USER_AGENT, telegram_markdown_escape
from backend.app.services.print_confirmation import apply_outcome_verdict
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

# Telegram holds a getUpdates call open for up to this long before answering
# with an empty list. The HTTP read timeout below has to outlast it.
GET_UPDATES_TIMEOUT = 50
# A 409 means a webhook is set for the bot or a second poller is running; a
# 401/404 means the bot token is wrong or the bot was deleted. None of them
# clears by retrying, so the loop backs off well beyond the poll interval
# instead of hammering the API every few seconds.
CONFLICT_COOLDOWN = 300
MAX_BACKOFF = 60
PENDING_TTL = timedelta(days=7)
PRUNE_INTERVAL = 3600

REACTION_VERDICTS = {"\U0001f44d": "good", "\U0001f44e": "reject"}
VERDICT_SUFFIX = {"good": "✅ marked as good", "reject": "❌ marked as reject"}


def verdict_from_reaction(new_reaction: list) -> str | None:
    """Map the reaction list of a message_reaction update to a verdict.

    Only plain emoji reactions count; custom emoji and paid reactions are
    ignored. The first thumbs in the list wins when several are set.
    """
    for reaction in new_reaction or []:
        if not isinstance(reaction, dict) or reaction.get("type") != "emoji":
            continue
        verdict = REACTION_VERDICTS.get(reaction.get("emoji", ""))
        if verdict:
            return verdict
    return None


def _provider_reaction_token(provider: NotificationProvider) -> str | None:
    """Bot token of a provider that should be polled, else None."""
    if provider.provider_type != "telegram" or not provider.enabled:
        return None
    if (provider.telegram_verdict_mode or "buttons") == "buttons":
        return None
    config = json.loads(provider.config) if isinstance(provider.config, str) else (provider.config or {})
    token = str(config.get("bot_token") or "").strip()
    return token or None


def _bot_label(bot_token: str) -> str:
    """Log-safe name for a bot: the numeric id in front of the secret part."""
    return f"bot {bot_token.split(':', 1)[0]}"


class TelegramReactionPoller:
    def __init__(self):
        # Everything is keyed by bot token, not provider: update_ids are a
        # per-bot sequence and Telegram serves one getUpdates consumer per
        # bot, so providers sharing a bot share one poll.
        self._tasks: dict[str, asyncio.Task] = {}
        # bot token -> ids of the providers whose prompts this poll answers.
        self._providers: dict[str, set[int]] = {}
        # bot token -> last update_id confirmed. Kept across task restarts
        # within the process so a resync does not re-fetch handled updates.
        self._offsets: dict[str, int] = {}
        self._http_client: httpx.AsyncClient | None = None
        self._session_factory = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        await self.sync()

    def stop(self) -> list[asyncio.Task]:
        """Cancel every poll. Returns the cancelled tasks so aclose() can wait for them."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            logger.info("Telegram reaction poller stopped (%d bot(s))", len(tasks))
        self._tasks.clear()
        self._providers.clear()
        return tasks

    async def aclose(self):
        """Shutdown: stop the polls, let them unwind, and release the HTTP client."""
        tasks = self.stop()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    async def sync(self):
        """Reconcile the running tasks with the providers in the database.

        Called at startup and after every provider create/update/delete.
        Starts one task per bot token that at least one enabled provider in
        reactions/both mode uses, re-points a running task at the providers
        that now share its bot, and cancels the task of a bot no provider
        wants any more (deleted, disabled, token changed, or switched back
        to buttons). A running task never restarts on a provider edit alone.
        """
        async with self._sessions()() as db:
            result = await db.execute(
                select(NotificationProvider).where(NotificationProvider.provider_type == "telegram")
            )
            providers = list(result.scalars().all())

        wanted: dict[str, set[int]] = {}
        for provider in providers:
            token = _provider_reaction_token(provider)
            if token:
                wanted.setdefault(token, set()).add(provider.id)

        for token in list(self._tasks):
            if token not in wanted:
                self._tasks.pop(token).cancel()
                self._providers.pop(token, None)
                logger.info("Telegram reaction poll stopped for %s", _bot_label(token))

        for token, provider_ids in wanted.items():
            self._providers[token] = provider_ids
            if token not in self._tasks:
                self._tasks[token] = asyncio.create_task(
                    self._run(token), name=f"telegram-reactions-{token.split(':', 1)[0]}"
                )
                logger.info(
                    "Telegram reaction poll started for %s (provider(s) %s)",
                    _bot_label(token),
                    ", ".join(str(i) for i in sorted(provider_ids)),
                )

    def is_polling(self, provider_id: int) -> bool:
        return any(provider_id in ids for token, ids in self._providers.items() if token in self._tasks)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory
        from backend.app.core.database import async_session

        return async_session

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(GET_UPDATES_TIMEOUT + 10.0, connect=5.0),
                headers={"User-Agent": _USER_AGENT},
            )
        return self._http_client

    async def _sleep(self, seconds: float):
        await asyncio.sleep(seconds)

    async def _run(self, bot_token: str):
        backoff = 1.0
        last_prune: float | None = None
        while True:
            try:
                if last_prune is None or time.monotonic() - last_prune > PRUNE_INTERVAL:
                    await self.prune_stale()
                    last_prune = time.monotonic()
                status = await self.poll_once(bot_token)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Telegram reaction poll failed for %s: %s", _bot_label(bot_token), e)
                await self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
            backoff = 1.0
            if status != "ok":
                await self._sleep(CONFLICT_COOLDOWN)

    # ------------------------------------------------------------------
    # One poll
    # ------------------------------------------------------------------

    async def poll_once(self, bot_token: str) -> str:
        """One getUpdates round trip.

        Returns "ok", or "conflict" / "rejected" for the permanent failures
        (409 webhook or second poller; 401/404 bad or deleted bot token) that
        are surfaced on the providers and cooled down instead of retried.
        """
        params: dict = {"timeout": GET_UPDATES_TIMEOUT, "allowed_updates": ["message_reaction"]}
        offset = self._offsets.get(bot_token)
        if offset is not None:
            params["offset"] = offset + 1

        client = await self._get_client()
        response = await client.post(f"https://api.telegram.org/bot{bot_token}/getUpdates", json=params)

        if response.status_code in (401, 404, 409):
            description = ""
            try:
                description = response.json().get("description") or ""
            except Exception:
                pass
            if response.status_code == 409:
                status = "conflict"
                message = (
                    f"Telegram getUpdates conflict: {description or 'conflict'}. Reactions cannot be received "
                    f"while a webhook is set for this bot or another poller is running"
                )
            else:
                status = "rejected"
                detail = f": {description}" if description else ""
                message = (
                    f"Telegram rejected the bot token (HTTP {response.status_code}{detail}). "
                    f"Check the provider's bot token"
                )
            await self._record_error(bot_token, f"{message}; retrying in {CONFLICT_COOLDOWN // 60} minutes.")
            return status
        if response.status_code != 200:
            raise RuntimeError(f"getUpdates HTTP {response.status_code}")
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"getUpdates failed: {payload.get('description', 'unknown error')}")

        for update in payload.get("result") or []:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offsets[bot_token] = max(self._offsets.get(bot_token, update_id), update_id)
            reaction = update.get("message_reaction")
            if not isinstance(reaction, dict):
                continue
            try:
                await self._handle_reaction(bot_token, reaction)
            except Exception as e:
                logger.warning("Telegram reaction for %s could not be applied: %s", _bot_label(bot_token), e)
        return "ok"

    async def _record_error(self, bot_token: str, message: str):
        """Surface a permanent poll failure on every provider that uses the bot."""
        provider_ids = sorted(self._providers.get(bot_token, ()))
        logger.error("%s (provider(s) %s): %s", _bot_label(bot_token), provider_ids or "-", message)
        if not provider_ids:
            return
        try:
            async with self._sessions()() as db:
                result = await db.execute(select(NotificationProvider).where(NotificationProvider.id.in_(provider_ids)))
                for provider in result.scalars().all():
                    provider.last_error = message
                    provider.last_error_at = utcnow_naive()
                await db.commit()
        except Exception as e:
            logger.warning("Could not record the Telegram poll error on providers %s: %s", provider_ids, e)

    async def _handle_reaction(self, bot_token: str, reaction: dict):
        chat_id = str((reaction.get("chat") or {}).get("id", "")).strip()
        message_id = reaction.get("message_id")
        verdict = verdict_from_reaction(reaction.get("new_reaction") or [])
        provider_ids = self._providers.get(bot_token)
        if not chat_id or not isinstance(message_id, int) or verdict is None or not provider_ids:
            return

        async with self._sessions()() as db:
            # message_ids are unique per chat, but two bots talking to the
            # same person each see their own numbering, so the match stays
            # scoped to the providers of this bot.
            pending = await db.scalar(
                select(TelegramPendingVerdict).where(
                    TelegramPendingVerdict.provider_id.in_(sorted(provider_ids)),
                    TelegramPendingVerdict.chat_id == chat_id,
                    TelegramPendingVerdict.message_id == message_id,
                )
            )
            if pending is None:
                return

            archive = await db.get(PrintArchive, pending.archive_id)
            applied = archive is not None and await apply_outcome_verdict(db, archive, verdict)
            has_caption = bool(pending.has_caption)
            text = pending.message_text
            archive_id = pending.archive_id
            # Whatever happened to the archive, this message is answered.
            await db.delete(pending)
            await db.commit()

        if not applied:
            logger.info("Telegram reaction on archive %s ignored: verdict already recorded", archive_id)
            return
        logger.info("[#3046] Telegram reaction marked archive %s as '%s'", archive_id, verdict)
        await self._confirm_on_message(bot_token, chat_id, message_id, has_caption, text, verdict)

    async def _confirm_on_message(
        self, bot_token: str, chat_id: str, message_id: int, has_caption: bool, text: str | None, verdict: str
    ):
        """Append the verdict to the prompt so the chat shows it was taken.

        Editing also drops the inline keyboard in "both" mode — the links
        are dead once a verdict landed. Best effort: a failed edit is logged,
        the verdict itself is already committed.
        """
        suffix = VERDICT_SUFFIX[verdict]
        client = await self._get_client()
        try:
            if text:
                body = telegram_markdown_escape(f"{text}\n\n{suffix}")
                if has_caption:
                    method, field = "editMessageCaption", "caption"
                else:
                    method, field = "editMessageText", "text"
                payload = {"chat_id": chat_id, "message_id": message_id, field: body, "parse_mode": "Markdown"}
            else:
                method = "sendMessage"
                payload = {"chat_id": chat_id, "text": suffix, "reply_parameters": {"message_id": message_id}}
            response = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=payload)
            if response.status_code != 200 or not response.json().get("ok"):
                logger.warning("Telegram %s failed after reaction verdict: HTTP %s", method, response.status_code)
        except Exception as e:
            logger.warning("Telegram confirmation edit failed: %s", e)

    async def prune_stale(self):
        """Forget prompts nobody reacted to within a week."""
        async with self._sessions()() as db:
            await db.execute(
                delete(TelegramPendingVerdict).where(TelegramPendingVerdict.created_at < utcnow_naive() - PENDING_TTL)
            )
            await db.commit()


telegram_reaction_poller = TelegramReactionPoller()
