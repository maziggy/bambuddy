"""Plugin management API routes.

Endpoints:
  GET    /api/v1/plugins                               list all known plugins
  POST   /api/v1/plugins/upload                        upload & analyse a plugin zip
  POST   /api/v1/plugins/install/{upload_id}           install a staged upload
  GET    /api/v1/plugins/{key}/assets/{path}           serve static asset from plugin dir
  PATCH  /api/v1/plugins/{key}/enable                  enable a plugin (restart required)
  PATCH  /api/v1/plugins/{key}/disable                 disable a plugin (restart required)
  GET    /api/v1/plugins/{key}/settings                get merged settings for a plugin
  PUT    /api/v1/plugins/{key}/settings                update settings for a plugin
  GET    /api/v1/plugins/{key}                         SimpleApiPlugin GET handler
  POST   /api/v1/plugins/{key}/command                 SimpleApiPlugin command handler
"""

import json
import logging
import mimetypes
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings as app_settings
from backend.app.core.database import async_session
from backend.app.models.plugin import PluginRecord
from backend.app.plugins.base import SimpleApiPlugin
from backend.app.plugins.octoprint_converter import ConversionResult, detect_and_convert
from backend.app.plugins.registry import plugin_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins", tags=["plugins"])

# ---------------------------------------------------------------------------
# Staged upload store  (upload_id → {result, temp_dir, expires_at})
# ---------------------------------------------------------------------------

_STAGE_TTL = 600  # seconds — 10 minutes
_staged: dict[str, dict] = {}


def _gc_staged() -> None:
    """Remove expired staged uploads and clean up their temp dirs."""
    now = time.time()
    expired = [uid for uid, entry in _staged.items() if entry["expires_at"] < now]
    for uid in expired:
        try:
            shutil.rmtree(_staged[uid]["temp_dir"], ignore_errors=True)
        except Exception:
            pass
        _staged.pop(uid, None)


async def get_db():
    async with async_session() as db:
        yield db


# ---------------------------------------------------------------------------
# Plugin management
# ---------------------------------------------------------------------------

