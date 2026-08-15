"""The dispatch matcher and the pipeline pre-flight must agree on filament types.

``pipeline_eligibility`` exists to tell the operator, before a run starts, what
the matcher will do when it starts. It used to answer that from its own copy of
the equivalence table, and the copies had drifted into disagreeing in both
directions:

  - it aliased ``PLA Basic`` to ``PLA`` where the matcher does not, so a job
    could clear the pre-flight and then fail on type at dispatch;
  - it lacked the ``PA12-CF``/``PAHT-CF`` grouping the matcher has, so a job the
    matcher handles fine was flagged as a mismatch.

Both now read ``backend.app.utils.filament_types``. These tests pin the shared
answer and, more importantly, pin that the two modules give the *same* answer —
that is the property that broke, and it cannot be caught by testing either side
alone.
"""

import pytest

from backend.app.services.pipeline_eligibility import _canonical as eligibility_canonical
from backend.app.services.print_scheduler import canonical_filament_type as scheduler_canonical
from backend.app.utils.filament_types import (
    FILAMENT_TYPE_GROUPS,
    canonical_filament_type,
    filament_types_compatible,
)

# Every type either module has ever had an opinion about, plus the shapes the
# printer actually emits.
VOCABULARY = [
    "PLA",
    "PLA Basic",
    "PLA Matte",
    "PLA Silk",
    "PLA Pro",
    "PLA Tough",
    "PETG",
    "PETG HF",
    "PETG Basic",
    "PETG Translucent",
    "ABS",
    "ASA",
    "TPU",
    "TPU 95A",
    "PC",
    "PA",
    "PA-CF",
    "PA12-CF",
    "PAHT-CF",
    "PVA",
    "",
    "   ",  # whitespace-only: must behave identically on both sides
    "EXOTIC_WOOD",
]


class TestSchedulerAndEligibilityAgree:
    @pytest.mark.parametrize("ftype", VOCABULARY)
    def test_both_modules_canonicalise_identically(self, ftype):
        assert scheduler_canonical(ftype) == eligibility_canonical(ftype)

    @pytest.mark.parametrize("a", VOCABULARY)
    @pytest.mark.parametrize("b", ["PLA", "PA-CF", "PA12-CF", "PETG"])
    def test_both_modules_agree_on_compatibility(self, a, b):
        """The pre-flight must never claim a pairing the matcher would reject,
        nor flag one the matcher would accept."""
        assert (scheduler_canonical(a) == scheduler_canonical(b)) == (
            eligibility_canonical(a) == eligibility_canonical(b)
        )

    def test_the_two_regressions_that_prompted_this(self):
        # Used to differ: eligibility aliased the product name, the matcher did not.
        assert eligibility_canonical("PLA Basic") == scheduler_canonical("PLA Basic")
        assert not filament_types_compatible("PLA Basic", "PLA")
        # Used to differ the other way: the matcher grouped the nylons, eligibility did not.
        assert eligibility_canonical("PA12-CF") == scheduler_canonical("PA12-CF")
        assert filament_types_compatible("PA12-CF", "PA-CF")


class TestEquivalenceGroups:
    def test_nylon_variants_are_interchangeable(self):
        for variant in ("PA-CF", "PA12-CF", "PAHT-CF"):
            assert filament_types_compatible(variant, "PA-CF"), variant

    def test_carbon_filled_nylon_is_not_plain_nylon(self):
        """PA-CF is filled and PA is not; standing one in for the other changes
        the part."""
        assert not filament_types_compatible("PA-CF", "PA")

    def test_product_variants_are_not_aliases(self):
        """Silk, Matte and Basic print differently even though they dry alike,
        so the matcher must not substitute one for another."""
        for variant in ("PLA Silk", "PLA Matte", "PLA Basic", "PLA Tough"):
            assert not filament_types_compatible(variant, "PLA"), variant

    def test_every_group_canonicalises_to_its_first_entry(self):
        for group in FILAMENT_TYPE_GROUPS:
            for member in group:
                assert canonical_filament_type(member) == group[0].upper()


class TestNormalisation:
    def test_matching_is_case_insensitive(self):
        assert filament_types_compatible("pla", "PLA")
        assert filament_types_compatible("pa12-cf", "PA-CF")

    def test_missing_type_canonicalises_to_empty(self):
        assert canonical_filament_type(None) == ""
        assert canonical_filament_type("") == ""

    def test_surrounding_whitespace_is_preserved_on_purpose(self):
        """Stripping would be a quiet behaviour change, not a tidy-up.

        A whitespace-only tray_type would collapse to "", and so does the type
        of a 3MF filament element that declares none — so a typeless
        requirement would start matching a junk-typed tray instead of reporting
        the slot unmapped. Both sides of the app agree on the unstripped rule,
        which is what matters here; padded types are a separate fix.
        """
        assert canonical_filament_type("  PETG  ") == "  PETG  "
        assert not filament_types_compatible("  PETG  ", "PETG")
        assert not filament_types_compatible("   ", "")

    def test_an_unknown_type_passes_through_uppercased(self):
        """Third-party materials still compare against themselves rather than
        collapsing into one bucket."""
        assert canonical_filament_type("exotic_wood") == "EXOTIC_WOOD"
        assert filament_types_compatible("exotic_wood", "EXOTIC_WOOD")
        assert not filament_types_compatible("EXOTIC_WOOD", "PLA")
