"""Direct tests for the shared colour-catalogue lookup (#2907).

The module was extracted so the two inventory modes stop answering "what colour
is this roll" in two places -- three fixes had landed on the built-in side
without crossing to the Spoolman side. Tests that reach it only through
``_find_or_create_filament`` cannot pin what it does, because the fake session
those use ignores the query and answers with a fixed row: the manufacturer
filter, the sub-brand filter, the id tie-break and the six-character prefix are
all invisible to them. These drive a real session against seeded rows instead.
"""

from __future__ import annotations

import pytest

from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.services.color_catalog_lookup import (
    CatalogColor,
    best_product_line_match,
    product_line_rank,
    resolve_bambu_color,
)


async def resolve_bambu_color_name(db, rgba, sub_brand):
    """The name alone, for the tests that are only about which row is chosen."""
    color = await resolve_bambu_color(db, rgba, sub_brand)
    return color.name if color else None


async def _seed(db, rows: list[dict]) -> None:
    for row in rows:
        db.add(ColorCatalogEntry(**row))
    await db.commit()


BAMBU_BLACK = {"manufacturer": "Bambu Lab", "color_name": "Black", "hex_color": "#000000", "material": "PLA Basic"}
# As catalog_defaults.py seeds it: the line in `material`, a bare colour in
# `color_name`. An earlier version of this fixture said "Matte Charcoal" here,
# which is SpoolmanDB's spelling, not the catalogue's.
BAMBU_CHARCOAL = {
    "manufacturer": "Bambu Lab",
    "color_name": "Charcoal",
    "hex_color": "#000000",
    "material": "PLA Matte",
}


@pytest.mark.asyncio
async def test_the_sub_brand_picks_between_two_rows_at_the_same_hex(db_session):
    """The reported defect. #000000 is two different colours depending on the line."""
    await _seed(db_session, [BAMBU_BLACK, BAMBU_CHARCOAL])

    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Basic") == "Black"
    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Matte") == "Charcoal"


@pytest.mark.asyncio
async def test_another_manufacturer_at_the_same_hex_is_not_an_answer(db_session):
    """Only Bambu rows may name a Bambu roll, whatever else sits at that hex.

    Seeded first, so it wins the id tie-break: with the manufacturer filter gone
    this returns "Panchroma Black". The sub-brand is left empty on purpose --
    with one supplied the material filter drops the Polymaker row anyway and the
    test would pass whether or not the manufacturer is checked, which is no test
    at all.
    """
    await _seed(
        db_session,
        [
            {"manufacturer": "Polymaker", "color_name": "Panchroma Black", "hex_color": "#000000", "material": "PLA"},
            BAMBU_BLACK,
        ],
    )

    assert await resolve_bambu_color_name(db_session, "000000FF", None) == "Black"
    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Basic") == "Black"


@pytest.mark.asyncio
async def test_a_sub_brand_the_catalogue_does_not_carry_has_no_answer(db_session):
    """A line with no row is None, not the nearest row at that hex."""
    await _seed(db_session, [BAMBU_BLACK])

    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Nonesuch") is None


@pytest.mark.asyncio
async def test_no_sub_brand_falls_back_to_the_lowest_id(db_session):
    """With no sub-brand the filter cannot settle it, so the lowest id wins.

    Not a third-party roll, which was the reasoning here before and was wrong:
    such a roll cannot reach this module from its only caller. ``is_bambu_lab_spool``
    gates non-Bambu rolls out, and ``parse_ams_tray`` substitutes ``tray_type``
    when ``tray_sub_brands`` is empty (spoolman.py:1116-1118), so ``sub_brand``
    is never empty on that path. The built-in side does reach it, since
    ``create_spool_from_tray`` passes ``tray_sub_brands`` through as it arrives.

    The ordering itself is not pinned here, and no better test exists to write.
    Deleting the ``order_by`` leaves this passing; only reversing it to
    ``.desc()`` fails. On SQLite the id is the rowid, so an unordered scan comes
    back in id order anyway -- assigning ids against insertion order does not
    separate them either -- and the suite is SQLite-only with no Postgres job,
    so nothing in CI can notice if that ORDER BY goes. The determinism it buys
    is real and untested; saying so is more honest than a docstring that claims
    the two modes cannot disagree about which row wins.
    """
    await _seed(db_session, [BAMBU_BLACK, BAMBU_CHARCOAL])

    assert await resolve_bambu_color_name(db_session, "000000FF", None) == "Black"
    assert await resolve_bambu_color_name(db_session, "000000FF", "") == "Black"


@pytest.mark.asyncio
async def test_only_the_first_six_characters_are_matched(db_session):
    """The catalogue stores RGB; the tray reports RGBA. The alpha is not part of
    the key, so an opaque roll matches on its RGB regardless of what follows."""
    await _seed(db_session, [BAMBU_BLACK])

    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Basic") == "Black"
    assert await resolve_bambu_color_name(db_session, "000000", "PLA Basic") == "Black"
    assert await resolve_bambu_color_name(db_session, "0000", "PLA Basic") is None
    assert await resolve_bambu_color_name(db_session, None, "PLA Basic") is None


@pytest.mark.asyncio
async def test_the_match_is_case_insensitive_on_both_sides(db_session):
    await _seed(
        db_session,
        [{"manufacturer": "bambu lab", "color_name": "Black", "hex_color": "#000000", "material": "pla basic"}],
    )

    assert await resolve_bambu_color_name(db_session, "000000ff", "PLA BASIC") == "Black"


