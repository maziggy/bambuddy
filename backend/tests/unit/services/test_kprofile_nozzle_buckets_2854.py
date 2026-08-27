"""The slot-card K value survives a query for a nozzle the printer lacks.

H2-series AMS trays carry no ``k`` field -- verified against a live H2 wire
capture, where every tray reports ``cali_idx`` and nothing else -- so the value
PR #2854 put on the slot card is resolved from the printer's calibration table
in ``state.kprofiles``.

That table is answered per nozzle diameter, and the printer answers whoever
asks: BambuStudio's queries land on the same report topic Bambuddy subscribes
to. Assigning each response straight to ``state.kprofiles`` let one answer
stand for the whole printer. Measured on the maintainer's H2 on 2026-08-25:
the nightly GitHub backup probes 0.2/0.4/0.6/0.8 in turn, the 0.8 probe found
no profiles on a 0.4+0.6 machine, and every K value on the card went blank
until something refilled the list.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, KProfile, NozzleInfo, PrinterState
from backend.app.utils.kprofile_lookup import build_slot_k_resolver


def _client() -> BambuMQTTClient:
    """A client with no transport -- only the response handling is under test."""
    return BambuMQTTClient(ip_address="10.0.0.1", serial_number="TESTSERIAL0000", access_code="00000000")


def _response(nozzle: str, *entries: tuple[int, str]) -> dict:
    """One ``extrusion_cali_get`` payload, as the printer sends it.

    The envelope carries the nozzle diameter; the per-filament entries do not.
    """
    return {
        "command": "extrusion_cali_get",
        "nozzle_diameter": nozzle,
        "filaments": [
            {
                "cali_idx": cali_idx,
                "extruder_id": 0,
                "filament_id": "GFL99",
                "k_value": k_value,
                "name": f"Profile {cali_idx}",
                "setting_id": "GFSL99",
            }
            for cali_idx, k_value in entries
        ],
    }


class TestNozzleBuckets:
    def test_an_empty_table_clears_only_its_own_nozzle(self):
        """The exact backup sequence that emptied the maintainer's card.

        0.2 and 0.8 come back empty on a 0.4+0.6 machine. Neither may take the
        other two nozzles' profiles with it.
        """
        client = _client()
        client._handle_kprofile_response(_response("0.2"))
        client._handle_kprofile_response(_response("0.4", (3, "0.020000")))
        client._handle_kprofile_response(_response("0.6", (3, "0.018000")))
        client._handle_kprofile_response(_response("0.8"))

        by_nozzle = {kp.nozzle_diameter: kp.k_value for kp in client.state.kprofiles}
        assert by_nozzle == {"0.4": "0.020000", "0.6": "0.018000"}

    def test_a_fresh_table_replaces_its_own_nozzle_wholesale(self):
        """A re-read is authoritative for its nozzle: deletions must stick."""
        client = _client()
        client._handle_kprofile_response(_response("0.4", (3, "0.020000"), (4, "0.021000")))
        client._handle_kprofile_response(_response("0.4", (3, "0.019000")))

        assert [(kp.slot_id, kp.k_value) for kp in client.state.kprofiles] == [(3, "0.019000")]

    def test_a_response_for_one_nozzle_leaves_the_others_alone(self):
        """The BambuStudio case: someone else asks about a nozzle we didn't."""
        client = _client()
        client._handle_kprofile_response(_response("0.4", (3, "0.020000")))
        client._handle_kprofile_response(_response("0.6", (3, "0.018000")))

        assert len(client.state.kprofiles) == 2

    def test_an_unattributable_answer_is_not_allowed_to_empty_the_table(self):
        """No envelope diameter and no entries names no bucket. Keep what we have."""
        client = _client()
        client._handle_kprofile_response(_response("0.4", (3, "0.020000")))
        client._handle_kprofile_response({"command": "extrusion_cali_get", "filaments": []})

        assert len(client.state.kprofiles) == 1

    def test_entries_name_their_own_bucket_when_the_envelope_does_not(self):
        """Firmware that omits the envelope diameter still has to be filed."""
        client = _client()
        client._handle_kprofile_response(_response("0.4", (3, "0.020000")))
        client._handle_kprofile_response(
            {
                "command": "extrusion_cali_get",
                "filaments": [
                    {"cali_idx": 3, "extruder_id": 0, "k_value": "0.017000", "nozzle_diameter": "0.6"},
                ],
            }
        )

        by_nozzle = {kp.nozzle_diameter: kp.k_value for kp in client.state.kprofiles}
        assert by_nozzle == {"0.4": "0.020000", "0.6": "0.017000"}

    def test_a_pending_request_still_refuses_another_nozzles_answer(self):
        """#1748's guard is unchanged: don't wake a waiter with the wrong table."""
        client = _client()
        client._pending_kprofile_requests["7"] = {"nozzle": "0.4", "event": MagicMock(), "profiles": None}
        client._handle_kprofile_response(_response("0.6", (3, "0.018000")))

        assert client.state.kprofiles == []


