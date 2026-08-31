"""Chamber preheat must read the trays the print loads, not the whole AMS (#2886).

A P2S with PLA in slot 1 and ASA in slot 2 preheated every PLA job to a 45°C
chamber target, because ``_derive_chamber_target`` took the max over every
loaded tray regardless of which ones the job mapped. The reporter's log shows
the cost: bed driven to 90°C and the full 900s max-wait plus 300s soak burned
before each upload, on a printer whose chamber tops out around 33°C and so
never satisfies the wait early.

The reporter's AMS, from ``push-status/printer-1.json`` in their support
bundle, is reproduced in ``_reporter_ams`` below, and the mapping the dispatch
actually sent — ``[-1, -1, -1, 1]`` — in ``PLA_ONLY_MAPPING``.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.print_scheduler import PrintScheduler

# Global tray ids for AMS unit 0: ams_id * 4 + tray_id.
PETG_PRO_TRAY = 0
PLA_TRAY = 1
ASA_TRAY = 2
PETG_TRAY = 3

# What the dispatcher put on the wire for the reporter's PLA job.
PLA_ONLY_MAPPING = json.dumps([-1, -1, -1, PLA_TRAY])


@pytest.fixture
def scheduler():
    return PrintScheduler()


def _make_item(ams_mapping=None, **overrides):
    """A queue item shaped the way `_preheat_and_soak` reads it.

    `ams_mapping` is passed through verbatim so a test can hand in the JSON
    string the column stores, a raw list, or junk.
    """
    fields = {
        "id": 96,
        "preheat_override": "inherit",
        "preheat_chamber_target_override": None,
        "ams_mapping": ams_mapping,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _make_client():
    client = MagicMock()
    client.set_bed_temperature = MagicMock(return_value=True)
    client.set_chamber_temperature = MagicMock(return_value=True)
    client.set_airduct_mode = MagicMock(return_value=True)
    return client


def _reporter_ams():
    """The four trays in the reporter's AMS unit 0, ids and all.

    Ids are strings because that is how their firmware reports them; the
    derivation has to coerce before it can compare against a mapping's ints.
    """
    return [
        {
            "id": "0",
            "tray": [
                {"id": "0", "tray_type": "PETG Pro"},
                {"id": "1", "tray_type": "PLA"},
                {"id": "2", "tray_type": "ASA"},
                {"id": "3", "tray_type": "PETG"},
            ],
        }
    ]


def _make_state(ams=None, vt_tray=None, bed_temp=0.0, chamber_temp=0.0):
    raw_data: dict = {}
    if ams is not None:
        raw_data["ams"] = ams
    if vt_tray is not None:
        raw_data["vt_tray"] = vt_tray
    return SimpleNamespace(
        temperatures={"bed": bed_temp, "chamber": chamber_temp},
        raw_data=raw_data,
        airduct_mode=0,
    )


def _ints(**values):
    return AsyncMock(side_effect=lambda _db, key, default: values.get(key, default))


def _derive(scheduler, state, item, targets=None):
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = state
        return scheduler._derive_chamber_target(
            SimpleNamespace(id=1, model="P2S"),
            targets if targets is not None else PrintScheduler._bundled_preheat_targets(),
            item,
        )


# ----------------------------------------------------------------------------
# The reported case
# ----------------------------------------------------------------------------


def test_the_reporters_pla_job_derives_no_chamber_target(scheduler):
    """PLA mapped, ASA merely parked two slots over → 0, not 45."""
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(PLA_ONLY_MAPPING))
    assert result == 0


def test_the_same_ams_still_derives_45_for_a_job_that_maps_the_asa(scheduler):
    """The narrowing must not disarm preheat — mapping the ASA tray still asks
    for its 45°C."""
    mapping = json.dumps([-1, -1, -1, ASA_TRAY])
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(mapping))
    assert result == 45


def test_a_multi_material_job_takes_the_max_of_the_trays_it_maps(scheduler):
    """PLA + ASA in one print: ASA is the binding constraint, exactly as the
    all-trays scan used to conclude for every job."""
    mapping = json.dumps([PLA_TRAY, ASA_TRAY])
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(mapping))
    assert result == 45


def test_a_mapped_petg_pro_job_ignores_the_asa(scheduler):
    """PETG normalises to PETG (0), not PETG-CF (40) — and the ASA next to it
    contributes nothing."""
    mapping = json.dumps([PETG_PRO_TRAY, PETG_TRAY])
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(mapping))
    assert result == 0


@pytest.mark.asyncio
async def test_end_to_end_the_pla_job_skips_preheat_entirely(scheduler):
    """The whole stage short-circuits: no bed command, no chamber command, no
    900s wait. Their archive carries no bed_temperature (the log line reads
    "archive has no bed_temperature metadata"), so with the chamber target back
    at 0 this lands on the pre-existing skip branch.

    The wait and soak are pinned to 0 and `asyncio.sleep` is patched even
    though a passing run reaches neither: without that, a regression here does
    not fail, it blocks for the full 900s wall-clock deadline.
    """
    db = AsyncMock()
    client = _make_client()
    archive = SimpleNamespace(bed_temperature=None)

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(queue_keep_warm_bed_temp=90, preheat_soak_seconds=0, preheat_max_wait_seconds=0),
        ),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        pm.get_status.return_value = _make_state(ams=_reporter_ams())
        proceeded = await scheduler._preheat_and_soak(
            db, _make_item(PLA_ONLY_MAPPING), SimpleNamespace(id=1, model="P2S"), archive
        )

    assert proceeded is True
    client.set_bed_temperature.assert_not_called()
    client.set_chamber_temperature.assert_not_called()


@pytest.mark.asyncio
async def test_end_to_end_a_mapped_asa_job_still_heats_the_bed_to_drive_the_chamber(scheduler):
    """The other half of the reported behaviour is correct and must survive:
    an ASA job with no bed metadata still falls back to the configured
    chamber-heating bed temperature."""
    db = AsyncMock()
    client = _make_client()
    archive = SimpleNamespace(bed_temperature=None)
    mapping = json.dumps([-1, -1, -1, ASA_TRAY])

    with (
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=True)),
        patch.object(
            scheduler,
            "_get_int_setting",
            _ints(queue_keep_warm_bed_temp=90, preheat_soak_seconds=0, preheat_max_wait_seconds=0),
        ),
        patch.object(scheduler, "_get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.asyncio.sleep", AsyncMock()),
    ):
        pm.get_client.return_value = client
        # Already at temperature so the convergence loop exits on its first pass.
        pm.get_status.return_value = _make_state(ams=_reporter_ams(), bed_temp=90.0, chamber_temp=46.0)
        await scheduler._preheat_and_soak(db, _make_item(mapping), SimpleNamespace(id=1, model="P2S"), archive)

    client.set_bed_temperature.assert_called_once_with(90)


# ----------------------------------------------------------------------------
# Fallback: an item with no usable mapping keeps the all-trays scan
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param(None, id="never-set"),
        pytest.param("", id="empty-string"),
        pytest.param("[-1, -1]", id="all-unresolved"),
        pytest.param("[null, null]", id="all-null"),
        pytest.param("[]", id="empty-list"),
        pytest.param("not json", id="unparseable"),
        pytest.param('{"tray": 1}', id="not-a-list"),
    ],
)
def test_an_item_without_a_usable_mapping_scans_every_tray(scheduler, mapping):
    """No statement about which trays are used means we cannot narrow. Falling
    back to the whole unit keeps preheat firing for prints that need it; the
    alternative — narrowing to nothing — would silently disable the feature."""
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(mapping))
    assert result == 45


def test_an_item_object_without_the_attribute_at_all_scans_every_tray(scheduler):
    """`_apply_keep_warm` reaches for `next_item.ams_mapping` on rows loaded by
    other code paths; a missing attribute must fall back, not raise."""
    item = SimpleNamespace(id=7)
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), item)
    assert result == 45


def test_passing_no_item_at_all_scans_every_tray(scheduler):
    """The parameter is optional so existing callers keep compiling; omitting
    it is the pre-#2886 behaviour."""
    with patch("backend.app.services.print_scheduler.printer_manager") as pm:
        pm.get_status.return_value = _make_state(ams=_reporter_ams())
        result = scheduler._derive_chamber_target(
            SimpleNamespace(id=1, model="P2S"), PrintScheduler._bundled_preheat_targets()
        )
    assert result == 45


