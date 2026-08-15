"""Which prints are worth an FTPS sweep, and which are not (#2780).

The gate this module guards is one-sided on purpose, and both sides matter:

* Missing it costs ~110 doomed FTP connections per print and an archive card
  that is blank with no stated reason -- the reported bug.
* Over-applying it costs archives that work today. A printer that never
  publishes ``sdcard`` and never had a ``project_file`` reach us must sweep
  exactly as before, or the fix is a regression for everyone else.

So the tests below spend most of their weight on the second failure mode.
"""

import pytest

from backend.app.services.print_storage import (
    REASON_INTERNAL_STORAGE,
    REASON_NO_EXTERNAL_STORAGE,
    external_storage_present,
    last_print_storage_verdict,
    print_file_reachable_over_ftp,
    url_is_external_storage,
)

pytestmark = pytest.mark.unit


class FakeState:
    """Stand-in for PrinterState with only the fields the helper reads."""

    def __init__(self, current_project_url=None, sdcard=False, sdcard_reported=False, last_project_url=None):
        self.current_project_url = current_project_url
        # Defaults to the per-print value: for every test that does not care
        # about the distinction, the two readings agree.
        self.last_project_url = current_project_url if last_project_url is None else last_project_url
        self.sdcard = sdcard
        self.sdcard_reported = sdcard_reported


class TestUrlScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://Benchy.gcode.3mf",
            # Real dispatches carry names with spaces and non-ASCII; the scheme
            # is all that is being read and none of that should disturb it.
            "ftp://Halterung Kühlschrank V2.gcode.3mf",
            "FTP://Benchy.gcode.3mf",
        ],
    )
    def test_ftp_means_external_storage(self, url):
        assert url_is_external_storage(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            # The scheme every H2C and P2S dispatch in #2780's bundle carried,
            # 35 out of 35.
            "brtc://emmc/169356_204314.STEP.gcode.3mf",
            "brtc://emmc/Benchy.gcode.3mf",
        ],
    )
    def test_brtc_means_internal_storage(self, url):
        assert url_is_external_storage(url) is False

    def test_an_unknown_scheme_is_not_assumed_reachable(self):
        """Matching the reachable value, not the unreachable one.

        If Bambu ships a third scheme, the safe reading is "somewhere we can't
        see", not "fine" -- an unrecognised scheme that read as reachable would
        put the storm straight back.
        """
        assert url_is_external_storage("sftp://Benchy.3mf") is False

    @pytest.mark.parametrize("url", [None, "", "Benchy.gcode.3mf"])
    def test_no_usable_url_declines_to_answer(self, url):
        """None is a third answer and must not collapse into False."""
        assert url_is_external_storage(url) is None

    @pytest.mark.parametrize("url", [12345, [], {}, object()])
    def test_a_non_string_url_declines_too(self, url):
        """The value arrives straight off the wire, so it is whatever the
        sender put there. Truth-testing alone would let a non-string fall
        through to the scheme comparison and read as internal storage --
        which is a silent skip of a sweep that should have run.
        """
        assert url_is_external_storage(url) is None


class TestSweepIsSkipped:
    def test_a_print_kept_on_internal_storage(self):
        verdict = print_file_reachable_over_ftp(FakeState(current_project_url="brtc://emmc/Benchy.gcode.3mf"))

        assert verdict.reachable is False
        assert verdict.reason == REASON_INTERNAL_STORAGE

    def test_a_printer_that_says_its_slot_is_empty(self):
        """#2780's H2C: `sdcard` False for three weeks, 800 clean FTPS
        connections, and a 550 on every single path it asked for."""
        verdict = print_file_reachable_over_ftp(FakeState(sdcard=False, sdcard_reported=True))

        assert verdict.reachable is False
        assert verdict.reason == REASON_NO_EXTERNAL_STORAGE


class TestSweepStillRuns:
    """The regression guard. Every case here worked before the gate existed."""

    def test_a_print_on_external_storage(self):
        assert print_file_reachable_over_ftp(FakeState(current_project_url="ftp://Benchy.gcode.3mf")).reachable

    def test_a_printer_that_never_mentioned_its_card(self):
        """Silence is not evidence.

        `sdcard` defaults to False, so a printer whose firmware simply never
        publishes the field looks identical to an empty slot unless the
        "did it ever say so" flag is honoured. Reading the default as an
        answer would skip the sweep for every one of them.
        """
        assert print_file_reachable_over_ftp(FakeState(sdcard=False, sdcard_reported=False)).reachable

    def test_a_printer_with_a_card_and_no_dispatch_seen(self):
        """Some brokers refuse the request-topic subscription, so no URL ever
        arrives. That install must behave exactly as it did before."""
        assert print_file_reachable_over_ftp(FakeState(sdcard=True, sdcard_reported=True)).reachable

    def test_an_explicit_ftp_url_outranks_a_disagreeing_card_flag(self):
        """A false skip is a regression; a needless sweep is only slow.

        When the dispatcher says the file went to external storage, believe
        the specific claim over the general one.
        """
        state = FakeState(current_project_url="ftp://Benchy.gcode.3mf", sdcard=False, sdcard_reported=True)

        assert print_file_reachable_over_ftp(state).reachable

    def test_no_state_at_all(self):
        """Printer not connected, or status not yet populated."""
        assert print_file_reachable_over_ftp(None).reachable

    def test_a_state_missing_the_fields_entirely(self):
        """The helper is duck-typed, and a PrinterState from a pickled or
        partially-constructed source may predate these fields."""

        class Bare:
            pass

        assert print_file_reachable_over_ftp(Bare()).reachable