def _state(profiles, *, nozzles=("0.4",), ams_extruder_map=None):
    return SimpleNamespace(
        kprofiles=list(profiles),
        nozzles=[SimpleNamespace(nozzle_diameter=d) for d in nozzles],
        ams_extruder_map=ams_extruder_map,
        ams_switch_inlet=None,
    )


def _profile(cali_idx: int, k_value: str, nozzle: str, extruder: int = 0) -> KProfile:
    return KProfile(
        slot_id=cali_idx,
        extruder_id=extruder,
        nozzle_id="",
        nozzle_diameter=nozzle,
        filament_id="GFL99",
        name=f"Profile {cali_idx}",
        k_value=k_value,
        n_coef="1.000000",
        ams_id=0,
        tray_id=-1,
    )


class TestSlotKResolver:
    def test_a_slot_reads_the_profile_on_its_own_extruder(self):
        """The H2C case from #2854: one spool, 0.018 left and 0.020 right.

        AMS 0 feeds extruder 1, AMS 1 feeds extruder 0, and calibration index 3
        exists on both.
        """
        resolve = build_slot_k_resolver(
            _state(
                [_profile(3, "0.020000", "0.4", extruder=0), _profile(3, "0.018000", "0.6", extruder=1)],
                nozzles=("0.4", "0.6"),
                ams_extruder_map={"0": 1, "1": 0},
            )
        )

        assert resolve(3, 0, 0) == pytest.approx(0.018)
        assert resolve(3, 1, 0) == pytest.approx(0.020)

    def test_a_swapped_out_nozzles_stale_table_loses_to_the_installed_one(self):
        """One extruder, two diameters: only one of them is fitted right now."""
        resolve = build_slot_k_resolver(
            _state([_profile(3, "0.020000", "0.4"), _profile(3, "0.017000", "0.6")], nozzles=("0.6",))
        )

        assert resolve(3, 0, 0) == pytest.approx(0.017)

    def test_an_index_that_two_installed_nozzles_both_claim_reads_as_unknown(self):
        """Blank beats confidently printing the other nozzle's number."""
        resolve = build_slot_k_resolver(
            _state([_profile(3, "0.020000", "0.4"), _profile(3, "0.017000", "0.6")], nozzles=("0.4", "0.6"))
        )

        assert resolve(3, 0, 0) is None

    def test_a_single_nozzle_printer_resolves_without_an_extruder_map(self):
        resolve = build_slot_k_resolver(_state([_profile(3, "0.020000", "0.4")]))

        assert resolve(3, 0, 2) == pytest.approx(0.020)

    def test_an_uncalibrated_slot_has_no_value(self):
        resolve = build_slot_k_resolver(_state([_profile(3, "0.020000", "0.4")]))

        assert resolve(None, 0, 0) is None
        assert resolve(-1, 0, 0) is None

    def test_an_unparseable_k_value_is_skipped_rather_than_raising(self):
        resolve = build_slot_k_resolver(_state([_profile(3, "not-a-number", "0.4")]))

        assert resolve(3, 0, 0) is None


