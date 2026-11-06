"""Without internet, the firmware check logs the outage once, not every failure.

Each check of each printer model fetches the Bambu Lab wiki and download page.
Offline, every one of those fetches logged a full ERROR (and every model a
WARNING): measured, 3 polls of 3 models gave 12 ERROR and 9 WARNING lines
saying the same thing. The outage is now logged once as a WARNING, failures
during it at debug, and the recovery once at INFO.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.firmware_check import FirmwareCheckService

LOGGER = "backend.app.services.firmware_check"


class _Offline:
    """Both HTTP clients failing at the transport, as without internet."""

    def __init__(self, service: FirmwareCheckService):
        self.service = service
        self.online = False
        self.curl = MagicMock()
        self.curl.get = AsyncMock(side_effect=self._curl_get)

    async def _curl_get(self, _url):
        if not self.online:
            raise RuntimeError("Failed to perform, curl: (7) Couldn't connect to server")
        return MagicMock(status_code=503, text="")

    async def httpx_get(self, url, **_kwargs):
        if not self.online:
            raise httpx.ConnectError("All connection attempts failed")
        return httpx.Response(200, text='<h2 id="h-01090000-20260101">x</h2>', request=httpx.Request("GET", url))


def _records(caplog, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == level]


@pytest.fixture
def offline(tmp_path):
    service = FirmwareCheckService()
    net = _Offline(service)
    with (
        patch("backend.app.services.firmware_check._data_dir", tmp_path),
        patch.object(service, "_get_bambulab_client", return_value=net.curl),
        patch.object(service._client, "get", new=net.httpx_get),
    ):
        yield service, net


@pytest.mark.asyncio
async def test_an_outage_is_logged_once(offline, caplog):
    service, _net = offline
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    for _poll in range(3):
        for model in ("X1C", "P1S", "H2D"):
            await service.check_for_update(model, "01.00.00.00")

    assert _records(caplog, logging.ERROR) == []
    # Once per host: the wiki and bambulab.com fail independently.
    warnings = [r.getMessage() for r in _records(caplog, logging.WARNING)]
    assert len(warnings) == 2, warnings
    assert {w.split()[0] for w in warnings} == {"wiki.bambulab.com", "bambulab.com"}
    assert all("unreachable" in w for w in warnings)


@pytest.mark.asyncio
async def test_the_recovery_is_logged_and_a_new_outage_warns_again(offline, caplog):
    service, net = offline
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    await service.check_for_update("X1C", "01.00.00.00")
    net.online = True
    await service.check_for_update("P1S", "01.00.00.00")
    recovered = [r.getMessage() for r in _records(caplog, logging.INFO) if "reachable again" in r.getMessage()]
    assert {m.split()[0] for m in recovered} == {"wiki.bambulab.com", "bambulab.com"}

    net.online = False
    caplog.clear()
    await service.check_for_update("H2D", "01.00.00.00")
    assert len(_records(caplog, logging.WARNING)) == 2


@pytest.mark.asyncio
async def test_one_host_down_is_logged_once_without_false_recoveries(offline, caplog):
    """The wiki answers while bambulab.com does not: one warning for that host,
    no 'reachable again' from the other."""
    service, _net = offline
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    async def wiki_up_only(url, **_kwargs):
        return httpx.Response(200, text='<h2 id="h-01090000-20260101">x</h2>', request=httpx.Request("GET", url))

    with patch.object(service._client, "get", new=wiki_up_only):
        for _poll in range(3):
            for model in ("X1C", "P1S", "H2D"):
                await service.check_for_update(model, "01.00.00.00")

    assert len(_records(caplog, logging.WARNING)) == 1
    assert [r for r in _records(caplog, logging.INFO) if "reachable again" in r.getMessage()] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.TooManyRedirects("loop"), httpx.DecodingError("bad encoding")])
async def test_a_wiki_error_that_is_not_an_outage_still_degrades_quietly(offline, error):
    service, _net = offline

    async def failing_wiki(_url, **_kwargs):
        raise error

    with patch.object(service._client, "get", new=failing_wiki):
        result = await service.check_for_update("X1C", "01.00.00.00")

    assert isinstance(result, dict)
