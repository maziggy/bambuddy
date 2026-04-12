"""Example Bambuddy plugin — demonstrates all available mixin interfaces.

Drop this folder into your plugins/ directory and restart Bambuddy.
The plugin will appear at GET /api/v1/plugins and its API at
POST /api/v1/plugins/example_plugin/command.
"""

import logging

from backend.app.plugins.base import (
    BambuddyPlugin,
    EventHandlerPlugin,
    Events,
    SettingsPlugin,
    SimpleApiPlugin,
    StartupPlugin,
    ShutdownPlugin,
)

__plugin_name__ = "Example Plugin"
__plugin_version__ = "1.0.0"
__plugin_description__ = "Demonstrates the Bambuddy plugin system."
__plugin_author__ = "Bambuddy"

logger = logging.getLogger(__name__)


class ExamplePlugin(
    BambuddyPlugin,
    StartupPlugin,
    ShutdownPlugin,
    EventHandlerPlugin,
    SettingsPlugin,
    SimpleApiPlugin,
):
    def get_settings_defaults(self):
        return {
            "log_events": True,
            "greeting": "Hello from the example plugin!",
        }

    async def on_after_startup(self):
        logger.info("[ExamplePlugin] Bambuddy started. Plugin key: %s", self._plugin_key)

    async def on_before_shutdown(self):
        logger.info("[ExamplePlugin] Bambuddy is shutting down.")

    async def on_event(self, event: str, payload: dict):
        # Check the plugin's own setting — note: settings require a DB session,
        # so we just log here unconditionally for the example.
        logger.info("[ExamplePlugin] Event: %s | payload keys: %s", event, list(payload.keys()))

        if event == Events.PRINT_STARTED:
            filename = payload.get("filename", "unknown")
            logger.info("[ExamplePlugin] Print started: %s", filename)

        elif event == Events.PRINT_DONE:
            filename = payload.get("filename", "unknown")
            logger.info("[ExamplePlugin] Print finished successfully: %s", filename)

        elif event == Events.PRINT_FAILED:
            logger.warning("[ExamplePlugin] Print FAILED: %s", payload.get("filename"))

    # SimpleApiPlugin — accessible at POST /api/v1/plugins/example_plugin/command

    def get_api_commands(self):
        return {
            "greet": [],           # no required params
            "echo": ["message"],   # requires "message" param
        }

    async def on_api_command(self, command: str, data: dict):
        if command == "greet":
            return {"greeting": "Hello from ExamplePlugin!"}
        if command == "echo":
            return {"echo": data["message"]}
        return {}

    async def on_api_get(self):
        return {
            "plugin": __plugin_name__,
            "version": __plugin_version__,
            "status": "running",
        }


__plugin_implementation__ = ExamplePlugin()