class TestPrimeKProfileTable:
    """Nothing used to read the calibration table on connect.

    ``state.kprofiles`` was filled only when someone opened the Profiles page
    or Configure Slot, when a GitHub backup ran, or when the printer answered
    a query BambuStudio made on the report topic Bambuddy shares. On the
    printers whose trays carry no ``k``, a Bambuddy nobody had visited showed
    an AMS card with no K values at all.
    """

    def _printer_state(self, *, nozzles, connected=True):
        return SimpleNamespace(
            connected=connected,
            nozzles=[SimpleNamespace(nozzle_diameter=d) for d in nozzles],
        )

    async def _prime(self, printer_state, client):
        from backend.app import main as main_module

        with (
            patch.object(main_module.printer_manager, "get_client", return_value=client),
            patch.object(main_module.printer_manager, "get_status", return_value=printer_state),
        ):
            return await main_module.prime_kprofile_table(7)

    @pytest.mark.asyncio
    async def test_it_asks_for_every_fitted_nozzle(self):
        """A dual-nozzle H2 needs both tables: cali_idx is numbered per nozzle."""
        client = MagicMock()
        client.get_kprofiles = AsyncMock(return_value=[])

        primed = await self._prime(self._printer_state(nozzles=("0.4", "0.6")), client)

        assert primed == 2
        assert [call.kwargs["nozzle_diameter"] for call in client.get_kprofiles.await_args_list] == ["0.4", "0.6"]

    @pytest.mark.asyncio
    async def test_two_identical_nozzles_are_asked_for_once(self):
        client = MagicMock()
        client.get_kprofiles = AsyncMock(return_value=[])

        primed = await self._prime(self._printer_state(nozzles=("0.4", "0.4")), client)

        assert primed == 1

    @pytest.mark.asyncio
    async def test_it_never_probes_sizes_the_printer_does_not_have(self):
        """Blind 0.2/0.4/0.6/0.8 probing is what the backup does, and it is
        exactly what used to blank the table."""
        client = MagicMock()
        client.get_kprofiles = AsyncMock(return_value=[])

        await self._prime(self._printer_state(nozzles=("0.6",)), client)

        assert [call.kwargs["nozzle_diameter"] for call in client.get_kprofiles.await_args_list] == ["0.6"]

    @pytest.mark.asyncio
    async def test_no_reported_nozzle_yet_asks_nothing(self):
        client = MagicMock()
        client.get_kprofiles = AsyncMock(return_value=[])

        primed = await self._prime(self._printer_state(nozzles=("",)), client)

        assert primed == 0
        client.get_kprofiles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_disconnected_printer_is_left_alone(self):
        client = MagicMock()
        client.get_kprofiles = AsyncMock(return_value=[])

        primed = await self._prime(self._printer_state(nozzles=("0.4",), connected=False), client)

        assert primed == 0
        client.get_kprofiles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_nozzle_failing_does_not_cost_the_other_its_table(self):
        """This runs on the back of a connection; it may not raise into it."""
        client = MagicMock()
        client.get_kprofiles = AsyncMock(side_effect=[TimeoutError("no answer"), []])

        primed = await self._prime(self._printer_state(nozzles=("0.4", "0.6")), client)

        assert primed == 1


class TestPrimeOnConnectEdge:
    """The connection's one priming attempt must not be spent too early."""

    def _state(self, *, connected=True, state="IDLE", nozzles=("0.4",)):
        """A real PrinterState: the handler reads far more of it than this
        test cares about, and a stub would only pin the fields I remembered."""
        printer_state = PrinterState()
        printer_state.connected = connected
        printer_state.state = state
        printer_state.nozzles = [NozzleInfo(nozzle_diameter=d) for d in nozzles]
        return printer_state

    async def _edge(self, printer_state, main_module):
        with (
            patch.object(main_module, "spawn_background_task", side_effect=lambda coro, **kw: coro.close()) as spawn,
            patch.object(main_module.ws_manager, "send_printer_status", new=AsyncMock()),
            patch.object(main_module, "printer_state_to_dict", return_value={}),
            patch.object(main_module.printer_manager, "get_model", return_value="H2D"),
            patch.object(main_module.printer_manager, "get_drying_targets", return_value={}),
        ):
            await main_module.on_printer_status_change(31, printer_state)
        return [call.kwargs.get("name", "") for call in spawn.call_args_list]

    @pytest.fixture(autouse=True)
    def _clean_latches(self):
        from backend.app import main as main_module

        main_module._printer_kprofiles_primed_since_connect.pop(31, None)
        main_module._printer_reconciled_since_connect.pop(31, None)
        yield
        main_module._printer_kprofiles_primed_since_connect.pop(31, None)
        main_module._printer_reconciled_since_connect.pop(31, None)

    @pytest.mark.asyncio
    async def test_a_connected_printer_with_a_known_nozzle_is_primed(self):
        from backend.app import main as main_module

        names = await self._edge(self._state(), main_module)

        assert any(name.startswith("prime-kprofiles") for name in names)

    @pytest.mark.asyncio
    async def test_it_is_primed_once_per_connection(self):
        from backend.app import main as main_module

        await self._edge(self._state(), main_module)
        names = await self._edge(self._state(), main_module)

        assert not any(name.startswith("prime-kprofiles") for name in names)

    @pytest.mark.asyncio
    async def test_a_state_that_names_no_nozzle_yet_does_not_spend_the_attempt(self):
        """The first push_status makes the state known but need not carry the
        nozzle fields. Latching there would leave the table unread all session."""
        from backend.app import main as main_module

        early = await self._edge(self._state(nozzles=("",)), main_module)
        assert not any(name.startswith("prime-kprofiles") for name in early)

        later = await self._edge(self._state(), main_module)
        assert any(name.startswith("prime-kprofiles") for name in later)

    @pytest.mark.asyncio
    async def test_a_reconnect_re_arms_it(self):
        from backend.app import main as main_module

        await self._edge(self._state(), main_module)
        await self._edge(self._state(connected=False), main_module)
        names = await self._edge(self._state(), main_module)

        assert any(name.startswith("prime-kprofiles") for name in names)
