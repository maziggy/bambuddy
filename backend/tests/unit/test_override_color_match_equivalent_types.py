"""An override's colour matches a tray of an equivalent filament type (SN-C1).

The type check already treats PA12-CF and PA-CF as the same filament, but the
colour check that ranks printers for an "Any model" job compared the names as
written. With an override for PA12-CF in a colour, a printer holding PA-CF in
that colour counted no match and was skipped, so the job waited forever.
"""

import pytest

from backend.app.services.print_scheduler import PrintScheduler


def _raw(tray_type: str, color: str = "FF0000FF") -> dict:
    return {"ams": [{"id": 0, "tray": [{"id": 0, "tray_type": tray_type, "tray_color": color}]}]}


@pytest.mark.parametrize(("wanted", "loaded"), [("PA12-CF", "PA-CF"), ("PA-CF", "PA12-CF")])
def test_an_equivalent_type_in_the_colour_counts(wanted, loaded):
    overrides = [{"type": wanted, "color": "#FF0000"}]

    assert PrintScheduler()._count_override_color_matches(1, overrides, _raw(loaded)) == 1


def test_another_type_in_the_colour_does_not():
    overrides = [{"type": "PETG", "color": "#FF0000"}]

    assert PrintScheduler()._count_override_color_matches(1, overrides, _raw("PLA")) == 0


def test_an_external_spool_of_an_equivalent_type_counts():
    overrides = [{"type": "PA-CF", "color": "#FF0000"}]
    raw = {"ams": [], "vt_tray": [{"id": 254, "tray_type": "PA12-CF", "tray_color": "FF0000FF"}]}

    assert PrintScheduler()._count_override_color_matches(1, overrides, raw) == 1
