"""The consumed-counter reset must not throw away the measured remaining weight (#2906).

"Reset usage to 0" on a Spoolman-backed spool PATCHed ``used_weight = 0``.
Spoolman derives ``remaining_weight`` as initial minus used, so zeroing the
used weight put the spool back to full — while the confirmation dialog promised,
in all thirteen locales, that "the spool itself, its remaining weight
calculation, and your settings are not changed."

The fake below is the point of these tests. It recomputes remaining the way a
real Spoolman does instead of accepting whatever it is told, so a reset that
writes ``used_weight`` fails here for the same reason it failed on the reporting
instance. Mocking that rule away is how the original implementation shipped
green: ``test_spoolman_inventory_api.py`` staged ``used_weight: 0`` next to
``remaining_weight: 750.0``, a pair real Spoolman cannot return.
"""

import json

import httpx
import pytest

from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
from backend.app.services.spoolman import SpoolmanClient

LABEL_WEIGHT = 1000.0


class FakeSpoolman:
    """A Spoolman that keeps remaining_weight = initial_weight - used_weight."""

    def __init__(self, *, initial: float = LABEL_WEIGHT, used: float = 263.0):
        self.spool: dict = {
            "id": 42,
            "filament": {"id": 7, "name": "PLA Basic", "material": "PLA", "weight": LABEL_WEIGHT},
            "initial_weight": initial,
            "used_weight": used,
            "remaining_weight": initial - used,
            "extra": {},
        }
        self.log: list[str] = []

    def _recompute(self) -> None:
        # With no initial weight there is nothing to derive remaining from, and
        # real Spoolman leaves it null rather than inventing one.
        initial = self.spool.get("initial_weight")
        if initial is None:
            self.spool["remaining_weight"] = None
            return
        self.spool["remaining_weight"] = initial - self.spool["used_weight"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        self.log.append(f"{request.method} {path}")
        if path == "/spool/42":
            if request.method == "GET":
                return httpx.Response(200, json=self.spool)
            body = json.loads(request.content) if request.content else {}
            self.spool.update(body)
            # The rule the old fixture mocked away.
            self._recompute()
            return httpx.Response(200, json=self.spool)
        if path.startswith("/field/spool/"):
            return httpx.Response(200, json={"name": path.rsplit("/", 1)[-1]})
        raise AssertionError(f"unexpected request {request.method} {path}")


@pytest.fixture
def fake():
    return FakeSpoolman()


@pytest.fixture
def client(fake):
    c = SpoolmanClient("http://localhost:7912")
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handler),
        base_url="http://localhost:7912",
    )
    return c


@pytest.mark.asyncio
async def test_reset_leaves_the_measured_remaining_weight_alone(client, fake):
    """The reported symptom: a spool with 737 g left jumped back to 1000 g."""
    before = fake.spool["remaining_weight"]

    await client.reset_spool_consumed_counter(42)

    assert fake.spool["remaining_weight"] == before == 737.0


@pytest.mark.asyncio
async def test_reset_does_not_touch_any_native_spoolman_field(client, fake):
    """initial, remaining and used all survive — the point of using extra.

    The rejected alternative rewrote initial_weight to the current remaining,
    which repairs Bambuddy's display by overwriting a field the user owns:
    it ratchets down on every reset and Spoolman's own views then show the
    spool as full.
    """
    await client.reset_spool_consumed_counter(42)

    assert fake.spool["initial_weight"] == 1000.0
    assert fake.spool["used_weight"] == 263.0
    assert fake.spool["remaining_weight"] == 737.0


@pytest.mark.asyncio
async def test_reset_records_the_baseline_in_extra(client, fake):
    await client.reset_spool_consumed_counter(42)

    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps(263.0)


@pytest.mark.asyncio
async def test_reset_preserves_other_extra_keys(client, fake):
    """The tag lives in the same dict; a reset must not drop it."""
    fake.spool["extra"] = {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'}

    await client.reset_spool_consumed_counter(42)

    assert fake.spool["extra"]["tag"] == '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'
    assert "bambu_weight_used_baseline" in fake.spool["extra"]


@pytest.mark.asyncio
async def test_displayed_consumed_reads_zero_while_remaining_holds(client, fake):
    """End to end through the read mapping, which is what the Inventory page uses."""
    await client.reset_spool_consumed_counter(42)

    mapped = _map_spoolman_spool(fake.spool)

    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0, "consumed reads 0"
    assert LABEL_WEIGHT - mapped["weight_used"] == 737.0, "remaining still 737 g"


@pytest.mark.asyncio
async def test_consumption_after_a_reset_counts_from_the_baseline(client, fake):
    """A reset is a baseline, not an erasure: the next print's grams show up."""
    await client.reset_spool_consumed_counter(42)
    # 50 g printed afterwards, recorded the way Spoolman's /use endpoint does.
    fake.spool["used_weight"] = 313.0
    fake.spool["remaining_weight"] = 687.0

    mapped = _map_spoolman_spool(fake.spool)

    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 50.0
    assert LABEL_WEIGHT - mapped["weight_used"] == 687.0


@pytest.mark.asyncio
async def test_reset_survives_a_spool_with_no_remaining_weight(client, fake):
    """Legacy spools, and spools with a filament linked but never primed, carry
    remaining_weight = None. The mapping documents that case; the reset used to
    be handed one and PATCH straight through it.
    """
    fake.spool["remaining_weight"] = None
    fake.spool["initial_weight"] = None

    await client.reset_spool_consumed_counter(42)
    mapped = _map_spoolman_spool({**fake.spool, "remaining_weight": None})

    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps(263.0)
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0


@pytest.mark.asyncio
async def test_second_reset_moves_the_baseline_forward(client, fake):
    """Idempotent in the sense that matters: resetting twice does not compound."""
    await client.reset_spool_consumed_counter(42)
    fake.spool["used_weight"] = 313.0
    fake.spool["remaining_weight"] = 687.0

    await client.reset_spool_consumed_counter(42)

    mapped = _map_spoolman_spool(fake.spool)
    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps(313.0)
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0
    assert LABEL_WEIGHT - mapped["weight_used"] == 687.0, "still not full"