@pytest.mark.asyncio
async def test_an_alpha_00_roll_is_clear_and_never_reaches_the_catalogue(db_session):
    """#1545, carried over deliberately -- see the docstring.

    The catalogue stores RGB only, so a clear roll's ``00000000`` would look up
    ``#000000`` and come back "Black". The seeded row here is exactly that trap:
    without the guard this returns "Black" and Spoolman mode names a clear roll
    black while the built-in path calls it "Clear" on the same printer.
    """
    await _seed(db_session, [BAMBU_BLACK])

    assert await resolve_bambu_color_name(db_session, "00000000", "PLA Basic") == "Clear"
    # Any hex with alpha 00, not just the black one.
    assert await resolve_bambu_color_name(db_session, "FFFFFF00", "PLA Basic") == "Clear"
    # Alpha FF is opaque and must still be looked up.
    assert await resolve_bambu_color_name(db_session, "000000FF", "PLA Basic") == "Black"


@pytest.mark.asyncio
async def test_the_row_hands_over_its_gradient_and_effect_not_just_its_name(db_session):
    """The built-in path takes all three off the row, so the shared lookup has to
    return all three or that caller could not use it. The shipped seed carries no
    stops on any row; a user-edited row does, and that is the case this is for."""
    await _seed(
        db_session,
        [
            {
                "manufacturer": "Bambu Lab",
                "color_name": "Arctic Whisper",
                "hex_color": "#A2C8E6",
                "material": "PLA Basic Gradient",
                "extra_colors": "A2C8E6,FFFFFF",
                "effect_type": "gradient",
            }
        ],
    )

    assert await resolve_bambu_color(db_session, "A2C8E6FF", "PLA Basic Gradient") == CatalogColor(
        "Arctic Whisper", "A2C8E6,FFFFFF", "gradient"
    )


@pytest.mark.asyncio
async def test_an_unknown_colour_has_no_name_rather_than_a_wrong_one(db_session):
    """The catalogue lags new releases; None is a real answer."""
    await _seed(db_session, [BAMBU_BLACK])

    assert await resolve_bambu_color_name(db_session, "ABCDEFFF", "PLA Basic") is None


class TestProductLineRank:
    """The three spellings a Bambu filament's name can take, ranked, and nothing else."""

    def test_the_sub_brand_is_the_strongest(self):
        """What Bambuddy names the filaments it creates."""
        assert product_line_rank("PLA Matte", "Charcoal", "PLA Matte", "PLA") == 0
        assert product_line_rank("pla matte", None, "PLA Matte", "PLA") == 0

    def test_the_library_spelling_folds_the_line_into_the_colour(self):
        """SpoolmanDB's names for these rolls, taken from a live 0.26.1 instance."""
        assert product_line_rank("Matte Charcoal", "Charcoal", "PLA Matte", "PLA") == 1
        assert product_line_rank("Translucent Teal", "Teal", "PLA Translucent", "PLA") == 1
        assert product_line_rank("HF Yellow", "Yellow", "PETG HF", "PETG") == 1
        # The plus is part of the product name, not the line the printer reports.
        assert product_line_rank("Tough+ Black", "Black", "PLA Tough", "PLA") == 1
        assert product_line_rank("Silk+ Gold", "Gold", "PLA Silk", "PLA") == 1
        # Galaxy puts the line after the colour.
        assert product_line_rank("Brown Galaxy", "Brown", "PLA Galaxy", "PLA") == 1

    def test_the_bare_colour_is_the_weakest(self):
        """PLA Basic's spelling in the library, and the lines with a base material
        of their own -- and shared across lines, which is why it ranks last."""
        assert product_line_rank("Black", "Black", "PLA Basic", "PLA") == 2
        assert product_line_rank("Black", "Black", "PLA Tough", "PLA") == 2

    def test_a_different_product_line_does_not_match(self):
        """The reported defect: a Matte roll must not attach to PLA Basic Black."""
        assert product_line_rank("Black", "Charcoal", "PLA Matte", "PLA") is None
        assert product_line_rank("PLA Basic", "Charcoal", "PLA Matte", "PLA") is None
        assert product_line_rank("Tough+ Black", "Black", "PLA Basic", "PLA") is None

    def test_an_unnamed_filament_never_matches(self):
        assert product_line_rank(None, "Black", "PLA Basic", "PLA") is None
        assert product_line_rank("", "Black", "PLA Basic", "PLA") is None
        assert product_line_rank("   ", "Black", "PLA Basic", "PLA") is None

    def test_with_neither_criterion_available_nothing_matches(self):
        """No catalogue row and no sub-brand: there is nothing to compare on, and
        guessing is what #2907 is about."""
        assert product_line_rank("Black", None, None, "PLA") is None
        assert product_line_rank("Black", None, "", "PLA") is None


class TestBestProductLineMatch:
    def test_the_line_spelling_beats_the_bare_colour_whatever_the_order(self):
        """The PLA Tough case from review: "Black" is PLA Basic's entry and comes
        first in the library, "Tough+ Black" is the roll's own."""
        basic = {"name": "Black"}
        tough = {"name": "Tough+ Black"}

        assert best_product_line_match([basic, tough], "Black", "PLA Tough", "PLA") is tough
        assert best_product_line_match([tough, basic], "Black", "PLA Tough", "PLA") is tough

    def test_the_bare_colour_is_taken_when_nothing_better_is_there(self):
        basic = {"name": "Black"}

        assert best_product_line_match([basic], "Black", "PLA Basic", "PLA") is basic

    def test_nothing_is_better_than_the_wrong_line(self):
        assert best_product_line_match([{"name": "Black"}], "Charcoal", "PLA Matte", "PLA") is None
        assert best_product_line_match([], "Charcoal", "PLA Matte", "PLA") is None
