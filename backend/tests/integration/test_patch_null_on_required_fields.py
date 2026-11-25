"""An explicit null for a field that can't be empty leaves it unchanged.

Update schemas declare most fields ``X | None = None`` so a PATCH can leave
them out, and most routes treat null as "unchanged". Routes that apply
``model_dump(exclude_unset=True)`` with setattr instead wrote a JSON null
straight into a NOT NULL column, and the request ended in a 500. The schema
now drops such a null before the route sees it, so every route treats it as
"unchanged"; a null for a column that may be empty still clears it.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_smart_plug(async_client: AsyncClient, smart_plug_factory, db_session):
    plug = await smart_plug_factory(username="admin", controls_printer_power=False)

    kept = await async_client.patch(f"/api/v1/smart-plugs/{plug.id}", json={"controls_printer_power": None})
    cleared = await async_client.patch(f"/api/v1/smart-plugs/{plug.id}", json={"username": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["controls_printer_power"] is False
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["username"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_printer(async_client: AsyncClient, printer_factory, db_session):
    printer = await printer_factory(location="Shelf")

    kept = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"camera_light_auto": None})
    cleared = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"location": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["camera_light_auto"] is False
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["location"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_archive(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, notes="first", quantity=3)

    kept = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"quantity": None})
    cleared = await async_client.patch(f"/api/v1/archives/{archive.id}", json={"notes": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["quantity"] == 3
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["notes"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_notification_provider(async_client: AsyncClient, notification_provider_factory, db_session):
    provider = await notification_provider_factory(name="Phone")

    kept = await async_client.patch(f"/api/v1/notifications/{provider.id}", json={"name": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["name"] == "Phone"


async def _queue_item(db_session, printer_factory, archive_factory):
    from backend.app.models.print_queue import PrintQueueItem

    printer = await printer_factory()
    archive = await archive_factory(printer.id)
    item = PrintQueueItem(printer_id=printer.id, archive_id=archive.id, position=1, bed_levelling="off")
    db_session.add(item)
    await db_session.commit()
    return item


@pytest.mark.asyncio
@pytest.mark.integration
async def test_queue_item(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    item = await _queue_item(db_session, printer_factory, archive_factory)

    kept = await async_client.patch(f"/api/v1/queue/{item.id}", json={"bed_levelling": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["bed_levelling"] == "off"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_queue_bulk_update(async_client: AsyncClient, printer_factory, archive_factory, db_session):
    item = await _queue_item(db_session, printer_factory, archive_factory)

    response = await async_client.patch(
        "/api/v1/queue/bulk", json={"item_ids": [item.id], "flow_cali": None, "bed_levelling": "on"}
    )

    assert response.status_code == 200, response.text
    await db_session.refresh(item)
    assert item.bed_levelling == "on"
    assert item.flow_cali == "auto"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_github_backup_config(async_client: AsyncClient, db_session):
    from backend.app.models.github_backup import GitHubBackupConfig

    db_session.add(GitHubBackupConfig(repository_url="https://github.com/o/r", access_token="t", branch="main"))
    await db_session.commit()

    kept = await async_client.patch("/api/v1/github-backup/config", json={"provider": None})

    assert kept.status_code == 200, kept.text
    assert kept.json()["provider"] == "github"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_null_next_to_a_real_change_does_not_block_it(async_client: AsyncClient, db_session):
    """On the routes that already skipped a null (projects among them) the rest
    of the request was applied; it must stay that way."""
    created = await async_client.post("/api/v1/projects/", json={"name": "Shelf"})
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    response = await async_client.patch(f"/api/v1/projects/{project_id}", json={"name": None, "notes": "x"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Shelf"
    assert response.json()["notes"] == "x"
