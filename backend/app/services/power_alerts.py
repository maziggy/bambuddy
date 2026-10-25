"""Smart plug power alerts.

A plug with power alerts on is read in the background (smart_plug_manager), so
an alert arrives without anyone having Bambuddy open. An alert is sent when the
power crosses from inside the range to above the high value or below the low
value, and again only after it has been back inside the range: a printer idling
below a low threshold is reported once, not every few minutes. The low value is
only checked while the plug reports itself switched on, so turning a printer
off raises no "power low" alert; a plug that can't report its switch state
(a REST plug without a status URL, an MQTT plug with only a power topic) gets
no low alerts at all.
"""

import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.smart_plug import SmartPlug
from backend.app.services.notification_service import notification_service
from backend.app.services.plug_energy import read_plug
from backend.app.utils.local_time import to_naive_utc, utcnow_naive

logger = logging.getLogger(__name__)

# Minimum time between two alerts of one plug, so a value hovering at the
# threshold doesn't send one alert per check.
COOLDOWN = timedelta(minutes=5)

# How long an unreachable plug is left alone before it is polled again, so a
# plug that is unplugged doesn't log a connection warning every minute.
UNREACHABLE_RETRY_SECONDS = 600


class PowerAlertMonitor:
    """Remembers per plug whether the power was last above, below or inside the range.

    Kept in memory: after a restart a plug that is still outside its range
    alerts once more.
    """

    def __init__(self) -> None:
        self._outside: dict[int, str | None] = {}
        self._retry_at: dict[int, float] = {}

    def forget_all_but(self, plug_ids: set[int]) -> None:
        """Drop what is known about plugs no longer checked (alerts off, deleted)."""
        for known in (self._outside, self._retry_at):
            for plug_id in set(known) - plug_ids:
                del known[plug_id]

    def is_resting(self, plug_id: int) -> bool:
        return time.monotonic() < self._retry_at.get(plug_id, 0.0)

    def mark_unreachable(self, plug_id: int) -> None:
        self._retry_at[plug_id] = time.monotonic() + UNREACHABLE_RETRY_SECONDS

    async def evaluate(self, plug: SmartPlug, state: str | None, power: float | None, db: AsyncSession) -> None:
        self._retry_at.pop(plug.id, None)
        if not plug.power_alert_enabled or power is None:
            return
        if state == "OFF":
            self._outside[plug.id] = None
            return

        if plug.power_alert_high is not None and power > plug.power_alert_high:
            level, threshold = "high", plug.power_alert_high
        elif plug.power_alert_low is not None and power < plug.power_alert_low and state == "ON":
            level, threshold = "low", plug.power_alert_low
        else:
            self._outside[plug.id] = None
            return

        if self._outside.get(plug.id) == level:
            return
        last = to_naive_utc(plug.power_alert_last_triggered)
        if last is not None and utcnow_naive() - last < COOLDOWN:
            # Not recorded as seen: if the power is still out of range once the
            # cooldown has passed, the alert goes out then.
            return

        plug_id, plug_name, printer_id = plug.id, plug.name, plug.printer_id
        plug.power_alert_last_triggered = utcnow_naive()
        await db.commit()

        direction = "above" if level == "high" else "below"
        detail = f"Power consumption is {power:.1f}W, {direction} threshold of {threshold:.1f}W"
        logger.info("Power alert for plug %s: %s", plug_name, detail)
        await notification_service.on_printer_error(
            printer_id=printer_id,
            printer_name=plug_name,
            error_type=f"Power {level.title()}",
            db=db,
            error_detail=detail,
        )
        # Recorded only once it has gone out: a failed write or send is retried
        # on a later pass (after the cooldown, if the write got through).
        self._outside[plug_id] = level


power_alert_monitor = PowerAlertMonitor()


async def check_all_plugs(db: AsyncSession, monitor: PowerAlertMonitor = power_alert_monitor) -> None:
    """Read every plug with power alerts on and alert on any crossing.

    One plug failing to answer, or failing to save, doesn't stop the others.
    """
    plugs = (await db.execute(select(SmartPlug).where(SmartPlug.power_alert_enabled.is_(True)))).scalars().all()
    checked = [
        (plug, plug.id, plug.name)
        for plug in plugs
        if plug.power_alert_high is not None or plug.power_alert_low is not None
    ]
    monitor.forget_all_but({plug_id for _, plug_id, _ in checked})

    rolled_back = False
    for plug, plug_id, plug_name in checked:
        if monitor.is_resting(plug_id):
            continue
        try:
            if rolled_back:
                # A rollback expires every loaded row; reading one lazily on an
                # async session would fail.
                await db.refresh(plug)
            state, power = await read_plug(plug, db)
            if state is None and power is None:
                # MQTT plugs are read from memory: nothing to spare by pausing,
                # and one that hasn't published yet would be ignored for minutes.
                if plug.plug_type != "mqtt":
                    monitor.mark_unreachable(plug_id)
                continue
            await monitor.evaluate(plug, state, power, db)
        except Exception as e:
            # A failed write leaves the session needing a rollback; without it
            # every plug after this one would fail on the same session.
            await db.rollback()
            rolled_back = True
            logger.warning("Power alert check failed for plug %s: %s", plug_name, e)
