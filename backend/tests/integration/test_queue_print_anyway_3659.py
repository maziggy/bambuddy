"""Print Anyway for a job waiting on a short spool (#3659).

With Loaded filament first on (#3587) a job whose spools are too short waits
instead of being staged. Start on it asks the same "Insufficient filament"
question as on a staged job; for an "any model" job that has no printer yet,
the short spools are named per printer of the model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.filament_deficit import FilamentDeficit
from backend.app.services.print_scheduler import scheduler

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SHORT_WAIT = "Waiting for filament: P1S left (needs more filament); P1S right (needs more filament) · priority in 60 h"


async def _model_job(db_session, archive_id: int, **fields) -> PrintQueueItem:
    item = PrintQueueItem(
        **{
            "printer_id": None,
            "target_model": "P1S",
            "archive_id": archive_id,
            "status": "pending",
            "position": 1,
            "waiting_reason": SHORT_WAIT,
            **fields,
        }
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


def _short(printer_id: int) -> list[FilamentDeficit]:
    return [
        FilamentDeficit(
            slot_id=1,
            ams_id=0,
            tray_id=printer_id,
            filament_type="PLA",
            required_grams=505.0,
            remaining_grams=196.0,
        )
    ]


@pytest.fixture
async def two_printers(printer_factory, archive_factory):
    left = await printer_factory(name="P1S left", model="P1S")
    right = await printer_factory(name="P1S right", model="P1S")
    archive = await archive_factory(left.id)
    return left, right, archive


def _deficit_on(short_ids: set[int]):
    async def deficit(_db, _item, *, printer_id=None, ams_mapping=None, require_known=False):
        return _short(printer_id) if printer_id in short_ids else []

    return deficit


def _patches(deficit, missing_types=lambda _pid, _types: []):
    return [
        patch("backend.app.services.print_scheduler.compute_deficit_for_queue_item", side_effect=deficit),
        patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[0])),
        patch.object(scheduler, "_get_missing_filament_types", side_effect=missing_types),
    ]


async def _start(async_client, item_id: int, skip: bool = False):
    query = "?skip_filament_check=true" if skip else ""
    return await async_client.post(f"/api/v1/queue/{item_id}/start{query}")


async def test_an_any_model_job_names_each_short_printer(async_client, db_session, two_printers):
    left, right, archive = two_printers
    item = await _model_job(db_session, archive.id, required_filament_types='["PLA"]')

    p_deficit, p_mapping, p_types = _patches(_deficit_on({left.id, right.id}))

    with p_deficit, p_mapping, p_types:
        response = await _start(async_client, item.id)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_filament"
    assert [(d["printer_name"], d["required_grams"], d["remaining_grams"]) for d in detail["deficit"]] == [
        ("P1S left", 505.0, 196.0),
        ("P1S right", 505.0, 196.0),
    ]
    await db_session.refresh(item)
    assert item.skip_filament_check is False


async def test_a_printer_without_the_filament_is_not_listed(async_client, db_session, two_printers):
    left, right, archive = two_printers
    item = await _model_job(db_session, archive.id, required_filament_types='["PLA"]')
    p_deficit, p_mapping, _ = _patches(_deficit_on({left.id, right.id}))
    p_types = patch.object(
        scheduler, "_get_missing_filament_types", side_effect=lambda pid, _t: ["PLA"] if pid == right.id else []
    )

    with p_deficit, p_mapping, p_types:
        response = await _start(async_client, item.id)

    assert [d["printer_name"] for d in response.json()["detail"]["deficit"]] == ["P1S left"]


async def test_print_anyway_releases_the_job(async_client, db_session, two_printers):
    left, right, archive = two_printers
    item = await _model_job(db_session, archive.id)
    p_deficit, p_mapping, p_types = _patches(_deficit_on({left.id, right.id}))

    with p_deficit as deficit, p_mapping, p_types:
        response = await _start(async_client, item.id, skip=True)

    assert response.status_code == 200, response.text
    deficit.assert_not_called()
    await db_session.refresh(item)
    # The scheduler's matcher and filament wait honour it from the next pass.
    assert item.skip_filament_check is True


@pytest.mark.parametrize(
    "reason",
    [
        # The filament is not loaded at all: nothing for "Print Anyway" to release.
        "Waiting for filament: P1S left (needs PLA (Red))",
        # Only busy: it starts by itself.
        "Busy: P1S left, P1S right",
        None,
    ],
)
async def test_only_a_short_spool_is_asked_about(async_client, db_session, two_printers, reason):
    left, right, archive = two_printers
    item = await _model_job(db_session, archive.id, waiting_reason=reason)
    p_deficit, p_mapping, p_types = _patches(_deficit_on({left.id, right.id}))

    with p_deficit as deficit, p_mapping, p_types:
        response = await _start(async_client, item.id)

    assert response.status_code == 200, response.text
    deficit.assert_not_called()
    await db_session.refresh(item)
    assert item.skip_filament_check is False


async def test_a_short_spool_that_was_topped_up_starts_without_asking(async_client, db_session, two_printers):
    _left, _right, archive = two_printers
    item = await _model_job(db_session, archive.id)
    p_deficit, p_mapping, p_types = _patches(_deficit_on(set()))

    with p_deficit, p_mapping, p_types:
        response = await _start(async_client, item.id)

    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.skip_filament_check is False


async def test_a_failing_check_does_not_break_start(async_client, db_session, two_printers):
    _left, _right, archive = two_printers
    item = await _model_job(db_session, archive.id)

    with patch.object(scheduler, "model_job_filament_deficit", AsyncMock(side_effect=RuntimeError("boom"))):
        response = await _start(async_client, item.id)

    assert response.status_code == 200, response.text
