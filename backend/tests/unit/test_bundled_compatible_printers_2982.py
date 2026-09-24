"""Standard-tier presets carry the slicer's own ``compatible_printers`` (#2982).

The sidecar's ``/profiles/bundled`` listing used to report only a name and a
``base_id``, which left the SliceModal inferring a preset's printer from its
NAME. That inference cannot work for several Bambu printers, because the bundle
ships no preset named after them: all ten of a P1S's process presets are named
``@BBL X1C`` and name the P1S only in ``compatible_printers``. Reading the name
classified every one of them as belonging to an X1 Carbon, so a P1S had zero
compatible processes, the dropdown hid all 198, and the auto-pick fell through
to an alphabetically-first ``0.06mm Fine @BBL A1 0.2 nozzle`` that the CLI then
refused.

These pin the pass-through, including the graceful degrade for a sidecar too
old to report the field.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import slicer_presets as sp


def _sidecar(payload: dict) -> MagicMock:
    svc = MagicMock()
    svc.list_bundled_profiles = AsyncMock(return_value=payload)
    svc.__aenter__ = AsyncMock(return_value=svc)
    svc.__aexit__ = AsyncMock(return_value=False)
    return svc


async def _fetch(payload: dict) -> dict:
    sp._bundled_cache = None
    svc = _sidecar(payload)
    with (
        patch.object(sp, "_resolve_slicer_api_url", AsyncMock(return_value="http://ok")),
        patch.object(sp, "SlicerApiService", return_value=svc),
    ):
        return await sp._fetch_bundled_presets(MagicMock())


P1S = "Bambu Lab P1S 0.4 nozzle"

# The real shape of the shipped bundle: a process preset named for one printer
# that names several others, the P1S among them.
X1C_PROCESS = {
    "name": "0.20mm Standard @BBL X1C",
    "base_id": "fdm_process_single_0.20",
    "compatible_printers": [
        "Bambu Lab X1 Carbon 0.4 nozzle",
        "Bambu Lab X1 0.4 nozzle",
        P1S,
        "Bambu Lab X1E 0.4 nozzle",
    ],
}
A1_FILAMENT = {
    "name": "Bambu ABS @BBL A1",
    "base_id": "Bambu ABS @base",
    "compatible_printers": ["Bambu Lab A1 0.4 nozzle", "Bambu Lab A1 0.6 nozzle"],
    "filament_type": "ABS",
    "filament_colour": None,
}


def _payload(**slots) -> dict:
    base: dict = {"printer": [], "process": [], "filament": []}
    base.update(slots)
    return base


class TestTheProcessSlot:
    @pytest.mark.asyncio
    async def test_carries_the_declared_printer_list(self):
        slots = await _fetch(_payload(process=[X1C_PROCESS]))
        assert slots["process"][0].compatible_printers == X1C_PROCESS["compatible_printers"]

    @pytest.mark.asyncio
    async def test_keeps_a_printer_no_preset_is_named_after(self):
        slots = await _fetch(_payload(process=[X1C_PROCESS]))
        assert P1S in (slots["process"][0].compatible_printers or [])

    @pytest.mark.asyncio
    async def test_an_older_sidecar_leaves_the_field_unset(self):
        """No field is not an empty list: unset means "said nothing", which
        keeps the name matcher in play, while an empty list would read as
        "compatible with no printer at all" and hide the preset everywhere."""
        slots = await _fetch(
            _payload(process=[{"name": "0.20mm Standard @BBL X1C", "base_id": None}]),
        )
        assert slots["process"][0].compatible_printers is None

    @pytest.mark.asyncio
    async def test_normalises_a_bare_string(self):
        slots = await _fetch(
            _payload(process=[{"name": "Solo", "base_id": None, "compatible_printers": P1S}]),
        )
        assert slots["process"][0].compatible_printers == [P1S]

    @pytest.mark.asyncio
    async def test_an_empty_list_reads_as_no_data(self):
        slots = await _fetch(
            _payload(process=[{"name": "Solo", "base_id": None, "compatible_printers": []}]),
        )
        assert slots["process"][0].compatible_printers is None

    @pytest.mark.asyncio
    async def test_a_malformed_value_reads_as_no_data(self):
        slots = await _fetch(
            _payload(process=[{"name": "Solo", "base_id": None, "compatible_printers": 7}]),
        )
        assert slots["process"][0].compatible_printers is None

    @pytest.mark.asyncio
    async def test_drops_non_string_entries_but_keeps_the_rest(self):
        slots = await _fetch(
            _payload(
                process=[
                    {"name": "Solo", "base_id": None, "compatible_printers": [P1S, None, 3, "  "]},
                ],
            ),
        )
        assert slots["process"][0].compatible_printers == [P1S]


class TestTheFilamentSlot:
    @pytest.mark.asyncio
    async def test_carries_both_the_printer_list_and_the_material(self):
        slots = await _fetch(_payload(filament=[A1_FILAMENT]))
        preset = slots["filament"][0]
        assert preset.compatible_printers == A1_FILAMENT["compatible_printers"]
        assert preset.filament_type == "ABS"

    @pytest.mark.asyncio
    async def test_a_colourless_bundled_profile_stays_colourless(self):
        """True of the whole BBL tree at every inheritance depth — colour is a
        spool attribute, not a profile one — so this must not be invented."""
        slots = await _fetch(_payload(filament=[A1_FILAMENT]))
        assert slots["filament"][0].filament_colour is None

    @pytest.mark.asyncio
    async def test_an_unresolvable_material_stays_none(self):
        """32 shipped filament profiles inherit from a parent the bundle does
        not contain, so the sidecar reports no material for them. They must
        still be listed — the picker treats "unknown" as eligible."""
        slots = await _fetch(
            _payload(
                filament=[
                    {
                        "name": "PolyLite PLA @BBL H2S",
                        "base_id": "PolyLite PLA @base",
                        "filament_type": None,
                        "compatible_printers": ["Bambu Lab H2S 0.4 nozzle"],
                    },
                ],
            ),
        )
        assert len(slots["filament"]) == 1
        assert slots["filament"][0].filament_type is None
        assert slots["filament"][0].compatible_printers == ["Bambu Lab H2S 0.4 nozzle"]


class TestThePrinterSlot:
    @pytest.mark.asyncio
    async def test_printer_presets_carry_no_compatibility_of_their_own(self):
        """A printer is what compatibility is measured against; a list on one
        would be meaningless, and the SliceModal never filters that dropdown."""
        slots = await _fetch(
            _payload(
                printer=[
                    {"name": P1S, "base_id": None, "compatible_printers": ["nonsense"]},
                ],
            ),
        )
        assert slots["printer"][0].compatible_printers is None
        assert slots["printer"][0].name == P1S
