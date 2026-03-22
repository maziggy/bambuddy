"""Debug script to test LIKE query functionality."""

import asyncio

from sqlalchemy import func, select

from backend.app.core.database import async_session
from backend.app.models.spool import Spool


async def test_like_query():
    async with async_session() as db:
        # Create a test spool
        spool = Spool(
            material="PLA",
            tag_uid="A45012FFCCDDEE88AABBCCDDEE112233",
            label_weight=1000,
            core_weight=250,
        )
        db.add(spool)
        await db.commit()
        print(f"Created spool with tag: {spool.tag_uid}")

        # Try exact match first (this should work)
        result = await db.execute(select(Spool).where(func.upper(Spool.tag_uid) == "A45012FFCCDDEE88AABBCCDDEE112233"))
        found = result.scalar_one_or_none()
        print(f"Found with exact match: {found is not None}")

        # Try LIKE with pattern (this is what we're testing)
        result = await db.execute(select(Spool).where(func.upper(Spool.tag_uid).like("%45012FF%")))
        found = result.scalar_one_or_none()
        print(f"Found with LIKE %45012FF%: {found is not None}")
        if found:
            print(f"Found tag: {found.tag_uid}")

        # Try LIKE with different pattern
        result = await db.execute(select(Spool).where(func.upper(Spool.tag_uid).like("%B45012FF%")))
        found = result.scalar_one_or_none()
        print(f"Found with LIKE %B45012FF%: {found is not None}")


asyncio.run(test_like_query())
