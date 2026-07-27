"""Tests for _pick_power_plug() in the print scheduler (#2629).

A printer can have several plugs linked to it: the one feeding the printer and
accessories that merely follow the print cycle (filter fan, chamber light). Only
the former can bring an offline printer back, so the queue's power-on step must
pick it rather than whichever row came back first.
"""

from types import SimpleNamespace

from backend.app.services.print_scheduler import PrintScheduler


def _plug(plug_id: int, name: str, controls_printer_power: bool) -> SimpleNamespace:
    return SimpleNamespace(id=plug_id, name=name, controls_printer_power=controls_printer_power)


class TestPickPowerPlug:
    def test_prefers_power_plug_over_earlier_accessory(self):
        fan = _plug(1, "BentoBox Filter", False)
        printer_plug = _plug(2, "P1S Power", True)

        assert PrintScheduler._pick_power_plug([fan, printer_plug]) is printer_plug

    def test_keeps_first_power_plug_when_several_qualify(self):
        first = _plug(1, "P1S Power", True)
        second = _plug(2, "Bench Power", True)

        assert PrintScheduler._pick_power_plug([first, second]) is first

    def test_falls_back_to_first_when_none_flagged(self):
        """Pre-#2629 behaviour for setups where no plug is marked as the power
        source — powering on may not work, but nothing gets worse."""
        fan = _plug(1, "BentoBox Filter", False)
        light = _plug(2, "Chamber Light", False)

        assert PrintScheduler._pick_power_plug([fan, light]) is fan

    def test_single_plug_is_returned_regardless(self):
        only = _plug(1, "P1S Power", True)

        assert PrintScheduler._pick_power_plug([only]) is only