class TestReasonIsAlwaysPresentWhenUnreachable:
    @pytest.mark.parametrize(
        "state",
        [
            FakeState(current_project_url="brtc://emmc/x.3mf"),
            FakeState(sdcard=False, sdcard_reported=True),
        ],
    )
    def test_unreachable_carries_a_reason(self, state):
        """The reason crosses into the API and picks the banner text. An
        unreachable verdict without one would render the generic advice --
        which is the wrong advice, and the whole point of the change."""
        verdict = print_file_reachable_over_ftp(state)

        assert verdict.reachable is False
        assert verdict.reason

    def test_reachable_carries_no_reason(self):
        assert print_file_reachable_over_ftp(FakeState(sdcard=True, sdcard_reported=True)).reason is None


class TestTheGateUsesThePerPrintUrlOnly:
    """A stale URL must never gate a sweep.

    ``current_project_url`` is cleared when a print ends; ``last_project_url``
    is sticky for reporting. The gate reads only the first, and that is
    load-bearing rather than tidiness: 18% of the print starts in #2780's
    support bundle (14 of 79) had no ``project_file`` on the request topic at
    all -- touchscreen reprints, restart recovery, anything Bambuddy did not
    see dispatched. If those inherited the previous job's destination, a
    printer that ran one Studio print to internal storage would skip the FTPS
    sweep for every subsequent screen-started print, losing archives that work
    today.

    The asymmetry is what makes it worth pinning: a stale ``ftp://`` costs
    only a pointless sweep, while a stale ``brtc://`` costs an archive.
    """

    def test_a_print_with_no_dispatch_of_its_own_still_sweeps(self):
        """The previous print went to internal storage; this one Bambuddy
        never saw dispatched. Unknown, so sweep."""
        state = FakeState(
            current_project_url=None,
            last_project_url="brtc://emmc/previous.gcode.3mf",
            sdcard=True,
            sdcard_reported=True,
        )

        assert print_file_reachable_over_ftp(state).reachable

    def test_the_sticky_reading_still_reports_it(self):
        """The diagnostic is normally run after the print that prompted it, so
        it needs the answer the gate has rightly forgotten."""
        state = FakeState(
            current_project_url=None,
            last_project_url="brtc://emmc/previous.gcode.3mf",
            sdcard=True,
            sdcard_reported=True,
        )

        verdict = last_print_storage_verdict(state)

        assert verdict.reachable is False
        assert verdict.reason == REASON_INTERNAL_STORAGE

    def test_an_empty_slot_is_reported_by_both(self):
        """Not URL-derived, so clearing the per-print value changes nothing."""
        state = FakeState(sdcard=False, sdcard_reported=True)

        assert print_file_reachable_over_ftp(state).reason == REASON_NO_EXTERNAL_STORAGE
        assert last_print_storage_verdict(state).reason == REASON_NO_EXTERNAL_STORAGE

    def test_the_sticky_reading_never_gates_a_sweep(self):
        """Guard against someone swapping the two back: if the gate ever reads
        the sticky field, the case above starts failing -- and so does this."""
        import inspect

        from backend.app.services import print_storage

        source = inspect.getsource(print_storage.print_file_reachable_over_ftp)
        assert "current_project_url" in source
        assert "last_project_url" not in source.split('"""')[-1]


class TestTimelapseUsesTheNarrowerRule:
    """The printer writes its timelapse to the card itself.

    Where the *sliced file* went says nothing about whether a video exists, so
    gating the timelapse scan on the URL would silently stop finding videos on
    every H2C and P2S that has a card in -- a new bug, introduced by the fix
    for this one.
    """

    def test_internal_storage_does_not_suppress_the_timelapse_scan(self):
        state = FakeState(current_project_url="brtc://emmc/x.3mf", sdcard=True, sdcard_reported=True)

        assert print_file_reachable_over_ftp(state).reachable is False
        assert external_storage_present(state) is True

    def test_an_empty_slot_does_suppress_it(self):
        assert external_storage_present(FakeState(sdcard=False, sdcard_reported=True)) is False

    def test_silence_does_not(self):
        assert external_storage_present(FakeState(sdcard=False, sdcard_reported=False)) is True

    def test_no_state_does_not(self):
        assert external_storage_present(None) is True
