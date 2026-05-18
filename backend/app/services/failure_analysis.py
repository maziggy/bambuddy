from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.print_log import PrintLogEntry
from backend.app.models.printer import Printer


class FailureAnalysisService:
    """Service for analyzing print failure patterns.

    Reads from print_log_entries (per-event data) rather than print_archives
    so reprints contribute each run and orphan events (archive deleted, log
    row survived via ON DELETE SET NULL) still count consistently with
    Quick Stats. The archive-based predecessor diverged from Quick Stats
    after #1378 moved the rest of the page to per-event aggregation.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze_failures(
        self,
        days: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        printer_id: int | None = None,
        project_id: int | None = None,
        created_by_id: int | None = None,
    ) -> dict:
        """Analyze failure patterns across logged print events."""
        # Build base query — separate date vs non-date filters for trend reuse
        base_filter = []
        non_date_filter = []
        if date_from or date_to:
            if date_from:
                dt_from = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
                base_filter.append(PrintLogEntry.created_at >= dt_from)
            if date_to:
                dt_to = datetime.combine(date_to, time.max, tzinfo=timezone.utc)
                base_filter.append(PrintLogEntry.created_at <= dt_to)
            range_start = dt_from if date_from else datetime.now(timezone.utc) - timedelta(days=365)
            range_end = dt_to if date_to else datetime.now(timezone.utc)
            effective_days = max((range_end - range_start).days, 1)
        else:
            effective_days = days if days is not None else 30
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=effective_days)
            base_filter.append(PrintLogEntry.created_at >= cutoff_date)
        if printer_id:
            non_date_filter.append(PrintLogEntry.printer_id == printer_id)
        # project_id is an archive-level concept; PrintLogEntry has no project
        # link, so we resolve it by archive_id where present.
        if project_id:
            from backend.app.models.archive import PrintArchive

            project_archive_ids = await self.db.execute(
                select(PrintArchive.id).where(PrintArchive.project_id == project_id)
            )
            archive_ids = [row[0] for row in project_archive_ids.fetchall()]
            if archive_ids:
                non_date_filter.append(PrintLogEntry.archive_id.in_(archive_ids))
            else:
                # No archives in this project → nothing to count
                non_date_filter.append(PrintLogEntry.id.is_(None))
        if created_by_id is not None:
            if created_by_id == -1:
                non_date_filter.append(PrintLogEntry.created_by_id.is_(None))
            else:
                non_date_filter.append(PrintLogEntry.created_by_id == created_by_id)
        base_filter.extend(non_date_filter)

        # Total counts
        total_result = await self.db.execute(select(func.count(PrintLogEntry.id)).where(and_(*base_filter)))
        total_prints = total_result.scalar() or 0

        failed_result = await self.db.execute(
            select(func.count(PrintLogEntry.id)).where(
                and_(*base_filter, PrintLogEntry.status.in_(["failed", "aborted"]))
            )
        )
        failed_prints = failed_result.scalar() or 0

        failure_rate = (failed_prints / total_prints * 100) if total_prints > 0 else 0

        # Failures by reason
        reason_result = await self.db.execute(
            select(
                PrintLogEntry.failure_reason,
                func.count(PrintLogEntry.id).label("count"),
            )
            .where(and_(*base_filter, PrintLogEntry.status.in_(["failed", "aborted"])))
            .group_by(PrintLogEntry.failure_reason)
            .order_by(func.count(PrintLogEntry.id).desc())
        )
        failures_by_reason = {(row[0] or "Unknown"): row[1] for row in reason_result.fetchall()}

        # Failures by filament type
        filament_result = await self.db.execute(
            select(
                PrintLogEntry.filament_type,
                func.count(PrintLogEntry.id).label("count"),
            )
            .where(and_(*base_filter, PrintLogEntry.status.in_(["failed", "aborted"])))
            .group_by(PrintLogEntry.filament_type)
            .order_by(func.count(PrintLogEntry.id).desc())
        )
        failures_by_filament = {(row[0] or "Unknown"): row[1] for row in filament_result.fetchall()}

        # Failures by printer
        printer_result = await self.db.execute(
            select(
                PrintLogEntry.printer_id,
                func.count(PrintLogEntry.id).label("count"),
            )
            .where(
                and_(
                    *base_filter,
                    PrintLogEntry.status.in_(["failed", "aborted"]),
                    PrintLogEntry.printer_id.isnot(None),
                )
            )
            .group_by(PrintLogEntry.printer_id)
            .order_by(func.count(PrintLogEntry.id).desc())
        )
        failures_by_printer_id = {row[0]: row[1] for row in printer_result.fetchall()}

        # Get printer names
        if failures_by_printer_id:
            printers_result = await self.db.execute(
                select(Printer.id, Printer.name).where(Printer.id.in_(failures_by_printer_id.keys()))
            )
            printer_names = {row[0]: row[1] for row in printers_result.fetchall()}
            failures_by_printer = {
                printer_names.get(pid, f"Printer {pid}"): count for pid, count in failures_by_printer_id.items()
            }
        else:
            failures_by_printer = {}

        # Failures by hour of day
        failed_events_result = await self.db.execute(
            select(PrintLogEntry.started_at).where(
                and_(
                    *base_filter,
                    PrintLogEntry.status.in_(["failed", "aborted"]),
                    PrintLogEntry.started_at.isnot(None),
                )
            )
        )
        failures_by_hour = defaultdict(int)
        for (started_at,) in failed_events_result.fetchall():
            if started_at:
                hour = started_at.hour
                failures_by_hour[hour] += 1
        failures_by_hour_complete = {h: failures_by_hour.get(h, 0) for h in range(24)}

        # Recent failures
        recent_result = await self.db.execute(
            select(PrintLogEntry)
            .where(and_(*base_filter, PrintLogEntry.status.in_(["failed", "aborted"])))
            .order_by(PrintLogEntry.created_at.desc())
            .limit(10)
        )
        recent_failures = [
            {
                "id": e.archive_id,
                "print_name": e.print_name,
                "failure_reason": e.failure_reason,
                "filament_type": e.filament_type,
                "printer_id": e.printer_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in recent_result.scalars().all()
        ]

        # Failure rate trend (by week)
        trend_data = []
        num_weeks = max(effective_days // 7, 1)
        for i in range(num_weeks):
            week_end = datetime.now(timezone.utc) - timedelta(weeks=i)
            week_start = week_end - timedelta(weeks=1)

            week_filter = [
                PrintLogEntry.created_at >= week_start,
                PrintLogEntry.created_at < week_end,
                *non_date_filter,
            ]

            week_total = await self.db.execute(select(func.count(PrintLogEntry.id)).where(and_(*week_filter)))
            week_failed = await self.db.execute(
                select(func.count(PrintLogEntry.id)).where(
                    and_(*week_filter, PrintLogEntry.status.in_(["failed", "aborted"]))
                )
            )

            total = week_total.scalar() or 0
            failed = week_failed.scalar() or 0
            rate = (failed / total * 100) if total > 0 else 0

            trend_data.append(
                {
                    "week_start": week_start.date().isoformat(),
                    "total_prints": total,
                    "failed_prints": failed,
                    "failure_rate": round(rate, 1),
                }
            )

        trend_data.reverse()  # Oldest first

        return {
            "period_days": effective_days,
            "total_prints": total_prints,
            "failed_prints": failed_prints,
            "failure_rate": round(failure_rate, 1),
            "failures_by_reason": failures_by_reason,
            "failures_by_filament": failures_by_filament,
            "failures_by_printer": failures_by_printer,
            "failures_by_hour": failures_by_hour_complete,
            "recent_failures": recent_failures,
            "trend": trend_data,
        }
