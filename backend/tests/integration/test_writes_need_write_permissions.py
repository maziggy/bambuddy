"""Routes that change shared state need a write permission, not a read one.

Each route is called by a user holding only the permission it used to accept
(refused) and by one holding the permission it needs now (let through; it may
still fail later for its own reasons, e.g. no camera or no such device).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# (method, path template, permission that used to suffice, permission needed now)
ROUTES = [
    ("POST", "/api/v1/printers/{printer}/camera/plate-detection/calibrate", "camera:view", "printers:update"),
    ("PUT", "/api/v1/printers/{printer}/camera/plate-detection/references/0", "camera:view", "printers:update"),
    ("DELETE", "/api/v1/printers/{printer}/camera/plate-detection/references/0", "camera:view", "printers:update"),
    ("POST", "/api/v1/bug-report/stop-logging?was_debug=true", "settings:read", "settings:update"),
    ("POST", "/api/v1/smart-plugs/discover/scan", "smart_plugs:read", "smart_plugs:create"),
    ("POST", "/api/v1/smart-plugs/discover/stop", "smart_plugs:read", "smart_plugs:create"),
    ("POST", "/api/v1/spoolbuddy/diagnostics/no-such-device/run", "inventory:read", "inventory:update"),
]


async def _call(client: AsyncClient, headers: dict[str, str], method: str, path: str):
    kwargs = {"json": {"label": "x"}} if method == "PUT" else {}
    return await client.request(method, path, headers=headers, **kwargs)


@pytest.mark.parametrize(("method", "path", "read_permission", "write_permission"), ROUTES)
async def test_a_read_permission_no_longer_changes_anything(
    async_client: AsyncClient, printer_factory, method, path, read_permission, write_permission
):
    printer = await printer_factory()
    admin = await _admin_token(async_client)
    reader = _auth(
        (
            await _user(
                async_client,
                admin,
                "reader",
                [await _group(async_client, admin, "r", permissions=["printers:read", read_permission])],
            )
        )[0]
    )
    writer = _auth(
        (
            await _user(
                async_client,
                admin,
                "writer",
                [
                    await _group(
                        async_client, admin, "w", permissions=["printers:read", read_permission, write_permission]
                    )
                ],
            )
        )[0]
    )
    url = path.format(printer=printer.id)

    # Keep the let-through calls from touching the network or the log level.
    with (
        patch("backend.app.api.routes.smart_plugs.tasmota_scanner") as scanner,
        patch("backend.app.api.routes.bug_report._get_recent_sanitized_logs", new=AsyncMock(return_value="")),
    ):
        scanner.scan_range = AsyncMock()
        scanner.progress = (0, 0)
        scanner.is_running = False
        refused = await _call(async_client, reader, method, url)
        allowed = await _call(async_client, writer, method, url)

    assert refused.status_code == 403, refused.text
    assert allowed.status_code != 403, allowed.text