def test_a_partially_resolved_mapping_narrows_to_the_slots_that_resolved(scheduler):
    """`[-1, 2]` is NOT all-unresolved: slot 2 matched the ASA tray, so it is a
    genuine statement and the ASA counts."""
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item(json.dumps([-1, ASA_TRAY])))
    assert result == 45


def test_a_mapping_already_stored_as_a_list_is_read_without_json(scheduler):
    """The column is Text, but rows written in-process can still hold a list."""
    result = _derive(scheduler, _make_state(ams=_reporter_ams()), _make_item([PLA_TRAY]))
    assert result == 0


# ----------------------------------------------------------------------------
# Tray addressing
# ----------------------------------------------------------------------------


def test_a_second_ams_unit_is_addressed_with_the_four_slot_stride(scheduler):
    """Unit 1 tray 2 is global id 6, not 2 — getting the stride wrong would
    match the ASA in unit 0 instead."""
    ams = _reporter_ams() + [{"id": 1, "tray": [{"id": 2, "tray_type": "ABS"}]}]
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([6]))) == 45
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([2]))) == 45  # unit 0's ASA
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([PLA_TRAY]))) == 0


def test_an_ams_ht_is_addressed_by_its_unit_id(scheduler):
    """AMS-HT units number from 128 and hold one tray, so the global id is the
    unit id itself — 128 * 4 + 0 would address nothing."""
    ams = [{"id": 128, "tray": [{"id": 0, "tray_type": "ABS"}]}]
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([128]))) == 45
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([0]))) == 0


