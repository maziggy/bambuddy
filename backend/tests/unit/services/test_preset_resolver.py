"""Tests for the source-aware preset resolver used by the slice route."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.schemas.slicer import PresetRef
from backend.app.services import preset_resolver

# --- standard tier --------------------------------------------------------


def test_standard_emits_inherits_stub():
    """Standard tier returns a JSON stub the sidecar's resolver can flatten
    against `BUNDLED_PROFILES_PATH/<category>/<name>.json`. No content
    round-trip needed — the sidecar reads the bundled JSON itself."""
    out = preset_resolver._resolve_standard(
        PresetRef(source="standard", id="Bambu Lab X1 Carbon 0.4 nozzle"),
        slot="printer",
    )
    payload = json.loads(out)
    assert payload == {
        "name": "Bambu Lab X1 Carbon 0.4 nozzle",
        "inherits": "Bambu Lab X1 Carbon 0.4 nozzle",
        # `from: "system"` so the sidecar's compatibility check doesn't
        # treat this as a User-authored profile and reject it against
        # system filament/process pairs.
        "from": "system",
        # `type` is required by the CLI's --load-settings parser. Without
        # it the CLI silently exits with rc=-5 ("input preset file is
        # invalid"), causing every 3MF slice to fall back to embedded
        # settings. See preset_resolver._SLOT_TO_PROFILE_TYPE.
        "type": "machine",
    }


def test_standard_emits_correct_type_per_slot():
    """Each slot maps to the right `type` value the CLI parser expects:
    printer → machine, process → process, filament → filament. Missing or
    wrong type causes the CLI to silently exit with rc=-5."""
    for slot, expected_type in (("printer", "machine"), ("process", "process"), ("filament", "filament")):
        out = preset_resolver._resolve_standard(
            PresetRef(source="standard", id="anything"),
            slot=slot,
        )
        assert json.loads(out)["type"] == expected_type, slot


def test_standard_rejects_unknown_slot():
    with pytest.raises(HTTPException) as exc:
        preset_resolver._resolve_standard(PresetRef(source="standard", id="anything"), slot="bogus")
    assert exc.value.status_code == 400


# --- local tier -----------------------------------------------------------


@pytest.mark.asyncio
async def test_local_returns_setting_blob():
    db = MagicMock()
    preset = MagicMock()
    preset.preset_type = "filament"
    preset.setting = '{"name": "PLA Basic"}'
    db.get = AsyncMock(return_value=preset)

    out = await preset_resolver._resolve_local(db, PresetRef(source="local", id="42"), slot="filament")
    assert out == '{"name": "PLA Basic"}'
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_rejects_non_integer_id():
    db = MagicMock()
    db.get = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await preset_resolver._resolve_local(db, PresetRef(source="local", id="not-a-number"), slot="filament")
    assert exc.value.status_code == 400
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_rejects_wrong_preset_type():
    """A `local` ref pointing at a process preset for the filament slot
    must fail — same guard the legacy slice path had."""
    db = MagicMock()
    preset = MagicMock()
    preset.preset_type = "process"
    db.get = AsyncMock(return_value=preset)
    with pytest.raises(HTTPException) as exc:
        await preset_resolver._resolve_local(db, PresetRef(source="local", id="1"), slot="filament")
    assert exc.value.status_code == 400
    assert "preset_type='filament'" in exc.value.detail


# --- cloud tier -----------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_blocks_user_without_cloud_auth():
    """Defence-in-depth: a user holding LIBRARY_UPLOAD but not CLOUD_AUTH
    cannot slice with cloud presets even if their User row carries a
    leftover cloud_token from a previous permission state."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=False)
    with pytest.raises(HTTPException) as exc:
        await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="PFU123"), slot="printer")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cloud_400_when_no_token_stored():
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=(None, None, None)),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="PFU123"), slot="printer")
    assert exc.value.status_code == 400
    assert "Sign in" in exc.value.detail


@pytest.mark.asyncio
async def test_cloud_unwraps_setting_envelope():
    """Bambu Cloud's `get_setting_detail` returns the preset wrapped under
    `.setting`; the sidecar wants the inner content, not the envelope."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(
        return_value={
            "setting_id": "PFU123",
            "name": "X1C Custom",
            "setting": {"name": "X1C Custom", "nozzle_diameter": [0.4]},
        }
    )
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", "e@x", "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
    ):
        out = await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="PFU123"), slot="printer")
    payload = json.loads(out)
    assert payload == {"name": "X1C Custom", "nozzle_diameter": [0.4]}
    cloud_mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_falls_back_to_top_level_when_no_envelope():
    """If a cloud response doesn't nest under `.setting` (rare but seen on
    some endpoints), forward the whole payload rather than failing — the
    sidecar will reject malformed content cleanly."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(return_value={"name": "X1C Custom", "nozzle_diameter": [0.4]})
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", None, "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
    ):
        out = await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="PFU123"), slot="printer")
    payload = json.loads(out)
    assert "name" in payload


@pytest.mark.asyncio
async def test_cloud_auth_error_returns_401():
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(side_effect=preset_resolver.BambuCloudAuthError("expired"))
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", None, "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
        pytest.raises(HTTPException) as exc,
    ):
        await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="PFU123"), slot="printer")
    assert exc.value.status_code == 401


# --- top-level dispatcher -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_preset_ref_dispatches_by_source():
    """The public entrypoint just routes to the right tier-specific
    helper. Verify each branch is selected correctly."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    preset = MagicMock()
    preset.preset_type = "printer"
    preset.setting = '{"local": true}'
    db.get = AsyncMock(return_value=preset)

    # local
    out = await preset_resolver.resolve_preset_ref(db, user, PresetRef(source="local", id="1"), slot="printer")
    assert out == '{"local": true}'

    # standard
    out = await preset_resolver.resolve_preset_ref(
        db, user, PresetRef(source="standard", id="Some Bundled Name"), slot="printer"
    )
    assert json.loads(out)["inherits"] == "Some Bundled Name"
