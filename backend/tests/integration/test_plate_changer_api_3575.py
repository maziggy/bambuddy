"""Plate changer capacity through the printers API (#3575)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

WS = "backend.app.api.routes.printers.ws_manager"


@pytest.fixture
def ws():
    with patch(WS) as mock:
        mock.send_printer_plates_changed = AsyncMock()
        mock.refresh_printer_scopes = AsyncMock()
        yield mock.send_printer_plates_changed


async def _patch(async_client, printer_id, body, **kwargs):
    return await async_client.patch(f"/api/v1/printers/{printer_id}", json=body, **kwargs)


class TestPatchCapacity:
    async def test_new_printers_have_counting_off(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory()
        data = (await async_client.get(f"/api/v1/printers/{printer.id}")).json()
        assert data["plate_capacity"] is None
        assert data["plates_remaining"] is None

    async def test_turning_it_on_starts_full(self, async_client, printer_factory, ws):
        printer = await printer_factory()

        response = await _patch(async_client, printer.id, {"plate_capacity": 5})

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (5, 5)
        got = (await async_client.get(f"/api/v1/printers/{printer.id}")).json()
        assert (got["plate_capacity"], got["plates_remaining"]) == (5, 5)
        ws.assert_awaited_once_with(printer.id, 5, 5)

    async def test_lowering_the_capacity_clamps_the_count(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=4)

        response = await _patch(async_client, printer.id, {"plate_capacity": 3})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (3, 3)
        ws.assert_awaited_once_with(printer.id, 3, 3)

    async def test_the_clamp_is_one_conditional_sql_update(self, async_client, printer_factory, ws, test_engine):
        """A print finishing during the PATCH may count down: the clamp must not overwrite it."""
        from sqlalchemy import event

        printer = await printer_factory(plate_capacity=5, plates_remaining=4)
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE PRINTERS"):
                statements.append(statement)

        event.listen(test_engine.sync_engine, "before_cursor_execute", record)
        try:
            response = await _patch(async_client, printer.id, {"plate_capacity": 3})
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", record)

        assert response.json()["plates_remaining"] == 3
        assert any("plates_remaining >" in sql for sql in statements), statements
        # The ORM flush itself carries the capacity only, never a Python-side count.
        assert not any("plates_remaining=?" in sql.replace(" ", "") and "plate_capacity" in sql for sql in statements)

    async def test_lowering_below_nothing_keeps_a_smaller_count(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=1)

        response = await _patch(async_client, printer.id, {"plate_capacity": 3})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (3, 1)

    async def test_raising_the_capacity_keeps_the_count(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=2)

        response = await _patch(async_client, printer.id, {"plate_capacity": 8})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (8, 2)

    async def test_capacity_with_a_missing_count_starts_full(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=None)

        response = await _patch(async_client, printer.id, {"plate_capacity": 6})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (6, 6)

    async def test_null_capacity_clears_both(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=2)

        response = await _patch(async_client, printer.id, {"plate_capacity": None})

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (None, None)
        ws.assert_awaited_once_with(printer.id, None, None)

    async def test_null_capacity_with_a_count_still_clears_both(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=2)

        response = await _patch(async_client, printer.id, {"plate_capacity": None, "plates_remaining": 1})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (None, None)

    async def test_plates_remaining_zero_is_accepted(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=4)

        response = await _patch(async_client, printer.id, {"plates_remaining": 0})

        assert response.status_code == 200, response.text
        assert response.json()["plates_remaining"] == 0
        ws.assert_awaited_once_with(printer.id, 0, 5)

    async def test_remaining_above_the_stored_capacity_is_a_400(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=4)

        response = await _patch(async_client, printer.id, {"plates_remaining": 6})

        assert response.status_code == 400
        got = (await async_client.get(f"/api/v1/printers/{printer.id}")).json()
        assert got["plates_remaining"] == 4
        ws.assert_not_awaited()

    async def test_remaining_without_any_capacity_is_ignored(self, async_client, printer_factory, ws):
        printer = await printer_factory()

        response = await _patch(async_client, printer.id, {"plates_remaining": 3})

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (None, None)

    async def test_both_in_the_body_with_remaining_above_capacity_is_a_422(self, async_client, printer_factory, ws):
        printer = await printer_factory()

        response = await _patch(async_client, printer.id, {"plate_capacity": 3, "plates_remaining": 4})

        assert response.status_code == 422

    async def test_both_in_the_body_within_range(self, async_client, printer_factory, ws):
        printer = await printer_factory()

        response = await _patch(async_client, printer.id, {"plate_capacity": 4, "plates_remaining": 2})

        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (4, 2)

    @pytest.mark.parametrize(
        "body",
        [{"plate_capacity": 0}, {"plate_capacity": 1000}, {"plate_capacity": -1}, {"plates_remaining": -1}],
    )
    async def test_out_of_range_values_are_a_422(self, async_client, printer_factory, ws, body):
        printer = await printer_factory(plate_capacity=5, plates_remaining=5)

        assert (await _patch(async_client, printer.id, body)).status_code == 422

    async def test_an_unrelated_patch_leaves_the_count_and_sends_nothing(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=2)

        response = await _patch(async_client, printer.id, {"name": "Renamed"})

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (5, 2)
        ws.assert_not_awaited()

    async def test_same_capacity_again_sends_nothing(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=2)

        await _patch(async_client, printer.id, {"plate_capacity": 5})

        ws.assert_not_awaited()

    async def test_a_failing_broadcast_does_not_fail_the_patch(self, async_client, printer_factory, ws):
        printer = await printer_factory()
        ws.side_effect = RuntimeError("ws down")

        response = await _patch(async_client, printer.id, {"plate_capacity": 5})

        assert response.status_code == 200, response.text
        assert response.json()["plates_remaining"] == 5


class TestCountUnknown:
    async def test_a_refill_and_a_capacity_patch_clear_the_unbooked_mark(self, async_client, printer_factory, ws):
        from backend.app.services import plate_changer

        printer = await printer_factory(plate_capacity=5, plates_remaining=0)
        plate_changer.mark_count_unknown(printer.id)
        await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")
        assert not plate_changer.is_count_unknown(printer.id)

        plate_changer.mark_count_unknown(printer.id)
        await _patch(async_client, printer.id, {"name": "Other"})
        assert plate_changer.is_count_unknown(printer.id)  # unrelated edits leave it
        # The edit dialog repeats the unchanged capacity on every save.
        await _patch(async_client, printer.id, {"name": "Other2", "plate_capacity": 5})
        assert plate_changer.is_count_unknown(printer.id)
        await _patch(async_client, printer.id, {"plates_remaining": None})
        assert plate_changer.is_count_unknown(printer.id)
        await _patch(async_client, printer.id, {"plate_capacity": 6})  # a real change
        assert not plate_changer.is_count_unknown(printer.id)

        plate_changer.mark_count_unknown(printer.id)
        await _patch(async_client, printer.id, {"plates_remaining": 2})
        assert not plate_changer.is_count_unknown(printer.id)

    async def test_a_null_plates_remaining_with_a_capacity_is_unchanged(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=3)

        response = await _patch(async_client, printer.id, {"plates_remaining": None})

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (5, 3)


class TestRefill:
    async def test_resets_to_the_capacity(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=0)

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        assert response.status_code == 200, response.text
        assert (response.json()["plate_capacity"], response.json()["plates_remaining"]) == (5, 5)
        assert (await async_client.get(f"/api/v1/printers/{printer.id}")).json()["plates_remaining"] == 5
        ws.assert_awaited_once_with(printer.id, 5, 5)

    async def test_without_a_capacity_is_a_400(self, async_client, printer_factory, ws):
        printer = await printer_factory()

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        assert response.status_code == 400
        ws.assert_not_awaited()

    async def test_unknown_printer_is_a_404(self, async_client, ws):
        assert (await async_client.post("/api/v1/printers/9999/refill-plates")).status_code == 404

    async def test_works_while_the_printer_is_disconnected(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=4, plates_remaining=1)

        with (
            patch("backend.app.api.routes.printers.printer_manager.is_connected", return_value=False),
            patch("backend.app.api.routes.printers.printer_manager.get_status", return_value=None),
        ):
            response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        assert response.status_code == 200, response.text
        assert response.json()["plates_remaining"] == 4

    async def test_does_not_touch_the_plate_clear_gate(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=4, plates_remaining=0)

        with patch("backend.app.api.routes.printers.printer_manager.set_awaiting_plate_clear") as set_gate:
            await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        set_gate.assert_not_called()

    async def test_a_failing_broadcast_does_not_fail_the_refill(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=4, plates_remaining=0)
        ws.side_effect = RuntimeError("ws down")

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        assert response.status_code == 200, response.text


class TestRefillPermission:
    async def test_needs_the_clear_plate_permission(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=0)
        admin = await _admin_token(async_client)
        reader = await _group(async_client, admin, "readers", permissions=["printers:read"])
        jwt, _ = await _user(async_client, admin, "reader", [reader])

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates", headers=_auth(jwt))

        assert response.status_code == 403
        assert (await async_client.get(f"/api/v1/printers/{printer.id}", headers=_auth(admin))).json()[
            "plates_remaining"
        ] == 0

    async def test_allowed_with_the_permission(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=0)
        admin = await _admin_token(async_client)
        group = await _group(async_client, admin, "clearers", permissions=["printers:read", "printers:clear_plate"])
        jwt, _ = await _user(async_client, admin, "clearer", [group])

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates", headers=_auth(jwt))

        assert response.status_code == 200, response.text
        assert response.json()["plates_remaining"] == 5

    async def test_unauthenticated_is_rejected_when_auth_is_on(self, async_client, printer_factory, ws):
        printer = await printer_factory(plate_capacity=5, plates_remaining=0)
        await _admin_token(async_client)

        response = await async_client.post(f"/api/v1/printers/{printer.id}/refill-plates")

        assert response.status_code in (401, 403)
