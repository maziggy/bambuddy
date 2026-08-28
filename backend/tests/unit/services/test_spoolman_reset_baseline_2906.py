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
    """A Spoolman that applies the three rules a real one applies.

    1. ``remaining_weight`` is derived, not stored: initial minus used.
    2. An extra key must be registered before it can be written, and an
       unregistered one is a 404 on GET so the caller creates it. A field's
       type cannot be changed afterwards.
    3. Extra values are validated against the registered type. The default
       type is ``text``, which requires the stored JSON to decode to a str.

    Rule 1 is why the reset stopped PATCHing ``used_weight``. Rules 2 and 3
    are why the first version of that fix did not land either: it wrote the
    baseline as a JSON number, which a ``text`` field rejects, and the fake
    answered every ``/field/spool/`` GET with 200 so neither rule ever ran.
    A fake that models the rule that bit last time and not the one biting now
    is the same failure as the fixture these tests were written to correct.
    """

    def __init__(self, *, initial: float = LABEL_WEIGHT, used: float = 263.0):
        self.spool: dict = {
            "id": 42,
            "filament": {"id": 7, "name": "PLA Basic", "material": "PLA", "weight": LABEL_WEIGHT},
            "initial_weight": initial,
            "used_weight": used,
            "remaining_weight": initial - used,
            "extra": {},
        }
        # What a real instance looks like before this feature runs: the keys
        # Bambuddy already writes are registered, all of them text, and
        # bambu_weight_used_baseline is not registered at all.
        self.fields: dict[str, str] = {
            "tag": "text",
            "bambu_color_name": "text",
            "bambu_slicer_filament_id": "text",
            "bambu_slicer_setting_id": "text",
        }
        self.log: list[str] = []

    def _validate_extra(self, extra: dict) -> str | None:
        """Return Spoolman's error message, or None if the dict is acceptable."""
        for name, raw in extra.items():
            field_type = self.fields.get(name)
            if field_type is None:
                return f"Unknown extra field {name}."
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                return f"Value for {name} is not valid JSON."
            if field_type == "text" and not isinstance(decoded, str):
                return "Value is not a string."
        return None

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
            if "extra" in body:
                error = self._validate_extra(body["extra"])
                if error is not None:
                    return httpx.Response(400, json={"message": error})
            self.spool.update(body)
            # The rule the old fixture mocked away.
            self._recompute()
            return httpx.Response(200, json=self.spool)
        if path.startswith("/field/spool/"):
            name = path.rsplit("/", 1)[-1]
            if request.method == "GET":
                if name not in self.fields:
                    return httpx.Response(404, json={"message": f"No field {name}."})
                return httpx.Response(200, json={"name": name, "field_type": self.fields[name]})
            if request.method == "POST":
                field_type = (json.loads(request.content) if request.content else {}).get("field_type", "text")
                existing = self.fields.get(name)
                if existing is not None and existing != field_type:
                    # The reason the storage type has to be right the first
                    # time: no later release can repair an install that
                    # registered the key with the wrong type.
                    return httpx.Response(400, json={"message": "Field type cannot be changed."})
                self.fields[name] = field_type
                return httpx.Response(201, json={"name": name, "field_type": field_type})
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

    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps("263.0")


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

    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps("263.0")
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0


@pytest.mark.asyncio
async def test_second_reset_moves_the_baseline_forward(client, fake):
    """Idempotent in the sense that matters: resetting twice does not compound."""
    await client.reset_spool_consumed_counter(42)
    fake.spool["used_weight"] = 313.0
    fake.spool["remaining_weight"] = 687.0

    await client.reset_spool_consumed_counter(42)

    mapped = _map_spoolman_spool(fake.spool)
    assert fake.spool["extra"]["bambu_weight_used_baseline"] == json.dumps("313.0")
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0
    assert LABEL_WEIGHT - mapped["weight_used"] == 687.0, "still not full"