@router.get("")
async def list_plugins(db: AsyncSession = Depends(get_db)):
    """Return all known plugins with metadata, enabled state, and load status."""
    result = await db.execute(select(PluginRecord))
    records = result.scalars().all()
    return [
        {
            "plugin_key": r.plugin_key,
            "name": r.name,
            "version": r.version,
            "description": r.description,
            "author": r.author,
            "enabled": r.enabled,
            "loaded": r.plugin_key in plugin_registry.plugins,
            "has_viewer": (app_settings.plugins_dir / r.plugin_key / "static" / "index.html").exists(),
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in records
    ]


# ---------------------------------------------------------------------------
# Upload & install
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_plugin(file: UploadFile = File(...)):
    """Upload a plugin zip, detect its type, and return a preview.

    Returns an upload_id valid for 10 minutes.  Call POST /install/{upload_id}
    to actually install it.
    """
    _gc_staged()

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are supported")

    temp_dir = Path(tempfile.mkdtemp(prefix="bambuddy_plugin_upload_"))
    try:
        zip_path = temp_dir / "upload.zip"
        content = await file.read()
        zip_path.write_bytes(content)

        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        zip_path.unlink()

        result: ConversionResult = detect_and_convert(extract_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid or corrupt zip file")
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception("Plugin upload failed")
        raise HTTPException(status_code=500, detail=str(exc))

    upload_id = str(uuid.uuid4())
    _staged[upload_id] = {
        "result": result,
        "temp_dir": str(temp_dir),
        "expires_at": time.time() + _STAGE_TTL,
    }

    return {
        "upload_id": upload_id,
        "plugin_type": result.plugin_type,
        "plugin_key": result.plugin_key,
        "name": result.name,
        "version": result.version,
        "description": result.description,
        "author": result.author,
        "supported_mixins": result.supported_mixins,
        "unsupported_mixins": result.unsupported_mixins,
        "conversion_notes": result.conversion_notes,
        "converted_code": result.converted_code,
        "already_installed": (app_settings.plugins_dir / result.plugin_key).exists(),
    }


@router.post("/install/{upload_id}")
async def install_plugin(upload_id: str, db: AsyncSession = Depends(get_db)):
    """Install a previously uploaded and staged plugin."""
    entry = _staged.get(upload_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Upload not found or expired")

    result: ConversionResult = entry["result"]
    temp_dir = Path(entry["temp_dir"])

    if result.plugin_type == "unknown":
        raise HTTPException(status_code=400, detail="Cannot install: unknown plugin type")

    dest = app_settings.plugins_dir / result.plugin_key
    try:
        if dest.exists():
            shutil.rmtree(dest)

        if result.plugin_type == "bambuddy":
            # Copy the detected plugin directory as-is
            shutil.copytree(result.plugin_dir, dest)

        elif result.plugin_type == "octoprint":
            dest.mkdir(parents=True)
            if result.converted_code:
                (dest / "__init__.py").write_text(result.converted_code, encoding="utf-8")
            # Also copy any non-Python assets (static files, etc.)
            if result.plugin_dir and result.plugin_dir.exists():
                for item in result.plugin_dir.iterdir():
                    if item.suffix == ".py":
                        continue
                    target = dest / item.name
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, target)

    except Exception as exc:
        logger.exception("Plugin install failed for '%s'", result.plugin_key)
        raise HTTPException(status_code=500, detail=f"Install failed: {exc}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        _staged.pop(upload_id, None)

    return {
        "status": "installed",
        "plugin_key": result.plugin_key,
        "name": result.name,
        "plugin_type": result.plugin_type,
        "restart_required": True,
    }


# ---------------------------------------------------------------------------
# Static asset serving
# ---------------------------------------------------------------------------

@router.get("/{plugin_key}/assets/{asset_path:path}")
async def serve_plugin_asset(plugin_key: str, asset_path: str):
    """Serve a static file from a plugin's static/ directory.

    Path traversal is prevented by resolving against the plugin's static dir
    and confirming the result is still inside it.
    """
    static_root = (app_settings.plugins_dir / plugin_key / "static").resolve()
    requested = (static_root / asset_path).resolve()

    # Guard against path traversal
    try:
        requested.relative_to(static_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_path}")

    mime_type, _ = mimetypes.guess_type(str(requested))
    return FileResponse(str(requested), media_type=mime_type or "application/octet-stream")


@router.patch("/{plugin_key}/enable")
async def enable_plugin(plugin_key: str, db: AsyncSession = Depends(get_db)):
    """Enable a plugin. Takes effect after the next restart."""
    record = await _get_record_or_404(db, plugin_key)
    record.enabled = True
    await db.commit()
    return {"status": "enabled", "restart_required": True}


@router.patch("/{plugin_key}/disable")
async def disable_plugin(plugin_key: str, db: AsyncSession = Depends(get_db)):
    """Disable a plugin. Takes effect after the next restart."""
    record = await _get_record_or_404(db, plugin_key)
    record.enabled = False
    await db.commit()
    return {"status": "disabled", "restart_required": True}


# ---------------------------------------------------------------------------
# Plugin settings
# ---------------------------------------------------------------------------

@router.get("/{plugin_key}/settings")
async def get_plugin_settings(plugin_key: str, db: AsyncSession = Depends(get_db)):
    """Return current settings for a plugin (defaults merged with stored values)."""
    if plugin_key not in plugin_registry.plugins:
        raise HTTPException(status_code=404, detail="Plugin not currently loaded")
    return await plugin_registry.get_plugin_settings(plugin_key, db)


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


@router.put("/{plugin_key}/settings")
async def update_plugin_settings(
    plugin_key: str,
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Persist settings for a plugin (merged into existing stored values)."""
    record = await _get_record_or_404(db, plugin_key)
    stored: dict[str, Any] = {}
    if record.settings:
        try:
            stored = json.loads(record.settings)
        except Exception:
            pass
    stored.update(body.settings)
    record.settings = json.dumps(stored)
    await db.commit()
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# SimpleApiPlugin endpoints
# ---------------------------------------------------------------------------

@router.get("/{plugin_key}")
async def plugin_get(plugin_key: str, db: AsyncSession = Depends(get_db)):
    """Call on_api_get() on a SimpleApiPlugin."""
    plugin = _get_loaded_plugin(plugin_key)
    if not isinstance(plugin, SimpleApiPlugin):
        raise HTTPException(status_code=400, detail="Plugin does not implement SimpleApiPlugin")
    try:
        return await plugin.on_api_get()
    except Exception as exc:
        logger.exception("Plugin '%s' on_api_get raised", plugin_key)
        raise HTTPException(status_code=500, detail=str(exc))


class CommandRequest(BaseModel):
    command: str
    data: dict[str, Any] = {}


@router.post("/{plugin_key}/command")
async def plugin_command(
    plugin_key: str,
    body: CommandRequest,
    db: AsyncSession = Depends(get_db),
):
    """Dispatch a command to a SimpleApiPlugin.

    Body::

        {"command": "my_command", "param1": "value1"}
    """
    plugin = _get_loaded_plugin(plugin_key)
    if not isinstance(plugin, SimpleApiPlugin):
        raise HTTPException(status_code=400, detail="Plugin does not implement SimpleApiPlugin")

    commands = plugin.get_api_commands()
    if body.command not in commands:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command '{body.command}'. Available: {list(commands.keys())}",
        )

    required = commands[body.command]
    missing = [p for p in required if p not in body.data]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"Missing required parameters: {missing}"
        )

    try:
        return await plugin.on_api_command(body.command, body.data)
    except Exception as exc:
        logger.exception("Plugin '%s' command '%s' raised", plugin_key, body.command)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_record_or_404(db: AsyncSession, plugin_key: str) -> PluginRecord:
    result = await db.execute(
        select(PluginRecord).where(PluginRecord.plugin_key == plugin_key)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_key}' not found")
    return record


def _get_loaded_plugin(plugin_key: str):
    plugin = plugin_registry.plugins.get(plugin_key)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_key}' is not loaded")
    return plugin
