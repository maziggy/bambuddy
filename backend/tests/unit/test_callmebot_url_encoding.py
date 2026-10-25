"""CallMeBot gets the phone number and API key URL-encoded.

They went into the query string as typed, so a phone number written as
"+49 151 2345678" reached CallMeBot without its "+", which a query string reads
as a space.
"""

from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.services.notification_service import NotificationService


@pytest.mark.asyncio
async def test_the_phone_and_key_arrive_as_typed():
    service = NotificationService()
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    service._get_client = AsyncMock(return_value=client)

    ok, _ = await service._send_callmebot({"phone": "+49 151 2345678", "apikey": "12&34"}, "Done: 100%")

    assert ok is True
    query = parse_qs(urlparse(client.get.await_args.args[0]).query)
    assert query["phone"] == ["+49 151 2345678"]
    assert query["apikey"] == ["12&34"]
    assert query["text"] == ["Done: 100%"]
