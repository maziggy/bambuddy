"""The shared drying preflight's blocked-reason rules (#2638).

An AMS can report several ``dry_sf_reason`` codes at once, and the two callers
render that differently: the immediate endpoint raises the message, the
scheduler stores a token the frontend translates. Both pick the code through
``primary_reason_code`` so one blocked AMS is not described two ways.
"""

import pytest

from backend.app.services import drying_preflight as preflight

pytestmark = pytest.mark.unit


class TestPrimaryReasonCode:
    def test_a_single_code_is_returned_as_is(self):
        assert preflight.primary_reason_code([2]) == 2

    @pytest.mark.parametrize("power_code", sorted(preflight.POWER_REASON_CODES))
    def test_power_outranks_a_transient_code(self, power_code):
        """ "AMS is busy" clears by itself; "plug the PSU in" does not, and the
        user can only act on one of them."""
        assert preflight.primary_reason_code([2, power_code]) == power_code
        assert preflight.primary_reason_code([power_code, 2]) == power_code

    def test_power_outranks_retract(self):
        assert preflight.primary_reason_code([preflight.RETRACT_REASON_CODE, 1]) == 1

    def test_retract_outranks_a_transient_code(self):
        assert preflight.primary_reason_code([0, preflight.RETRACT_REASON_CODE]) == preflight.RETRACT_REASON_CODE

    def test_transient_codes_keep_the_reported_order(self):
        assert preflight.primary_reason_code([6, 2, 4]) == 6

    def test_no_codes_is_no_answer(self):
        assert preflight.primary_reason_code([]) is None


class TestWaitingReason:
    def test_power(self):
        assert preflight.waiting_reason_for_codes([2, 8]) == preflight.WAITING_REASON_POWER

    def test_retract(self):
        assert preflight.waiting_reason_for_codes([2, 3]) == preflight.WAITING_REASON_RETRACT

    def test_transient_falls_through_to_the_generic_token(self):
        assert preflight.waiting_reason_for_codes([2]) == preflight.WAITING_REASON_BLOCKED

    def test_no_codes_is_not_a_specific_reason(self):
        """Callers only ask when something is blocking, but answering with a
        power alert for an empty list would be worse than saying nothing
        specific."""
        assert preflight.waiting_reason_for_codes([]) == preflight.WAITING_REASON_BLOCKED


class TestBothPathsAgree:
    """The endpoint quotes a message and the scheduler stores a token. Whatever
    they pick, it has to be the same code underneath."""

    @pytest.mark.parametrize(
        "codes,expected_token",
        [
            ([2, 1], preflight.WAITING_REASON_POWER),
            ([2, 8], preflight.WAITING_REASON_POWER),
            ([0, 3], preflight.WAITING_REASON_RETRACT),
            ([6, 2], preflight.WAITING_REASON_BLOCKED),
        ],
    )
    def test_the_message_and_the_token_describe_one_code(self, codes, expected_token):
        code = preflight.primary_reason_code(codes)

        # What the immediate endpoint raises.
        assert code in preflight.DRY_SF_REASON_MESSAGES
        # What the scheduled row records, for the same code.
        assert preflight.waiting_reason_for_codes(codes) == expected_token


class TestBlockingReasonCodes:
    def test_unknown_and_malformed_codes_are_dropped(self):
        """A firmware that grows a code we have no message for must not block a
        run with an unexplainable reason."""
        assert preflight.blocking_reason_codes({"dry_sf_reason": [2, 99, "x", None]}) == [2]

    def test_a_missing_unit_is_not_blocking(self):
        assert preflight.blocking_reason_codes(None) == []