@pytest.mark.asyncio
async def test_the_baseline_is_stored_in_the_form_a_text_field_accepts(client, fake):
    """The blocking defect in the first version of this fix.

    Spoolman registers an unseen extra key as ``text`` and then requires the
    value to decode to a str, so ``json.dumps(263.0)`` -- the JSON number
    ``263.0`` -- is rejected with "Value is not a string." and the PATCH 400s.
    Pinning the string form is what makes the write land at all.
    """
    await client.reset_spool_consumed_counter(42)

    stored = fake.spool["extra"]["bambu_weight_used_baseline"]
    assert json.loads(stored) == "263.0", "stored as a JSON string, not a JSON number"
    assert fake._validate_extra({"bambu_weight_used_baseline": stored}) is None


@pytest.mark.asyncio
async def test_the_baseline_key_is_registered_before_it_is_written(client, fake):
    """It is not registered on an existing install, so the reset has to create it."""
    assert "bambu_weight_used_baseline" not in fake.fields

    await client.reset_spool_consumed_counter(42)

    assert fake.fields["bambu_weight_used_baseline"] == "text"
    assert "GET /field/spool/bambu_weight_used_baseline" in fake.log
    assert "POST /field/spool/bambu_weight_used_baseline" in fake.log


@pytest.mark.asyncio
async def test_a_baseline_written_as_text_reads_back_as_a_number(client, fake):
    """The other half: the read side has to decode what the write side stores.

    ``_extract_extra_float`` used to require the decoded value to be a number,
    so the string form it now has to write would have read back as None and the
    baseline would have been silently ignored.
    """
    await client.reset_spool_consumed_counter(42)

    mapped = _map_spoolman_spool(fake.spool)

    assert mapped["weight_used_baseline"] == 263.0
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 0.0


def test_a_negative_stored_baseline_cannot_inflate_the_counter():
    """The write side clamps to >= 0 and the read side has to agree.

    It only shows on a spool whose remaining_weight and used_weight disagree --
    a hand-edited remaining, or a re-weigh -- because that is when the baseline
    is a non-zero correction rather than a cancelling pair. Here Spoolman says
    263 g used while remaining says 300 g has gone; the baseline carries the
    37 g difference. A stored -100 drags the sum under zero, the outer max()
    floors it at 0, and the 37 g correction is lost: consumed jumps to 300.
    Clamping ``stored`` itself keeps the correction intact.
    """
    spool = {
        "id": 42,
        "filament": {"id": 7, "name": "PLA Basic", "material": "PLA", "weight": LABEL_WEIGHT},
        "initial_weight": LABEL_WEIGHT,
        "used_weight": 263.0,
        "remaining_weight": 700.0,
        "extra": {"bambu_weight_used_baseline": json.dumps("-100.0")},
    }

    mapped = _map_spoolman_spool(spool)

    assert mapped["weight_used"] == 300.0
    assert mapped["weight_used_baseline"] == 37.0, "the negative is discarded, not subtracted"
    assert mapped["weight_used"] - mapped["weight_used_baseline"] == 263.0, "not inflated to 300"


def test_a_missing_baseline_and_a_zero_one_are_not_the_same_thing():
    """``or 0.0`` collapsed them, which is the distinction _extract_extra_float
    exists to preserve. Both map to a zero baseline, but by different routes and
    the helper has to keep answering None for the absent one."""
    from backend.app.api.routes._spoolman_helpers import (
        BAMBU_WEIGHT_USED_BASELINE_KEY,
        _extract_extra_float,
    )

    assert _extract_extra_float({}, BAMBU_WEIGHT_USED_BASELINE_KEY) is None
    assert (
        _extract_extra_float({BAMBU_WEIGHT_USED_BASELINE_KEY: json.dumps("0.0")}, BAMBU_WEIGHT_USED_BASELINE_KEY) == 0.0
    )
    # Both spellings have to read alike, so an install that already stored the
    # number form before this fix keeps working.
    assert (
        _extract_extra_float({BAMBU_WEIGHT_USED_BASELINE_KEY: json.dumps(263.0)}, BAMBU_WEIGHT_USED_BASELINE_KEY)
        == 263.0
    )
