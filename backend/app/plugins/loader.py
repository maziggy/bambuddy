"""Plugin loader — discovers and imports plugins from the plugins/ directory.

Each plugin is either:
  - A package:      plugins/<key>/__init__.py
  - A single file:  plugins/<key>.py

The module must expose a ``__plugin_implementation__`` attribute that is an
instance of a :class:`BambuddyPlugin` subclass.  Optional metadata attributes:

    __plugin_name__        = "Human Readable Name"
    __plugin_version__     = "1.0.0"
    __plugin_description__ = "What this plugin does."
    __plugin_author__      = "Author Name"
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.plugin import PluginRecord
from backend.app.plugins.base import BambuddyPlugin
from backend.app.plugins.registry import plugin_registry

logger = logging.getLogger(__name__)


async def discover_and_load_plugins(plugins_dir: Path, db: AsyncSession) -> None:
    """Scan *plugins_dir* and load all enabled plugins into the registry."""
    if not plugins_dir.exists():
        plugins_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created plugins directory: %s", plugins_dir)
        return

    entries = sorted(plugins_dir.iterdir())
    for entry in entries:
        if entry.is_dir():
            if entry.name.startswith(("_", ".")):
                continue
            plugin_key = entry.name
            plugin_path = entry
        elif entry.suffix == ".py" and not entry.stem.startswith("_"):
            plugin_key = entry.stem
            plugin_path = entry
        else:
            continue

        try:
            await _load_single_plugin(plugin_path, plugin_key, db)
        except Exception:
            logger.exception("Failed to load plugin '%s'", plugin_key)

    loaded = list(plugin_registry.plugins.keys())
    logger.info("Plugin loading complete. Loaded: %s", loaded if loaded else "none")


async def _load_single_plugin(path: Path, plugin_key: str, db: AsyncSession) -> None:
    """Import, validate, DB-register, and add a single plugin to the registry."""
    module = _import_plugin_module(path, plugin_key)

    impl = getattr(module, "__plugin_implementation__", None)
    if impl is None:
        logger.warning("Plugin '%s' has no __plugin_implementation__, skipping", plugin_key)
        return
    if not isinstance(impl, BambuddyPlugin):
        logger.warning(
            "Plugin '%s' __plugin_implementation__ is not a BambuddyPlugin instance, skipping",
            plugin_key,
        )
        return

    metadata = _read_metadata(module, impl)
    record = await _ensure_db_record(db, plugin_key, metadata)

    if not record.enabled:
        logger.info("Plugin '%s' is disabled in the database, skipping", plugin_key)
        return

    impl._plugin_key = plugin_key
    impl._plugin_registry = plugin_registry
    impl.initialize()

    plugin_registry.register(impl)
    logger.info(
        "Loaded plugin '%s' v%s by %s",
        metadata["name"],
        metadata["version"],
        metadata["author"] or "unknown",
    )


def _import_plugin_module(path: Path, plugin_key: str):
    """Return the imported module for a plugin, caching in sys.modules."""
    module_name = f"bambuddy_plugin_{plugin_key}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    if path.is_dir():
        init = path / "__init__.py"
        if not init.exists():
            raise ImportError(f"Plugin directory '{path}' has no __init__.py")
        spec = importlib.util.spec_from_file_location(
            module_name,
            init,
            submodule_search_locations=[str(path)],
        )
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for plugin '{plugin_key}'")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _read_metadata(module, impl: BambuddyPlugin) -> dict[str, Any]:
    return {
        "name": getattr(module, "__plugin_name__", impl.get_plugin_name()),
        "version": getattr(module, "__plugin_version__", impl.get_plugin_version()),
        "description": getattr(module, "__plugin_description__", impl.get_plugin_description()),
        "author": getattr(module, "__plugin_author__", impl.get_plugin_author()),
    }


async def _ensure_db_record(
    db: AsyncSession, plugin_key: str, metadata: dict[str, Any]
) -> PluginRecord:
    """Return the existing DB record for a plugin, or create a new enabled one."""
    result = await db.execute(
        select(PluginRecord).where(PluginRecord.plugin_key == plugin_key)
    )
    record = result.scalar_one_or_none()

    if record is None:
        record = PluginRecord(
            plugin_key=plugin_key,
            name=metadata["name"],
            version=metadata["version"],
            description=metadata["description"],
            author=metadata["author"],
            enabled=True,
            settings="{}",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        logger.info("Registered new plugin in DB: '%s'", plugin_key)
    else:
        # Keep name/version/description in sync with the installed code
        record.name = metadata["name"]
        record.version = metadata["version"]
        record.description = metadata["description"]
        record.author = metadata["author"]
        await db.commit()

    return record
