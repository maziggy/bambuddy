from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.archive import PrintArchive


class ArchiveComparisonService:
    """Service for comparing print archives."""

    # Fields to compare
    COMPARABLE_FIELDS = [
        ("layer_height", "Layer Height", "mm"),
        ("nozzle_diameter", "Nozzle Diameter", "mm"),
        ("bed_temperature", "Bed Temperature", "°C"),
        ("nozzle_temperature", "Nozzle Temperature", "°C"),
        ("filament_type", "Filament Type", None),
        ("filament_used_grams", "Filament Used", "g"),
        ("print_time_seconds", "Print Time", "s"),
        ("total_layers", "Total Layers", None),
        ("status", "Status", None),
    ]

    def __init__(self, db: AsyncSession):
        self.db = db

    async def compare_archives(self, archive_ids: list[int]) -> dict:
        """Compare multiple archives side by side.

        Args:
            archive_ids: List of 2-5 archive IDs to compare

        Returns:
            Dictionary with comparison results
        """
        if len(archive_ids) < 2:
            raise ValueError("At least 2 archives required for comparison")
        if len(archive_ids) > 5:
            raise ValueError("Maximum 5 archives can be compared at once")

        # Fetch archives
        result = await self.db.execute(
            select(PrintArchive).options(selectinload(PrintArchive.project)).where(PrintArchive.id.in_(archive_ids))
        )
        archives = {a.id: a for a in result.scalars().all()}

        if len(archives) != len(archive_ids):
            missing = set(archive_ids) - set(archives.keys())
            raise ValueError(f"Archives not found: {missing}")

        # Preserve order from input
        ordered_archives = [archives[id] for id in archive_ids]

        # Build basic info for each archive
        archive_info = [
            {
                "id": a.id,
                "print_name": a.print_name or a.filename,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "printer_id": a.printer_id,
                "project_name": a.project.name if a.project else None,
            }
            for a in ordered_archives
        ]

        # Build field comparison
        comparison = []
        differences = []

        for field_name, display_name, unit in self.COMPARABLE_FIELDS:
            values = [getattr(a, field_name) for a in ordered_archives]

            # Format values for display
            formatted_values = []
            for v in values:
                if v is None:
                    formatted_values.append(None)
                elif field_name == "print_time_seconds":
                    # Format as human-readable time
                    hours = int(v) // 3600
                    minutes = (int(v) % 3600) // 60
                    formatted_values.append(f"{hours}h {minutes}m" if hours else f"{minutes}m")
                elif isinstance(v, float):
                    formatted_values.append(round(v, 2))
                else:
                    formatted_values.append(v)

            # Check if values differ
            non_none_values = [v for v in values if v is not None]
            has_difference = len({str(v) for v in non_none_values}) > 1 if non_none_values else False

            field_data = {
                "field": field_name,
                "label": display_name,
                "unit": unit,
                "values": formatted_values,
                "raw_values": values,
                "has_difference": has_difference,
            }

            comparison.append(field_data)

            if has_difference:
                differences.append(field_data)

        # Analyze success/failure correlation
        success_correlation = self._analyze_success_correlation(ordered_archives)

        return {
            "archives": archive_info,
            "comparison": comparison,
            "differences": differences,
            "success_correlation": success_correlation,
        }

    def _analyze_success_correlation(self, archives: list[PrintArchive]) -> dict:
        """Analyze what settings correlate with success/failure."""
        successful = [a for a in archives if a.status == "completed"]
        failed = [a for a in archives if a.status == "failed"]

        if not successful or not failed:
            return {
                "has_both_outcomes": False,
                "message": "Need both successful and failed prints to analyze correlation",
            }

        # Find settings that differ between successful and failed
        insights = []

        for field_name, display_name, _unit in self.COMPARABLE_FIELDS:
            if field_name == "status":
                continue

            success_values = [getattr(a, field_name) for a in successful if getattr(a, field_name) is not None]
            failed_values = [getattr(a, field_name) for a in failed if getattr(a, field_name) is not None]

            if not success_values or not failed_values:
                continue

            # For numeric fields, compare averages
            if isinstance(success_values[0], (int, float)):
                success_avg = sum(success_values) / len(success_values)
                failed_avg = sum(failed_values) / len(failed_values)

                if abs(success_avg - failed_avg) > 0.1 * max(abs(success_avg), abs(failed_avg), 0.01):
                    direction = "higher" if success_avg > failed_avg else "lower"
                    insights.append(
                        {
                            "field": field_name,
                            "label": display_name,
                            "success_avg": round(success_avg, 2),
                            "failed_avg": round(failed_avg, 2),
                            "insight": f"Successful prints had {direction} {display_name}",
                        }
                    )
            else:
                # For categorical fields, check if success uses different values
                success_set = {str(v) for v in success_values}
                failed_set = {str(v) for v in failed_values}

                if success_set != failed_set:
                    insights.append(
                        {
                            "field": field_name,
                            "label": display_name,
                            "success_values": list(success_set),
                            "failed_values": list(failed_set),
                            "insight": f"Different {display_name} used in successful vs failed prints",
                        }
                    )

        return {
            "has_both_outcomes": True,
            "successful_count": len(successful),
            "failed_count": len(failed),
            "insights": insights,
        }

    async def find_similar_archives(
        self,
        archive_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """Find archives with similar settings for comparison.

        Args:
            archive_id: The archive to find similar ones for
            limit: Maximum number of results

        Returns:
            List of similar archives with match reasons
        """
        # Get the reference archive
        result = await self.db.execute(select(PrintArchive).where(PrintArchive.id == archive_id))
        reference = result.scalar_one_or_none()

        if not reference:
            raise ValueError("Archive not found")

        # Find similar archives
        similar = []

        # By same print name
        if reference.print_name:
            result = await self.db.execute(
                select(PrintArchive)
                .where(
                    PrintArchive.id != archive_id,
                    PrintArchive.print_name == reference.print_name,
                )
                .order_by(PrintArchive.created_at.desc())
                .limit(limit)
            )
            for a in result.scalars().all():
                similar.append(
                    {
                        "archive": {
                            "id": a.id,
                            "print_name": a.print_name or a.filename,
                            "status": a.status,
                            "created_at": a.created_at.isoformat() if a.created_at else None,
                        },
                        "match_reason": "Same print name",
                        "match_score": 100,
                    }
                )

        # By content hash
        if reference.content_hash and len(similar) < limit:
            result = await self.db.execute(
                select(PrintArchive)
                .where(
                    PrintArchive.id != archive_id,
                    PrintArchive.content_hash == reference.content_hash,
                )
                .order_by(PrintArchive.created_at.desc())
                .limit(limit - len(similar))
            )
            for a in result.scalars().all():
                if not any(s["archive"]["id"] == a.id for s in similar):
                    similar.append(
                        {
                            "archive": {
                                "id": a.id,
                                "print_name": a.print_name or a.filename,
                                "status": a.status,
                                "created_at": a.created_at.isoformat() if a.created_at else None,
                            },
                            "match_reason": "Same file content",
                            "match_score": 95,
                        }
                    )

        # By same filament type
        if reference.filament_type and len(similar) < limit:
            result = await self.db.execute(
                select(PrintArchive)
                .where(
                    PrintArchive.id != archive_id,
                    PrintArchive.filament_type == reference.filament_type,
                )
                .order_by(PrintArchive.created_at.desc())
                .limit(limit - len(similar))
            )
            for a in result.scalars().all():
                if not any(s["archive"]["id"] == a.id for s in similar):
                    similar.append(
                        {
                            "archive": {
                                "id": a.id,
                                "print_name": a.print_name or a.filename,
                                "status": a.status,
                                "created_at": a.created_at.isoformat() if a.created_at else None,
                            },
                            "match_reason": f"Same filament type ({reference.filament_type})",
                            "match_score": 50,
                        }
                    )

        # Sort by match score
        similar.sort(key=lambda x: x["match_score"], reverse=True)

        return similar[:limit]
