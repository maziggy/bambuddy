"""One fault, one sentence, on every surface that reports it (#2926).

The catalogue in ``services/hms_errors.py`` has always held the text, and the
status response never carried it, so each client resolved the same codes from
its own copy of the same table. The description is now resolved once, at parse
time, and passed through by all three serializers of an ``HMSError``: the
status response, the WebSocket broadcast, and the print-completion payload the
queue's failure reason is built from. These tests pin that they agree — the
point of resolving it in one place is that they cannot drift apart.
"""

import pytest

from backend.app.main import _format_hms_error_summary
from backend.app.schemas.printer import HMSErrorResponse
from backend.app.services.bambu_mqtt import HMSError, PrinterState
from backend.app.services.printer_manager import printer_state_to_dict

RUNOUT_SENTENCE = "Filament ran out. Please load new filament."


def _runout() -> HMSError:
    """A `print_error` fault the catalogue covers, as the parser builds it."""
    return HMSError(
        code="0x8004",
        attr=0x03008004,
        module=3,
        severity=3,
        full_code="03008004",
        description=RUNOUT_SENTENCE,
    )


def _uncatalogued() -> HMSError:
    """An `hms[]` fault the catalogue cannot describe — a real P2S code (#2728).
    Its G1_G4 collapse is "0500_000A", which is not a key either."""
    return HMSError(
        code="0x3000a",
        attr=0x05000200,
        module=5,
        severity=2,
        full_code="050002000003000A",
        description=None,
    )


class TestStatusResponse:
    def test_carries_the_description(self):
        """What the route's mapper produces — the field a third-party client
        needs so it does not have to ship the catalogue itself."""
        e = _runout()
        assert (
            HMSErrorResponse(
                code=e.code,
                attr=e.attr,
                module=e.module,
                severity=e.severity,
                actions=e.actions,
                job_id=e.job_id,
                full_code=e.full_code,
                description=e.description,
            ).description
            == RUNOUT_SENTENCE
        )

    def test_defaults_to_none_when_not_supplied(self):
        """A producer that never sets it still validates, so the field cannot
        break an existing construction path."""
        assert HMSErrorResponse(code="0x8004", attr=0, module=3, severity=3).description is None

    def test_serializes_as_null_rather_than_being_dropped(self):
        """A client distinguishing "no text" from "field absent" needs the key
        present. Pydantic includes None by default; pin it so a later
        `exclude_none` does not silently change the contract."""
        payload = HMSErrorResponse(code="0x3000a", attr=0, module=5, severity=2).model_dump()
        assert "description" in payload
        assert payload["description"] is None


class TestWebSocketBroadcast:
    def test_carries_the_description(self):
        """The broadcast is a separate hand-rolled serializer; a relay watching
        the stream should not have to poll REST to find out what a fault means."""
        state = PrinterState()
        state.hms_errors = [_runout()]
        assert printer_state_to_dict(state, printer_id=1)["hms_errors"][0]["description"] == RUNOUT_SENTENCE

    def test_passes_none_through_for_an_uncatalogued_fault(self):
        """The fault is still broadcast — only the text is missing."""
        state = PrinterState()
        state.hms_errors = [_uncatalogued()]
        entry = printer_state_to_dict(state, printer_id=1)["hms_errors"][0]
        assert entry["full_code"] == "050002000003000A"
        assert entry["description"] is None


class TestQueueFailureReason:
    def test_prefers_the_resolved_description(self):
        """Deliberately a sentence the local fallback would NOT produce, so the
        preference is observable rather than coincidentally identical."""
        supplied = "Filament ran out, as resolved at parse time."
        assert _format_hms_error_summary([{"code": "0x8004", "attr": 0x03008004, "description": supplied}]) == (
            f"[0300_8004] {supplied}"
        )

    def test_falls_back_for_an_entry_without_the_field(self):
        """Entries predating the field still resolve, so the helper's own
        contract is unchanged for any other caller."""
        assert _format_hms_error_summary([{"code": "0x8004", "attr": 0x03008004}]) == (f"[0300_8004] {RUNOUT_SENTENCE}")

    def test_bare_short_code_when_nothing_describes_it(self):
        assert _format_hms_error_summary([{"code": "0x9999", "attr": 0x99990000, "description": None}]) == "[9999_9999]"


class TestSurfacesAgree:
    @pytest.mark.parametrize("fault,expected", [(_runout(), RUNOUT_SENTENCE), (_uncatalogued(), None)])
    def test_the_same_fault_reads_the_same_everywhere(self, fault, expected):
        """The reason to resolve once rather than at each boundary: these three
        cannot report different text for one fault."""
        state = PrinterState()
        state.hms_errors = [fault]
        broadcast = printer_state_to_dict(state, printer_id=1)["hms_errors"][0]["description"]
        rest = HMSErrorResponse(
            code=fault.code,
            attr=fault.attr,
            module=fault.module,
            severity=fault.severity,
            full_code=fault.full_code,
            description=fault.description,
        ).description
        assert broadcast == expected
        assert rest == expected
        assert fault.description == expected
