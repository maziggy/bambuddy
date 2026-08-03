"""The chamber-temperature ceiling is shared by every surface that accepts one.

Reported on Discord: the preheat & heat-soak inputs capped at 60 °C, which put
the top of the H2 series' range (65 °C) out of reach. The ceiling now lives in
one place — ``MAX_CHAMBER_TEMP_C`` — and these tests pin both its value and the
fact that each schema actually derives its bound from it rather than carrying a
private literal that could drift back to 60.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.print_queue import (
    PrintQueueBulkUpdate,
    PrintQueueItemCreate,
    PrintQueueItemUpdate,
)
from backend.app.schemas.settings import AppSettingsUpdate
from backend.app.utils.printer_models import MAX_CHAMBER_TEMP_C

# The H2 series (H2C / H2D / H2D Pro / H2S) and X2D heat the chamber to 65 °C.
# X1E stops at 60 and clamps in firmware. Hard-coded here on purpose: if the
# constant moves, that should be a deliberate edit, not a silent one.
EXPECTED_CEILING = 65

# (schema, kwargs the schema requires beyond the field under test)
OVERRIDE_SCHEMAS = [
    (PrintQueueItemCreate, {}),
    (PrintQueueItemUpdate, {}),
    (PrintQueueBulkUpdate, {"item_ids": [1]}),
]


def test_ceiling_is_65():
    assert MAX_CHAMBER_TEMP_C == EXPECTED_CEILING


@pytest.mark.parametrize("schema,required", OVERRIDE_SCHEMAS)
def test_override_accepts_the_ceiling(schema, required):
    model = schema(preheat_chamber_target_override=MAX_CHAMBER_TEMP_C, **required)
    assert model.preheat_chamber_target_override == MAX_CHAMBER_TEMP_C


@pytest.mark.parametrize("schema,required", OVERRIDE_SCHEMAS)
def test_override_rejects_above_the_ceiling(schema, required):
    with pytest.raises(ValidationError):
        schema(preheat_chamber_target_override=MAX_CHAMBER_TEMP_C + 1, **required)


@pytest.mark.parametrize("schema,required", OVERRIDE_SCHEMAS)
def test_override_still_accepts_zero(schema, required):
    """0 is "no chamber phase, even if the filament map wants one" — raising
    the ceiling must not disturb the low end."""
    model = schema(preheat_chamber_target_override=0, **required)
    assert model.preheat_chamber_target_override == 0


def test_chamber_presets_accept_the_ceiling():
    payload = f"[35, 45, {MAX_CHAMBER_TEMP_C}]"
    assert AppSettingsUpdate(chamber_temp_presets=payload).chamber_temp_presets == payload


def test_chamber_presets_reject_above_the_ceiling():
    with pytest.raises(ValidationError) as exc:
        AppSettingsUpdate(chamber_temp_presets=f"[35, 45, {MAX_CHAMBER_TEMP_C + 1}]")
    assert f"[0, {MAX_CHAMBER_TEMP_C}]" in str(exc.value)