def test_unparseable_tray_ids_do_not_take_the_derivation_down(scheduler):
    """A junk id falls to 0, which addresses unit 0 slot 0. It must not raise —
    preheat is best-effort and an exception here aborts the dispatch stage."""
    ams = [{"id": None, "tray": [{"id": "x", "tray_type": "ASA"}]}]
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([0]))) == 45


def test_a_tray_with_no_type_contributes_nothing(scheduler):
    """An empty slot the mapping happens to name is not an error, just a 0."""
    ams = [{"id": 0, "tray": [{"id": 0, "tray_type": ""}, {"id": 1, "tray_type": "ASA"}]}]
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([0]))) == 0


def test_a_non_dict_tray_entry_is_stepped_over(scheduler):
    """Nothing between the derivation and `_dispatch_one`'s try/finally catches
    an exception, so a junk tray entry would leave the item holding its
    dispatch claim. The real tray beside it is still read."""
    ams = [{"id": 0, "tray": ["junk", {"id": 1, "tray_type": "ASA"}]}]
    assert _derive(scheduler, _make_state(ams=ams), _make_item(json.dumps([1]))) == 45
    assert _derive(scheduler, _make_state(ams=ams), _make_item(None)) == 45


def test_no_ams_telemetry_derives_zero(scheduler):
    assert _derive(scheduler, _make_state(), _make_item(PLA_ONLY_MAPPING)) == 0


def test_no_printer_status_derives_zero(scheduler):
    assert _derive(scheduler, None, _make_item(PLA_ONLY_MAPPING)) == 0


# ----------------------------------------------------------------------------
# External spool
# ----------------------------------------------------------------------------


def test_an_external_spool_the_mapping_names_is_read(scheduler):
    """254/255 address the external feeds. Before the mapping was consulted the
    external spool was invisible to the derivation, so an ASA print fed from it
    got no preheat at all.

    The AMS deliberately holds only PLA: an ASA tray here would let this pass
    without `vt_tray` ever being read."""
    state = _make_state(
        ams=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}],
        vt_tray=[{"id": 254, "tray_type": "ASA"}],
    )
    assert _derive(scheduler, state, _make_item(json.dumps([254]))) == 45


def test_an_external_spool_without_an_id_defaults_to_254(scheduler):
    """`_build_loaded_filaments` writes the same default, so a mapping built
    from it addresses the entry as 254."""
    state = _make_state(vt_tray=[{"tray_type": "ABS"}])
    assert _derive(scheduler, state, _make_item(json.dumps([254]))) == 45


def test_an_external_spool_the_mapping_does_not_name_is_ignored(scheduler):
    """The PLA job maps an AMS tray; the ASA hanging off the back is not part
    of this print."""
    state = _make_state(ams=_reporter_ams(), vt_tray=[{"id": 254, "tray_type": "ASA"}])
    assert _derive(scheduler, state, _make_item(PLA_ONLY_MAPPING)) == 0


def test_the_external_spool_stays_out_of_the_unnarrowed_scan(scheduler):
    """Without a mapping the scan is AMS-only, as it always was. Reading
    `vt_tray` here would newly preheat for a spool that may not be in use, so
    the fix is scoped to what the mapping positively states."""
    state = _make_state(
        ams=[{"id": 0, "tray": [{"id": 0, "tray_type": "PLA"}]}], vt_tray=[{"id": 254, "tray_type": "ASA"}]
    )
    assert _derive(scheduler, state, _make_item(None)) == 0


def test_a_non_dict_vt_tray_entry_is_skipped(scheduler):
    """Older firmware surfaced `vt_tray` as a dict; iterating it yields keys.

    The junk entry must be stepped over rather than raise, and the real one
    after it still read."""
    state = _make_state(vt_tray=["255", {"id": 254, "tray_type": "ASA"}])
    assert _derive(scheduler, state, _make_item(json.dumps([254]))) == 45


# ----------------------------------------------------------------------------
# Keep-warm reads the same narrowing
# ----------------------------------------------------------------------------


def test_keep_warm_uses_the_next_items_mapping(scheduler):
    """`_apply_keep_warm` gates the bed hold on the same derivation, so an ASA
    spool the next job never touches must not hold the bed at 90°C through the
    plate-clearing window."""
    pla_next = _make_item(PLA_ONLY_MAPPING)
    asa_next = _make_item(json.dumps([ASA_TRAY]))
    state = _make_state(ams=_reporter_ams())
    assert _derive(scheduler, state, pla_next) == 0
    assert _derive(scheduler, state, asa_next) == 45
