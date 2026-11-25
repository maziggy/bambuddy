"""Spoolman's "last used" means a print used the spool.

``update_spool`` stamped ``last_used = now`` on every call, and it is called by
the AMS sync for every loaded spool, by clearing a removed spool's location and
by weighing on the SpoolBuddy scale: every sync marked every loaded spool as
just used. Print usage goes through Spoolman's /use endpoint, which sets
last_used itself (Spoolman's database.spool.use_weight), so update_spool no
longer touches it. Local mode already behaved this way.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.spoolman import SpoolmanClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"remaining_weight": 500.0}, {"location": "Printer 1 AMS A"}, {"clear_location": True}],
    ids=["weight", "location", "clear-location"],
)
async def test_an_update_does_not_mark_the_spool_used(kwargs):
    client = SpoolmanClient("http://spoolman.invalid")
    response = MagicMock()
    response.json.return_value = {"id": 3}
    client._request_spool = AsyncMock(return_value=response)

    await client.update_spool(spool_id=3, **kwargs)

    body = client._request_spool.await_args.kwargs["json_body"]
    assert "last_used" not in body
