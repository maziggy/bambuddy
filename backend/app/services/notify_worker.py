"""Small lifecycle shared by the two opt-in Notify background workers.

Provider edits invalidate a cached enable flag. An unused integration therefore
has no periodic database work. Cleanup captures ownership without waiting for the
worker's HTTP/lock, then drains in the background; live row references retain a
resource ID returned by an already-running create after its provider is deleted.
"""

import asyncio
import json
import logging

from sqlalchemy import select

from backend.app.models.notification import NotificationProvider

logger = logging.getLogger(__name__)


class NotifyWorkerLifecycle:
    ownership_model = None
    feature_field = ""
    worker_interval = 60
    worker_name = "notify-worker"
    stopped_states = ("ended", "deleted")

    def _init_worker(self):
        self._provider_enabled: bool | None = None
        self._providers_dirty = True
        self._cleanup_once = False
        self._cleanup_pending = False
        self._initial_discovery = True
        self._change_epoch = 0
        self._retry_delay = 1
        self._wake = asyncio.Event()
        self._cleanup_jobs: dict[tuple[int, str], tuple[dict, list]] = {}
        self._retired: set[tuple[int, str]] = set()
        self._working_rows: list = []

    def _eligible(self, provider):
        config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
        return bool(
            provider.enabled
            and config.get(self.feature_field) is True
            and not str(config.get("device_id", "")).strip().upper().startswith(("GRP", "WB", "MC"))
        )

    def _on_activated(self):
        pass

    def providers_changed(self):
        """Fast notification after provider CRUD; performs no DB or HTTP work."""
        self._providers_dirty = True
        self._cleanup_once = True
        self._change_epoch += 1
        self._wake.set()

    def _retired_row(self, row):
        return (row.provider_id, row.credential_key) in self._retired

    async def schedule_cleanup(self, provider_id, old_config):
        """Capture cleanup before DELETE commits, or after a credential PATCH.

        Only a short database read is awaited. Never cancel an in-flight create:
        its response may be our only opportunity to learn the remote handle.
        """
        credential = self._cleanup_key(old_config)
        key = (provider_id, credential)
        self._retired.add(key)
        try:
            async with self._session() as db:
                persisted = (
                    await db.scalars(
                        select(self.ownership_model).where(
                            self.ownership_model.provider_id == provider_id,
                            self.ownership_model.credential_key == credential,
                        )
                    )
                ).all()
            previous = self._cleanup_jobs.get(key, ({}, []))[1]
            # Prefer the mutable worker object for each row. A create already
            # awaiting HTTP will assign its returned ID on this same object.
            captured = {row.id: row for row in [*persisted, *previous]}
            for row in self._working_rows:
                if row.provider_id == provider_id and row.credential_key == credential:
                    captured[row.id] = row
            self._cleanup_jobs[key] = (dict(old_config), list(captured.values()))
        except BaseException:
            if key not in self._cleanup_jobs:
                self._retired.discard(key)
            raise
        self.providers_changed()
        self.start()

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=self.worker_name)

    async def close(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http:
            await self._http.aclose()
            self._http = None
            self._client = None

    async def _run(self):
        while True:
            self._wake.clear()
            change_epoch = self._change_epoch
            try:
                if self._providers_dirty:
                    self._providers_dirty = False
                    async with self._session() as db:
                        providers = (
                            await db.scalars(
                                select(NotificationProvider).where(NotificationProvider.provider_type == "notify")
                            )
                        ).all()
                    previously_enabled = self._provider_enabled
                    self._provider_enabled = any(self._eligible(provider) for provider in providers)
                    if self._provider_enabled and previously_enabled is False:
                        self._on_activated()
                    if self._initial_discovery and not self._provider_enabled and providers:
                        async with self._session() as db:
                            pending = await db.scalar(
                                select(self.ownership_model.id)
                                .where(
                                    self.ownership_model.provider_id.in_([provider.id for provider in providers]),
                                    self.ownership_model.state.not_in(self.stopped_states),
                                )
                                .limit(1)
                            )
                        self._cleanup_pending = pending is not None
                    self._initial_discovery = False
                while self._cleanup_jobs:
                    key = next(iter(self._cleanup_jobs))
                    old_config, rows = self._cleanup_jobs.pop(key)
                    try:
                        await self.cleanup_provider(key[0], old_config, captured_rows=rows)
                    except Exception:
                        pending = self._cleanup_jobs.get(key, (old_config, []))[1]
                        self._cleanup_jobs[key] = (old_config, [*rows, *pending])
                        raise
                    finally:
                        if key not in self._cleanup_jobs:
                            self._retired.discard(key)
                if self._provider_enabled or self._cleanup_once or self._cleanup_pending:
                    await self.tick()
                if change_epoch == self._change_epoch:
                    self._cleanup_once = False
                self._retry_delay = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s reconciliation failed", self.worker_name)
                self._providers_dirty = True
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, 30)
                continue
            if self._providers_dirty or self._cleanup_jobs:
                continue
            if self._provider_enabled or self._cleanup_pending:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.worker_interval)
                except TimeoutError:
                    pass
            else:
                await self._wake.wait()
