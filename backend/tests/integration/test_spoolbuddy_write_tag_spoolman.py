"""Writing an NFC sticker for a Spoolman spool (SM-C6, SM-C5).

SM-C6: the "color_name not set" warning checked the Spoolman filament field,
which Spoolman never fills -- Bambuddy keeps the name in the spool's extra
-- so every write warned. A spool without a stored name gets its subtype
encoded, not an empty name.

SM-C5: a Bambu spool created from the AMS is linked by its tray UUID in
``extra.tag``. Writing a sticker replaced it, so the next AMS sync no longer
found the spool and created a duplicate.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spoolbuddy_device import SpoolBuddyDevice
from backend.tests._fixtures.spoolman_tags import BASE_URL, FakeSpoolman
from backend.tests.integration.test_spoolman_native_tags_3168 import TRAY_UUID, serving

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

WRITE = "/api/v1/spoolbuddy/nfc/write-tag"


@pytest.fixture
async def ready(db_session: AsyncSession):
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(Settings(key="spoolman_url", value=BASE_URL))
    db_session.add(
        SpoolBuddyDevice(
            device_id="sb-sticker",
            hostname="spoolbuddy",
            ip_address="10.0.0.9",
            firmware_version="1.0.0",
            has_nfc=True,
            has_scale=True,
            tare_offset=0,
            calibration_factor=1.0,
            last_seen=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()


async def _write(async_client, fake: FakeSpoolman, spool_id: int):
    with serving(fake):
        return await async_client.post(WRITE, json={"device_id": "sb-sticker", "spool_id": spool_id})


def _colour_warnings(response) -> list[str]:
    return [w for w in response.json().get("warnings", []) if "color" in w.lower()]


async def test_a_stored_colour_name_raises_no_warning(async_client, ready):
    fake = FakeSpoolman()
    fake.add_spool(7)["extra"]["bambu_color_name"] = json.dumps("Jade White")

    response = await _write(async_client, fake, 7)

    assert response.status_code == 200, response.text
    assert _colour_warnings(response) == []


async def test_a_missing_colour_name_says_the_subtype_is_encoded(async_client, ready):
    fake = FakeSpoolman()
    fake.add_spool(7)

    response = await _write(async_client, fake, 7)

    assert response.status_code == 200, response.text
    [warning] = _colour_warnings(response)
    assert "subtype" in warning


async def test_a_spool_linked_by_its_ams_tray_uuid_is_not_overwritten(async_client, ready):
    fake = FakeSpoolman()
    fake.add_spool(7, extra_tag=TRAY_UUID)

    response = await _write(async_client, fake, 7)

    assert response.status_code == 409
    assert "AMS" in response.json()["detail"]


async def test_a_tray_uuid_spoolman_also_keeps_natively_is_no_reason_to_refuse(async_client, ready):
    """Spoolman 0.27+ keeps the tray UUID as a native tag too, and the AMS
    sync finds the spool by that first: the sticker cannot break the link."""
    fake = FakeSpoolman()
    fake.add_spool(7, extra_tag=TRAY_UUID, tags=[TRAY_UUID])

    response = await _write(async_client, fake, 7)

    assert response.status_code == 200, response.text


async def test_a_chip_uid_in_extra_tag_is_no_reason_to_refuse(async_client, ready):
    """The tray UUID only native, extra.tag holding a chip UID: nothing a
    sticker overwrites links the spool to the AMS."""
    fake = FakeSpoolman()
    fake.add_spool(7, extra_tag="D3E68F32", tags=[TRAY_UUID])

    response = await _write(async_client, fake, 7)

    assert response.status_code == 200, response.text
