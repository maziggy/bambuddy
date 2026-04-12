# coding=utf-8
"""
PrettyGCode plugin for Bambuddy.

Converted from OctoPrint-PrettyGCode (https://github.com/Soopahfly/OctoPrint-PrettyGCode)
Original author: Kragrathea, maintained by soopahfly.

Conversion notes:
  - Converted: StartupPlugin, SettingsPlugin, AssetPlugin, SimpleApiPlugin
  - [not converted] TemplatePlugin: Bambuddy uses a standalone viewer page instead
  - [not converted] softwareupdate hook: OctoPrint-specific
  - Live nozzle animation during printing is not available: Bambu printers do not
    expose GCode serial logs (Send: ...). The 3D model and progress highlight work fine.

Access the viewer at: /api/v1/plugins/prettygcode/assets/index.html
"""

import logging

from backend.app.plugins.base import (
    BambuddyPlugin,
    AssetPlugin,
    SettingsPlugin,
    SimpleApiPlugin,
    StartupPlugin,
    Events,
)

__plugin_name__ = "PrettyGCode"
__plugin_version__ = "1.3.0"
__plugin_description__ = "3D GCode visualizer — converted from OctoPrint-PrettyGCode."
__plugin_author__ = "Kragrathea (maintained by soopahfly)"

logger = logging.getLogger(__name__)


class PrettyGCodePlugin(
    BambuddyPlugin,
    StartupPlugin,
    SettingsPlugin,
    AssetPlugin,
    SimpleApiPlugin,
):
    def get_settings_defaults(self):
        return {
            "dark_mode": False,
            "sync_to_progress": True,
            "show_nozzle": True,
            "fat_lines": False,
            "antialias": True,
            "orbit_when_idle": False,
        }

    def get_assets(self):
        return {
            "js": [
                "js/bambuddy_adapter.js",
                "js/prettygcode.js",
            ],
            "css": ["css/prettygcode.css"],
        }

    def get_api_commands(self):
        return {
            "update_settings": [],   # body contains any subset of settings keys
        }

    async def on_after_startup(self):
        logger.info("[PrettyGCode] Plugin loaded. Viewer: /api/v1/plugins/prettygcode/assets/index.html")

    async def on_api_get(self):
        """Return current plugin settings for the viewer page."""
        return self.get_settings_defaults()

    async def on_api_command(self, command: str, data: dict):
        if command == "update_settings":
            # Settings are persisted via the standard plugin settings endpoint.
            # This command is a no-op stub — the viewer uses PUT /plugins/prettygcode/settings directly.
            return {"status": "use PUT /api/v1/plugins/prettygcode/settings instead"}
        return {}


__plugin_implementation__ = PrettyGCodePlugin()
