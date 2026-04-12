"""Plugin registry — stores loaded plugin instances and dispatches hooks/events."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from backend.app.plugins.base import (
    BambuddyPlugin,
    EventHandlerPlugin,
    SettingsPlugin,
    ShutdownPlugin,
    StartupPlugin,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Global registry of all loaded Bambuddy plugins.

    Holds plugin instances keyed by their plugin_key and provides
    event/hook dispatch and settings persistence helpers.
    """

    def __init__(self):
        # plugin_key -> plugin instance
        self._plugins: dict[str, BambuddyPlugin] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: BambuddyPlugin) -> None:
        key = plugin._plugin_key
        self._plugins[key] = plugin
        logger.info("Plugin registered: %s (%s)", key, type(plugin).__name__)

    def unregister(self, plugin_key: str) -> None:
        self._plugins.pop(plugin_key, None)

    @property
    def plugins(self) -> dict[str, BambuddyPlugin]:
        return dict(self._plugins)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def fire_startup(self) -> None:
        """Call on_after_startup on all StartupPlugin instances."""
        for key, plugin in list(self._plugins.items()):
            if isinstance(plugin, StartupPlugin):
                try:
                    await plugin.on_after_startup()
                except Exception:
                    logger.exception("Plugin %s raised in on_after_startup", key)

    async def fire_shutdown(self) -> None:
        """Call on_before_shutdown on all ShutdownPlugin instances."""
        for key, plugin in list(self._plugins.items()):
            if isinstance(plugin, ShutdownPlugin):
                try:
                    await plugin.on_before_shutdown()
                except Exception:
                    logger.exception("Plugin %s raised in on_before_shutdown", key)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def fire_event(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Fire an event to all EventHandlerPlugin instances concurrently.

        A misbehaving plugin cannot delay or crash other plugins — each
        handler runs inside its own exception guard.
        """
        if payload is None:
            payload = {}
        handlers = [
            (key, plugin)
            for key, plugin in self._plugins.items()
            if isinstance(plugin, EventHandlerPlugin)
        ]
        if not handlers:
            return
        await asyncio.gather(
            *(self._safe_event(key, plugin, event, payload) for key, plugin in handlers)
        )

    async def _safe_event(
        self,
        key: str,
        plugin: EventHandlerPlugin,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            await plugin.on_event(event, payload)
        except Exception:
            logger.exception("Plugin %s raised during on_event(%s)", key, event)

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------

    async def get_plugin_settings(self, plugin_key: str, db: "AsyncSession") -> dict[str, Any]:
        """Return settings for a plugin (defaults merged with DB-stored values)."""
        from sqlalchemy import select
        from backend.app.models.plugin import PluginRecord

        plugin = self._plugins.get(plugin_key)
        defaults: dict[str, Any] = (
            plugin.get_settings_defaults() if isinstance(plugin, SettingsPlugin) else {}
        )

        result = await db.execute(
            select(PluginRecord).where(PluginRecord.plugin_key == plugin_key)
        )
        record = result.scalar_one_or_none()
        stored: dict[str, Any] = {}
        if record and record.settings:
            try:
                stored = json.loads(record.settings)
            except Exception:
                pass
        return {**defaults, **stored}

    async def save_plugin_setting(
        self,
        plugin_key: str,
        key: str,
        value: Any,
        db: "AsyncSession",
    ) -> None:
        """Persist a single setting key for a plugin."""
        from sqlalchemy import select
        from backend.app.models.plugin import PluginRecord

        result = await db.execute(
            select(PluginRecord).where(PluginRecord.plugin_key == plugin_key)
        )
        record = result.scalar_one_or_none()
        if record is None:
            logger.warning("save_plugin_setting: no DB record for plugin '%s'", plugin_key)
            return
        stored: dict[str, Any] = {}
        if record.settings:
            try:
                stored = json.loads(record.settings)
            except Exception:
                pass
        stored[key] = value
        record.settings = json.dumps(stored)
        await db.commit()


# Global singleton — import this everywhere
plugin_registry = PluginRegistry()
