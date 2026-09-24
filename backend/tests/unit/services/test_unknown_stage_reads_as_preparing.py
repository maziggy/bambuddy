"""A stage number Bambuddy cannot name reads as "Preparing" on the card.

New printers report stages before Bambuddy learns their names -- the H2C still
has several -- and until now those reached the card as ``Unknown stage (72)``.
The number means nothing to the person reading it, and the card is not where it
belongs: every stage that has turned out to be unnamed so far was part of the
run-up to printing, so "Preparing" is both the more useful answer and the more
likely one.

The substitution is display-only. ``get_stage_name`` still reports the number,
because it also feeds the stage-transition log line and the once-per-session
warning that exists precisely to capture unnamed stages so they can be named in
a later release. Replacing the number there would hide the only thing that
reports these -- these tests pin that separation, not just the new label.
"""

import pytest

from backend.app.services.bambu_mqtt import STAGE_NAMES, get_stage_name
from backend.app.services.printer_manager import get_derived_status_name

pytestmark = pytest.mark.unit


class _State:
    """Minimal PrinterState stand-in: only the fields this path reads."""

    def __init__(self, stg_cur: int, state: str = "RUNNING") -> None:
        self.stg_cur = stg_cur
        self.state = state
        self.temperatures: dict = {}
        self.progress = 0
        self.layer_num = 0


# Two numbers no firmware in the table uses. 72 is the one reported from an H2C
# in the field (#2916's logging change was added for exactly this gap).
UNNAMED = [n for n in (68, 70, 71, 72, 73, 75, 76, 80, 200, 254) if n not in STAGE_NAMES]


class TestTheCard:
    @pytest.mark.parametrize("stage", UNNAMED)
    def test_an_unnamed_stage_reads_as_preparing(self, stage):
        assert get_derived_status_name(_State(stage)) == "Preparing"

    def test_it_never_shows_the_raw_number(self):
        for stage in UNNAMED:
            assert "Unknown" not in (get_derived_status_name(_State(stage)) or "")

    @pytest.mark.parametrize("stage", sorted(STAGE_NAMES))
    def test_every_named_stage_keeps_its_own_name(self, stage):
        # The substitution must not swallow the table it is standing in for.
        assert get_derived_status_name(_State(stage)) == STAGE_NAMES[stage]

    def test_the_label_matches_the_one_the_table_already_uses(self):
        # Stage 74 is "Preparing" in STAGE_NAMES. An unnamed stage renders the
        # same string rather than a second spelling of the same idea.
        assert get_derived_status_name(_State(74)) == get_derived_status_name(_State(UNNAMED[0]))

    def test_a_paused_printer_is_covered_too(self):
        assert get_derived_status_name(_State(UNNAMED[0], state="PAUSE")) == "Preparing"


class TestTheIdleSentinelsAreUntouched:
    """255 and -1 are "no stage", not unnamed stages, and must stay None."""

    def test_a1_p1_idle_sentinel(self):
        assert get_derived_status_name(_State(255, state="IDLE")) is None

    def test_x1_idle_sentinel(self):
        assert get_derived_status_name(_State(-1, state="IDLE")) is None

    def test_the_sentinels_stay_none_even_while_running(self):
        # Out of range on both sides of 0..254; the temperature fallback below
        # has nothing to work with here, so the answer is still no stage.
        assert get_derived_status_name(_State(255)) is None


class TestTheDiagnosticPathStillReportsTheNumber:
    @pytest.mark.parametrize("stage", UNNAMED)
    def test_get_stage_name_still_names_the_number(self, stage):
        # This is what the log line and the unnamed-stage warning print. If it
        # ever starts saying "Preparing", nobody can tell which stage to add.
        assert get_stage_name(stage) == f"Unknown stage ({stage})"

    def test_the_two_paths_genuinely_disagree_for_an_unnamed_stage(self):
        stage = UNNAMED[0]
        assert get_derived_status_name(_State(stage)) != get_stage_name(stage)

    def test_and_agree_for_a_named_one(self):
        assert get_derived_status_name(_State(74)) == get_stage_name(74)
