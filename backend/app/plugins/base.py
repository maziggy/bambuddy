"""Base classes and mixins for Bambuddy plugins, inspired by OctoPrint's plugin system.

A plugin is a Python package (or single .py file) placed in the plugins/ directory.
It must expose a module-level ``__plugin_implementation__`` that is an instance of
a class that inherits from :class:`BambuddyPlugin` and any number of mixin classes.

Minimal example::

    # plugins/my_plugin/__init__.py
    from backend.app.plugins.base import BambuddyPlugin, EventHandlerPlugin, Events

    __plugin_name__ = "My Plugin"
    __plugin_version__ = "1.0.0"
    __plugin_description__ = "Does something useful."
    __plugin_author__ = "Your Name"

    class MyPlugin(BambuddyPlugin, EventHandlerPlugin):
        async def on_event(self, event, payload):
            if event == Events.PRINT_DONE:
                print(f"Print finished: {payload.get('filename')}")

    __plugin_implementation__ = MyPlugin()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.app.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


class BambuddyPlugin:
    """Base class for all Bambuddy plugins.

    Every plugin implementation must inherit from this class, plus any
    combination of the mixin classes below.
    """

    # Injected by the loader after instantiation
    _plugin_key: str = ""
    _plugin_registry: "PluginRegistry | None" = None

    def get_plugin_name(self) -> str:
        """Human-readable display name."""
        return self.__class__.__module__

    def get_plugin_version(self) -> str:
        return "0.0.1"

    def get_plugin_description(self) -> str:
        return ""

    def get_plugin_author(self) -> str:
        return ""

    def initialize(self) -> None:
        """Called once after the plugin is loaded and identity attributes are set.

        Override to perform one-time setup that needs ``_plugin_key`` or
        ``_plugin_registry``.
        """


# ---------------------------------------------------------------------------
# Mixin interfaces (mirrors OctoPrint naming for familiarity)
# ---------------------------------------------------------------------------

class StartupPlugin:
    """Run code after Bambuddy has fully started."""

    async def on_after_startup(self) -> None:
        """Called once after all services are initialised."""


class ShutdownPlugin:
    """Run code before Bambuddy shuts down."""

    async def on_before_shutdown(self) -> None:
        """Called once just before services stop."""


class EventHandlerPlugin:
    """React to Bambuddy lifecycle events.

    Implement :meth:`on_event` to receive all fired events. Compare the
    ``event`` argument against :class:`Events` constants.
    """

    async def on_event(self, event: str, payload: dict[str, Any]) -> None:
        """Called for every event fired by Bambuddy.

        Args:
            event:   Event name constant, e.g. ``Events.PRINT_STARTED``.
            payload: Event-specific data dictionary.
        """


class SettingsPlugin:
    """Expose user-configurable settings stored in the Bambuddy database.

    Settings for each plugin are stored as a JSON blob under the plugin's key.
    Use :meth:`get_settings` and :meth:`save_setting` (injected helpers) to
    read/write them at runtime.

    Override :meth:`get_settings_defaults` to declare keys and their defaults;
    the stored values are merged on top of defaults when read.
    """

    def get_settings_defaults(self) -> dict[str, Any]:
        """Return default values for all settings this plugin exposes.

        Example::

            return {"temperature_threshold": 60, "notify": True}
        """
        return {}


class SimpleApiPlugin:
    """Expose a simple command-based HTTP API endpoint.

    Commands are reachable at::

        POST /api/v1/plugins/<plugin_key>/command
        {"command": "<name>", "param1": ..., "param2": ...}

    A GET endpoint is also available::

        GET  /api/v1/plugins/<plugin_key>
    """

    def get_api_commands(self) -> dict[str, list[str]]:
        """Return a mapping of command name -> list of required parameter names.

        Example::

            return {"set_temp": ["target"], "reset": []}
        """
        return {}

    async def on_api_command(self, command: str, data: dict[str, Any]) -> dict[str, Any]:
        """Handle a POST command. Return a JSON-serialisable dict."""
        return {}

    async def on_api_get(self) -> dict[str, Any]:
        """Handle GET /api/v1/plugins/<plugin_key>. Return a JSON-serialisable dict."""
        return {}


class AssetPlugin:
    """Serve static assets (JS / CSS) from the plugin's ``static/`` directory.

    Files declared here are accessible at::

        /plugin-assets/<plugin_key>/js/<file>
        /plugin-assets/<plugin_key>/css/<file>
    """

    def get_assets(self) -> dict[str, list[str]]:
        """Return asset filenames relative to the plugin's ``static/`` folder.

        Example::

            return {"js": ["plugin.js"], "css": ["plugin.css"]}
        """
        return {}


# ---------------------------------------------------------------------------
# Standard event names
# ---------------------------------------------------------------------------

class Events:
    """Standard event names fired by the Bambuddy event system.

    These deliberately mirror OctoPrint's event names so that porting
    OctoPrint plugin knowledge requires minimal adjustment.
    """

    # Printer connectivity
    CONNECTED = "Connected"
    DISCONNECTED = "Disconnected"
    PRINTER_ADDED = "PrinterAdded"
    PRINTER_REMOVED = "PrinterRemoved"

    # Print lifecycle — matches OctoPrint naming
    PRINT_STARTED = "PrintStarted"
    PRINT_DONE = "PrintDone"
    PRINT_FAILED = "PrintFailed"
    PRINT_CANCELLED = "PrintCancelled"
    PRINT_PAUSED = "PrintPaused"
    PRINT_RESUMED = "PrintResumed"
    PRINT_PROGRESS = "PrintProgress"

    # Archiving
    ARCHIVE_CREATED = "ArchiveCreated"
    ARCHIVE_UPDATED = "ArchiveUpdated"

    # Application lifecycle
    STARTUP = "Startup"
    SHUTDOWN = "Shutdown"

    # Settings
    SETTINGS_UPDATED = "SettingsUpdated"
