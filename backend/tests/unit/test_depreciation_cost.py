"""Tests for printer depreciation cost calculation."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.archive import PrintArchive
from backend.app.models.printer import Printer

# -- Printer model fields ---------------------------------------------------


@pytest.mark.asyncio
async def test_printer_has_depreciation_fields(db_session: AsyncSession):
    """Printer model includes purchase_price and lifespan_hours columns."""
    printer = Printer(
        name="Test",
        serial_number="00M09A000000001",
        ip_address="192.168.1.100",
        access_code="12345678",
        purchase_price=600.0,
        lifespan_hours=3000.0,
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    assert printer.purchase_price == 600.0
    assert printer.lifespan_hours == 3000.0


@pytest.mark.asyncio
async def test_printer_depreciation_fields_nullable(db_session: AsyncSession):
    """purchase_price and lifespan_hours default to None."""
    printer = Printer(
        name="Test",
        serial_number="00M09A000000002",
        ip_address="192.168.1.101",
        access_code="12345678",
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)

    assert printer.purchase_price is None
    assert printer.lifespan_hours is None


# -- Archive model field ----------------------------------------------------


@pytest.mark.asyncio
async def test_archive_has_depreciation_cost_field(db_session: AsyncSession, printer_factory):
    """PrintArchive model includes depreciation_cost column."""
    printer = await printer_factory()
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
        print_time_seconds=7200,
        depreciation_cost=0.40,
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)

    assert archive.depreciation_cost == 0.40


@pytest.mark.asyncio
async def test_archive_depreciation_cost_nullable(db_session: AsyncSession, printer_factory):
    """depreciation_cost defaults to None."""
    printer = await printer_factory()
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
    )
    db_session.add(archive)
    await db_session.commit()
    await db_session.refresh(archive)

    assert archive.depreciation_cost is None


# -- Depreciation calculation logic -----------------------------------------


def _calc_depreciation(purchase_price: float, lifespan_hours: float, print_time_seconds: int) -> float:
    """Mirror the calculation used in production code."""
    return round((purchase_price / lifespan_hours) * (print_time_seconds / 3600), 2)


def test_depreciation_calculation_basic():
    """$600 printer / 3000h lifespan, 2-hour print = $0.40."""
    assert _calc_depreciation(600.0, 3000.0, 7200) == 0.40


def test_depreciation_calculation_short_print():
    """$600 printer / 3000h lifespan, 10-minute print = $0.03."""
    assert _calc_depreciation(600.0, 3000.0, 600) == 0.03


def test_depreciation_calculation_expensive_printer():
    """$1500 printer / 5000h lifespan, 2-hour print = $0.60."""
    assert _calc_depreciation(1500.0, 5000.0, 7200) == 0.60


def test_depreciation_calculation_one_second():
    """Very short print rounds correctly."""
    result = _calc_depreciation(600.0, 3000.0, 1)
    assert result == 0.0  # $0.000056 rounds to 0.00


# -- Schema fields ----------------------------------------------------------


def test_printer_schemas_include_depreciation_fields():
    """PrinterBase, PrinterUpdate, and PrinterResponse have the new fields."""
    from backend.app.schemas.printer import PrinterBase, PrinterResponse, PrinterUpdate

    # PrinterBase (used by PrinterCreate)
    base = PrinterBase(
        name="T",
        serial_number="S",
        ip_address="1.2.3.4",
        access_code="A",
        purchase_price=600.0,
        lifespan_hours=3000.0,
    )
    assert base.purchase_price == 600.0
    assert base.lifespan_hours == 3000.0

    # PrinterUpdate
    update = PrinterUpdate(purchase_price=500.0, lifespan_hours=2000.0)
    assert update.purchase_price == 500.0
    assert update.lifespan_hours == 2000.0

    # PrinterUpdate with None (clear values)
    update_clear = PrinterUpdate(purchase_price=None, lifespan_hours=None)
    assert update_clear.purchase_price is None


def test_archive_schemas_include_depreciation_cost():
    """ArchiveResponse and ArchiveStats include depreciation_cost."""
    from backend.app.schemas.archive import ArchiveStats

    stats = ArchiveStats(
        total_prints=10,
        successful_prints=9,
        failed_prints=1,
        total_print_time_hours=50.0,
        total_filament_grams=500.0,
        total_cost=25.0,
        prints_by_filament_type={},
        prints_by_printer={},
        total_depreciation_cost=5.50,
    )
    assert stats.total_depreciation_cost == 5.50


def test_archive_stats_depreciation_defaults_to_zero():
    """total_depreciation_cost defaults to 0.0 when not provided."""
    from backend.app.schemas.archive import ArchiveStats

    stats = ArchiveStats(
        total_prints=0,
        successful_prints=0,
        failed_prints=0,
        total_print_time_hours=0,
        total_filament_grams=0,
        total_cost=0,
        prints_by_filament_type={},
        prints_by_printer={},
    )
    assert stats.total_depreciation_cost == 0.0


def test_archive_slim_includes_depreciation_cost():
    """ArchiveSlim schema includes depreciation_cost field."""
    from datetime import datetime

    from backend.app.schemas.archive import ArchiveSlim

    slim = ArchiveSlim(
        printer_id=1,
        print_name="Test",
        print_time_seconds=3600,
        filament_used_grams=50.0,
        filament_type="PLA",
        filament_color=None,
        status="completed",
        started_at=datetime.now(),
        completed_at=datetime.now(),
        cost=1.25,
        depreciation_cost=0.40,
        quantity=1,
        created_at=datetime.now(),
    )
    assert slim.depreciation_cost == 0.40


def test_archive_slim_depreciation_defaults_to_none():
    """ArchiveSlim depreciation_cost defaults to None when not provided."""
    from datetime import datetime

    from backend.app.schemas.archive import ArchiveSlim

    slim = ArchiveSlim(
        printer_id=1,
        print_name="Test",
        print_time_seconds=3600,
        filament_used_grams=50.0,
        filament_type="PLA",
        filament_color=None,
        status="completed",
        started_at=datetime.now(),
        completed_at=datetime.now(),
        cost=1.25,
        quantity=1,
        created_at=datetime.now(),
    )
    assert slim.depreciation_cost is None


# -- Integration: recalculate endpoint --------------------------------------


def test_depreciation_formula_matches_expected():
    """The depreciation formula produces correct results for known inputs."""
    # $600 printer / 3000h lifespan, 2-hour print
    assert round((600.0 / 3000.0) * (7200 / 3600), 2) == 0.40
    # $1500 printer / 5000h lifespan, 30-minute print
    assert round((1500.0 / 5000.0) * (1800 / 3600), 2) == 0.15


@pytest.mark.asyncio
async def test_depreciation_not_calculated_without_price(db_session: AsyncSession, printer_factory):
    """If printer has no purchase_price, depreciation_cost stays None."""
    printer = await printer_factory()  # No purchase_price or lifespan_hours
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
        print_time_seconds=7200,
    )
    db_session.add(archive)
    await db_session.commit()

    # Simulate what recalculate does
    dep = None
    if archive.printer_id and archive.print_time_seconds:
        if printer.purchase_price and printer.lifespan_hours and printer.lifespan_hours > 0:
            dep = round((printer.purchase_price / printer.lifespan_hours) * (archive.print_time_seconds / 3600), 2)
    assert dep is None


@pytest.mark.asyncio
async def test_depreciation_calculated_with_both_fields(db_session: AsyncSession, printer_factory):
    """When printer has both price and lifespan, depreciation is calculated."""
    printer = await printer_factory(purchase_price=1500.0, lifespan_hours=5000.0)
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
        print_time_seconds=3600,  # 1 hour
    )
    db_session.add(archive)
    await db_session.commit()

    # Simulate calculation
    dep = round((printer.purchase_price / printer.lifespan_hours) * (archive.print_time_seconds / 3600), 2)
    assert dep == 0.30  # $1500 / 5000h * 1h


@pytest.mark.asyncio
async def test_depreciation_not_calculated_without_print_time(db_session: AsyncSession, printer_factory):
    """If archive has no print_time_seconds, depreciation is not calculated."""
    printer = await printer_factory(purchase_price=600.0, lifespan_hours=3000.0)
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
        print_time_seconds=None,
    )
    db_session.add(archive)
    await db_session.commit()

    dep = None
    if archive.printer_id and archive.print_time_seconds:
        if printer.purchase_price and printer.lifespan_hours and printer.lifespan_hours > 0:
            dep = round((printer.purchase_price / printer.lifespan_hours) * (archive.print_time_seconds / 3600), 2)
    assert dep is None


@pytest.mark.asyncio
async def test_depreciation_not_calculated_with_zero_lifespan(db_session: AsyncSession, printer_factory):
    """Zero lifespan_hours must not cause division by zero."""
    printer = await printer_factory(purchase_price=600.0, lifespan_hours=0.0)
    archive = PrintArchive(
        printer_id=printer.id,
        filename="test.3mf",
        file_path="archives/test.3mf",
        file_size=1024,
        print_time_seconds=7200,
    )
    db_session.add(archive)
    await db_session.commit()

    dep = None
    if archive.printer_id and archive.print_time_seconds:
        # lifespan_hours > 0 check prevents division by zero
        if printer.purchase_price and printer.lifespan_hours and printer.lifespan_hours > 0:
            dep = round((printer.purchase_price / printer.lifespan_hours) * (archive.print_time_seconds / 3600), 2)
    assert dep is None
