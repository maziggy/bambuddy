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
    # Resolver rewrites the `type` field to the CLI-expected value AND pins
    # `from: "system"` (#1712 follow-up: Bambu Cloud labels printers as
    # "printer" and filaments routinely ship with empty `from`; the CLI
    # rejects either with the same -5 "input preset invalid" surface).
    assert payload == {
        "name": "X1C Custom",
        "nozzle_diameter": [0.4],
        "type": "machine",
        "from": "system",
    }
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


@pytest.mark.parametrize(
    "slot, source_type, expected_type",
    [
        # Bambu Cloud's wire shape: `printer` / `print` / `filament`. The CLI
        # only accepts `machine` / `process` / `filament`. Without rewrite
        # the CLI exits -5 with `operator(): unknown config type` and the
        # sidecar surfaces "The input preset file is invalid and can not be
        # parsed" (#1712 follow-up, reported by maziggy on Mecha Mewtwo).
        ("printer", "printer", "machine"),
        ("process", "print", "process"),
        ("filament", "filament", "filament"),
        # Cloud-side already CLI-shaped: still gets overwritten to the
        # canonical value — idempotent, no harm.
        ("printer", "machine", "machine"),
        ("process", "process", "process"),
        # Missing type field on the source payload: synthesise it.
        ("printer", None, "machine"),
        ("process", None, "process"),
    ],
)
@pytest.mark.asyncio
async def test_cloud_rewrites_type_field_for_cli(slot, source_type, expected_type):
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    setting: dict = {"name": "P"}
    if source_type is not None:
        setting["type"] = source_type
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(return_value={"setting": setting})
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", None, "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
    ):
        out = await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="X"), slot=slot)
    assert json.loads(out)["type"] == expected_type


@pytest.mark.parametrize(
    "source_from",
    [
        # The actual failing case (#1712 follow-up): Bambu Cloud's filament
        # detail endpoint routinely returns presets with no `from` field or
        # `from: ""`. The CLI rejects either with
        # `operator(): ... from  unsupported` (note the double space — that's
        # the literal stderr from the sidecar log on the Mecha Mewtwo slice).
        "",
        # Cloud-side already CLI-friendly: still gets pinned to "system" —
        # idempotent, no harm, matches the standard-tier convention.
        "system",
        # GUI-exported values that the sidecar's normalizeFromField also
        # maps to "system" for the same reason — we beat it to the punch.
        "User",
        "System",
    ],
)
@pytest.mark.asyncio
async def test_cloud_pins_from_field_to_system(source_from):
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    setting: dict = {"name": "F", "type": "filament", "from": source_from}
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(return_value={"setting": setting})
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", None, "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
    ):
        out = await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="X"), slot="filament")
    assert json.loads(out)["from"] == "system"


@pytest.mark.asyncio
async def test_cloud_synthesises_from_field_when_missing():
    """The original failing payload had no `from` field at all (sidecar
    error: `from  unsupported` — double space = empty value). The resolver
    must still emit a usable `from` instead of forwarding the gap."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    setting = {"name": "F", "type": "filament"}  # NB: no `from`
    cloud_mock = MagicMock()
    cloud_mock.set_token = MagicMock()
    cloud_mock.get_setting_detail = AsyncMock(return_value={"setting": setting})
    cloud_mock.close = AsyncMock()
    with (
        patch.object(
            preset_resolver,
            "get_stored_token",
            AsyncMock(return_value=("tok", None, "global")),
        ),
        patch.object(preset_resolver, "BambuCloudService", return_value=cloud_mock),
    ):
        out = await preset_resolver._resolve_cloud(db, user, PresetRef(source="cloud", id="X"), slot="filament")
    assert json.loads(out)["from"] == "system"


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


# --- orca_cloud tier -------------------------------------------------------


@pytest.mark.asyncio
async def test_orca_cloud_blocks_user_without_orca_cloud_auth():
    """Defence-in-depth, same shape as the Bambu Cloud permission check:
    a user holding LIBRARY_UPLOAD but not ORCA_CLOUD_AUTH can't slice with
    Orca Cloud presets even if their User row still carries a token."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=False)
    with pytest.raises(HTTPException) as exc:
        await preset_resolver._resolve_orca_cloud(db, user, PresetRef(source="orca_cloud", id="abc"), slot="printer")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_orca_cloud_unwraps_content():
    """Orca's profile shape is ``{id, name, content, updated_time, created_time}``
    — the inner ``content`` is the actual slicer-format JSON. We forward
    that, not the wrapper."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    svc_mock = MagicMock()
    svc_mock.get_profile = AsyncMock(
        return_value={
            "id": "abc",
            "name": "X1C Custom",
            "content": {"name": "X1C Custom", "nozzle_diameter": [0.4]},
        }
    )
    svc_mock.close = AsyncMock()
    with patch.object(preset_resolver, "_build_orca_service", AsyncMock(return_value=svc_mock)):
        out = await preset_resolver._resolve_orca_cloud(
            db, user, PresetRef(source="orca_cloud", id="abc"), slot="printer"
        )
    payload = json.loads(out)
    # Resolver rewrites `type` to the CLI-expected value AND pins
    # `from: "system"` (#1712 follow-up). Orca natively uses "machine" but
    # Bambu-sourced syncs can carry "printer" and either empty/missing `from`.
    assert payload == {
        "name": "X1C Custom",
        "nozzle_diameter": [0.4],
        "type": "machine",
        "from": "system",
    }
    svc_mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_orca_cloud_auth_error_returns_401():
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    svc_mock = MagicMock()
    svc_mock.get_profile = AsyncMock(side_effect=preset_resolver.OrcaCloudAuthError("expired"))
    svc_mock.close = AsyncMock()
    with (
        patch.object(preset_resolver, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        pytest.raises(HTTPException) as exc,
    ):
        await preset_resolver._resolve_orca_cloud(db, user, PresetRef(source="orca_cloud", id="abc"), slot="printer")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_orca_cloud_not_found_returns_400():
    """``get_profile`` raises generic ``OrcaCloudError`` for "not found" —
    the resolver maps that to a 400 (not 502) so the UI can show "profile
    no longer exists" rather than "service down"."""
    db = MagicMock()
    user = MagicMock()
    user.has_permission = MagicMock(return_value=True)
    svc_mock = MagicMock()
    svc_mock.get_profile = AsyncMock(side_effect=preset_resolver.OrcaCloudError("profile 'abc' not found (scanned 0)"))
    svc_mock.close = AsyncMock()
    with (
        patch.object(preset_resolver, "_build_orca_service", AsyncMock(return_value=svc_mock)),
        pytest.raises(HTTPException) as exc,
    ):
        await preset_resolver._resolve_orca_cloud(db, user, PresetRef(source="orca_cloud", id="abc"), slot="printer")
    assert exc.value.status_code == 400


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
