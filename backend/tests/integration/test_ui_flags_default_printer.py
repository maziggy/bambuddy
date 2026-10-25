"""The print dialog preselects the default printer, for users who cannot read
all settings too: it comes with the UI flags, and only when the caller may use
that printer."""

import pytest
from httpx import AsyncClient

from backend.app.models.settings import Settings
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_default_printer_comes_with_the_ui_flags(async_client: AsyncClient, db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(Settings(key="default_printer_id", value=str(printer.id)))
    await db_session.commit()

    flags = (await async_client.get("/api/v1/settings/ui-flags")).json()

    assert flags["default_printer_id"] == printer.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_default_printer_outside_the_callers_printers_is_not_offered(
    async_client: AsyncClient, db_session, printer_factory
):
    mine = await printer_factory()
    theirs = await printer_factory()
    db_session.add(Settings(key="default_printer_id", value=str(theirs.id)))
    await db_session.commit()
    admin = await _admin_token(async_client)
    team = await _group(async_client, admin, "team", permissions=["printers:read"], printer_ids=[mine.id])
    jwt, _ = await _user(async_client, admin, "member", [team])

    flags = (await async_client.get("/api/v1/settings/ui-flags", headers=_auth(jwt))).json()

    assert flags["default_printer_id"] is None
