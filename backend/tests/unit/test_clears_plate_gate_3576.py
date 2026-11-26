"""finish_needs_plate_confirmation with a job that clears its own plate (#3576)."""

import itertools
from unittest.mock import patch

import pytest

from backend.app.services import plate_clear
from backend.app.services.print_scheduler import PrintScheduler, _plate_clear_holds, finish_needs_plate_confirmation

# The printer's own "Require plate-clear confirmation" (#2860): None follows the app setting.
OVERRIDES = [None, True, False]


@pytest.fixture(autouse=True)
def overrides():
    plate_clear.load_overrides({})
    yield plate_clear
    plate_clear.load_overrides({})


class TestTruthTable:
    @pytest.mark.parametrize(
        ("override", "require", "clears", "expected"),
        [
            # Only a flagged job on a printer that follows the setting, with the setting on, is lifted.
            (None, True, True, False),
            # The setting off: nothing to ask for either way.
            (None, False, True, False),
            (None, False, False, False),
            # No flag: follows the setting, as before.
            (None, True, False, True),
            # A printer set to always require it wins over the flag.
            (True, True, True, True),
            (True, False, True, True),
            (True, True, False, True),
            (True, False, False, True),
            # A printer set to never require it waits for nothing.
            (False, True, True, False),
            (False, False, True, False),
            (False, True, False, False),
            (False, False, False, False),
        ],
    )
    def test_table(self, override, require, clears, expected):
        assert finish_needs_plate_confirmation(override, require, job_clears_plate=clears) is expected

    def test_default_keeps_the_old_behaviour(self):
        for override, require in itertools.product(OVERRIDES, [True, False]):
            assert finish_needs_plate_confirmation(override, require) == finish_needs_plate_confirmation(
                override, require, job_clears_plate=False
            )

    def test_the_flag_is_keyword_only(self):
        with pytest.raises(TypeError):
            finish_needs_plate_confirmation(None, True, True)  # type: ignore[misc]

    @pytest.mark.parametrize("override", OVERRIDES)
    @pytest.mark.parametrize("require", [True, False])
    def test_the_flag_never_adds_a_wait(self, override, require):
        """A flagged job waits at most as often as an unflagged one."""
        flagged = finish_needs_plate_confirmation(override, require, job_clears_plate=True)
        plain = finish_needs_plate_confirmation(override, require)
        assert not (flagged and not plain)

    def test_returns_a_real_bool(self):
        assert finish_needs_plate_confirmation(None, True) is True
        assert finish_needs_plate_confirmation(None, False, job_clears_plate=True) is False


class TestOverrideAccessor:
    """plate_clear.override hands out the printer's own setting, raw."""

    def test_no_override_reads_as_none(self):
        assert plate_clear.override(1) is None

    @pytest.mark.parametrize("value", [True, False])
    def test_an_override_reads_back(self, overrides, value):
        overrides.set_override(1, value)
        assert plate_clear.override(1) is value
        assert plate_clear.override(2) is None

    def test_clearing_an_override_reads_as_none_again(self, overrides):
        overrides.set_override(1, False)
        overrides.set_override(1, None)
        assert plate_clear.override(1) is None


class TestPlateGateUnaffected:
    """The scheduler's gate reads the printer's state, not the job's flag."""

    @pytest.mark.parametrize("override", OVERRIDES)
    @pytest.mark.parametrize("require", [True, False])
    def test_without_the_flag_the_helper_is_the_gate_of_2860(self, overrides, override, require):
        overrides.set_override(1, override)
        assert plate_clear.applies(1, require) is finish_needs_plate_confirmation(plate_clear.override(1), require)

    @pytest.mark.parametrize("override", OVERRIDES)
    @pytest.mark.parametrize("require", [True, False])
    @pytest.mark.parametrize("awaiting", [True, False])
    def test_scheduler_hold_equals_the_helper_without_the_flag(self, overrides, override, require, awaiting):
        overrides.set_override(1, override)
        with patch("backend.app.services.print_scheduler.printer_manager") as pm:
            pm.is_awaiting_plate_clear.return_value = awaiting
            assert _plate_clear_holds(1, require) is (awaiting and finish_needs_plate_confirmation(override, require))

    def test_gate_has_no_job_parameter(self):
        with pytest.raises(TypeError):
            _plate_clear_holds(1, True, job_clears_plate=True)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            PrintScheduler()._is_printer_idle(1, True, job_clears_plate=True)  # type: ignore[call-arg]
