"""A printer's own "Require plate-clear confirmation" through the API (#2860).

The value lives on the printer row and is cached for the scheduler, WLED and
the notification; the cache must follow every write, and a deleted printer's
override must not pass to a later printer given the same id.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.services import plate_clear
from backend.app.services.printer_manager import PrinterManager


@pytest.fixture(autouse=True)
def overrides():
    plate_clear.load_overrides({})
    yield plate_clear
    plate_clear.load_overrides({})


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_printer_follows_the_setting(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()

    response = await async_client.get(f"/api/v1/printers/{printer.id}")

    assert response.status_code == 200
    assert response.json()["require_plate_clear"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_round_trips_and_reaches_the_cache(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})
    assert response.status_code == 200
    assert response.json()["require_plate_clear"] is False
    assert plate_clear.applies(printer.id, True) is False

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": True})
    assert response.json()["require_plate_clear"] is True
    assert plate_clear.applies(printer.id, False) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_null_returns_to_the_setting(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": None})

    assert response.status_code == 200
    assert response.json()["require_plate_clear"] is None
    assert plate_clear.applies(printer.id, True) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_other_updates_leave_it_alone(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})

    response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"name": "Renamed"})

    assert response.json()["require_plate_clear"] is False
    assert plate_clear.applies(printer.id, True) is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_drops_the_override(async_client: AsyncClient, printer_factory):
    printer = await printer_factory()
    await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})

    response = await async_client.delete(f"/api/v1/printers/{printer.id}")

    assert response.status_code == 200
    assert plate_clear.applies(printer.id, True) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_delete_keeps_the_override(async_client: AsyncClient, printer_factory):
    """The override goes only once the printer row is gone: a delete that
    fails before its commit leaves the printer, and its setting, as they were."""
    printer = await printer_factory()
    await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})

    with (
        patch(
            "backend.app.core.printer_scope.drop_printers_from_api_keys",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        await async_client.delete(f"/api/v1/printers/{printer.id}")

    assert plate_clear.applies(printer.id, True) is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_startup_loads_the_overrides(test_engine, printer_factory, overrides):
    on = await printer_factory(require_plate_clear=True)
    off = await printer_factory(require_plate_clear=False)
    following = await printer_factory()
    overrides.set_override(following.id, False)  # stale: must be replaced

    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with patch("backend.app.core.database.async_session", session_maker):
        await PrinterManager().load_awaiting_plate_clear_from_db()

    assert plate_clear.applies(on.id, False) is True
    assert plate_clear.applies(off.id, True) is False
    assert plate_clear.applies(following.id, True) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_refreshes_the_wled_preset(async_client: AsyncClient, printer_factory):
    """A printer's lights follow a changed override at once, not at its next
    status push, which on an idle printer can be a while."""
    printer = await printer_factory(is_active=True)
    state = object()
    with (
        patch("backend.app.api.routes.printers.printer_manager.get_status", return_value=state),
        patch("backend.app.api.routes.printers.wled_manager.handle_status") as handle_status,
        patch("backend.app.api.routes.printers.wled_manager.configure_printer") as configure,
    ):
        response = await async_client.patch(f"/api/v1/printers/{printer.id}", json={"require_plate_clear": False})

    assert response.status_code == 200
    assert handle_status.call_args.args[:2] == (printer.id, state)
    # The WLED config itself did not change, so its runtime is left alone.
    configure.assert_not_called()
