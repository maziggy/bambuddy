"""The charging policy: what a print's wallet charge is made of (#3581).

By default a charge and its description are the filament's alone, as before.
The policy can add the run's actual energy and printer wear, separately for
completed prints and for failed or cancelled ones, and charge the filament of
a print that did not finish in full or not at all. Everything stays in the
one ``print_charge`` transaction and is listed in its description, ahead of
the ``[failed: ...]`` suffix the Finance page parses.
"""

import json
import re

import pytest
from sqlalchemy import delete, select

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation, CostCenter, WalletTransaction
from backend.app.models.print_log import INCLUDE_OPEN_RUNS, PrintLogEntry
from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.finance_billing import (
    ChargePolicy,
    apply_pending_print_charge,
    apply_print_charge_for_archive,
    defer_print_charge,
    print_charge_policy,
    recover_pending_print_charges,
)

pytestmark = pytest.mark.asyncio

# frontend/src/pages/FinancePage.tsx: PRINT_CHARGE_PARTIAL_REGEX
PARTIAL_SUFFIX = re.compile(r"\[(aborted|failed|cancelled):\s*([^\]]*)\]", re.IGNORECASE)


@pytest.fixture
async def shop(db_session):
    """A user printing "Benchy" (1.20 of filament) against a cost centre that
    holds a reservation for it, on a run of its own."""
    db_session.add(Settings(key="billing_enabled", value="true"))
    user = User(username="payer", password_hash="x", is_active=True)
    center = CostCenter(name="Lab", is_active=True, is_private=False)
    db_session.add_all([user, center])
    await db_session.flush()
    archive = PrintArchive(
        filename="benchy.3mf",
        file_path="archives/test/benchy.3mf",
        file_size=1,
        print_name="Benchy",
        status="completed",
        cost=1.2,
        created_by_id=user.id,
        cost_center_id=center.id,
    )
    db_session.add(archive)
    await db_session.flush()
    run = PrintLogEntry(archive_id=archive.id, status="completed", billing_run_id="run-1", cost_center_id=center.id)
    hold = BudgetReservation(
        cost_center_id=center.id, amount=1.2, status="active", source_type="print_archive", print_archive_id=archive.id
    )
    db_session.add_all([run, hold])
    await db_session.commit()
    return {"archive": archive.id, "run": run.id, "hold": hold.id, "user": user.id}


@pytest.fixture(autouse=True)
def reported(monkeypatch):
    """The billing-failed alerts sent for charges that could not be written
    after their print's end, instead of sending them: (run id, error)."""
    from backend.app.services import finance_billing

    alerts = []

    async def record(run, error):
        alerts.append((run.id, str(error)))

    monkeypatch.setattr(finance_billing, "report_print_charge_failed", record)
    return alerts


async def _charges(db_session) -> list[WalletTransaction]:
    db_session.expire_all()
    return list((await db_session.scalars(select(WalletTransaction).order_by(WalletTransaction.id))).all())


async def _pending(db_session, run_id: int) -> str | None:
    db_session.expire_all()
    return await db_session.scalar(
        select(PrintLogEntry.pending_charge)
        .where(PrintLogEntry.id == run_id)
        .execution_options(**{INCLUDE_OPEN_RUNS: True})
    )


async def _hold_status(db_session, hold_id: int) -> str:
    db_session.expire_all()
    return (await db_session.get(BudgetReservation, hold_id)).status


class TestTheCharge:
    async def test_without_energy_or_wear_it_is_the_filaments_as_before(self, db_session, shop):
        assert await apply_print_charge_for_archive(db_session, shop["archive"], run_entry_id=shop["run"])
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.description) == (-1.2, "Print charge: Benchy")

    async def test_energy_and_wear_are_added_and_listed(self, db_session, shop):
        assert await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], energy_cost=0.083, wear_cost=0.25
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.transaction_type == "print_charge"
        assert charge.amount == -1.53
        assert charge.description == "Print charge: Benchy (filament 1.20 + energy 0.08 + wear 0.25)"
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_wear_alone(self, db_session, shop):
        assert await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], wear_cost=0.25
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.description) == (-1.45, "Print charge: Benchy (filament 1.20 + wear 0.25)")

    async def test_energy_that_could_not_be_read_is_said_and_the_rest_charged(self, db_session, shop):
        assert await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], wear_cost=0.25, energy_unavailable=True
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -1.45
        assert charge.description == "Print charge: Benchy (filament 1.20 + wear 0.25; energy not available)"

    async def test_a_part_that_rounds_to_nothing_is_not_listed(self, db_session, shop):
        assert await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], energy_cost=0.004, wear_cost=None
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.description) == (-1.2, "Print charge: Benchy")

    async def test_a_failed_run_pays_its_share_of_filament_and_all_its_energy_and_wear(self, db_session, shop):
        """Filament stays proportional; energy and wear are what the run used."""
        assert await apply_print_charge_for_archive(
            db_session,
            shop["archive"],
            run_entry_id=shop["run"],
            run_status="failed",
            filament_usage=(50.0, 100.0),
            energy_cost=0.08,
            wear_cost=0.25,
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.93
        assert charge.description == (
            "Print charge: Benchy (filament 0.60 + energy 0.08 + wear 0.25) [failed: 50.0% filament (50.0g/100.0g)]"
        )
        # What the Finance page reads off it, and shows without it.
        suffix = PARTIAL_SUFFIX.search(charge.description)
        assert suffix is not None and suffix.group(1) == "failed"
        assert charge.description.replace(suffix.group(0), "").strip() == (
            "Print charge: Benchy (filament 0.60 + energy 0.08 + wear 0.25)"
        )


class TestTheFilamentOfARunThatDidNotFinish:
    """Proportional to what it used (the default, as before), in full, or not
    charged."""

    async def _charge(self, db_session, shop, status="failed", **kwargs) -> bool:
        charged = await apply_print_charge_for_archive(
            db_session,
            shop["archive"],
            run_entry_id=shop["run"],
            run_status=status,
            filament_usage=(50.0, 100.0),
            **kwargs,
        )
        await db_session.commit()
        return charged

    async def test_proportional_is_the_default_and_reads_as_before(self, db_session, shop):
        assert await self._charge(db_session, shop)

        [charge] = await _charges(db_session)
        assert charge.amount == -0.6
        assert charge.description == "Print charge: Benchy [failed: 50.0% filament (50.0g/100.0g)]"

    @pytest.mark.parametrize("status", ["failed", "cancelled", "aborted"])
    async def test_in_full(self, db_session, shop, status):
        assert await self._charge(db_session, shop, status=status, partial_filament="full")

        [charge] = await _charges(db_session)
        assert charge.amount == -1.2
        assert charge.description == (
            f"Print charge: Benchy (filament 1.20; filament charged in full) [{status}: full filament]"
        )
        suffix = PARTIAL_SUFFIX.search(charge.description)
        assert suffix is not None and suffix.group(1) == status
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_in_full_with_energy_and_wear(self, db_session, shop):
        assert await self._charge(db_session, shop, partial_filament="full", energy_cost=0.08, wear_cost=0.25)

        [charge] = await _charges(db_session)
        assert charge.amount == -1.53
        assert charge.description == (
            "Print charge: Benchy (filament 1.20 + energy 0.08 + wear 0.25; filament charged in full) "
            "[failed: full filament]"
        )

    async def test_not_charged_and_nothing_else_is_no_charge(self, db_session, shop):
        """Nothing to charge: no transaction, and the reservation is released,
        as for any print charged nothing."""
        assert not await self._charge(db_session, shop, partial_filament="none")

        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "released"

    async def test_not_charged_but_its_energy_and_wear_are(self, db_session, shop):
        assert await self._charge(db_session, shop, partial_filament="none", energy_cost=0.08, wear_cost=0.25)

        [charge] = await _charges(db_session)
        assert charge.amount == -0.33
        assert charge.description == (
            "Print charge: Benchy (energy 0.08 + wear 0.25; filament not charged) [failed: filament not charged]"
        )
        suffix = PARTIAL_SUFFIX.search(charge.description)
        assert suffix is not None and suffix.group(1) == "failed"
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_not_charged_and_energy_that_could_not_be_read_is_no_charge(self, db_session, shop):
        assert not await self._charge(db_session, shop, partial_filament="none", energy_unavailable=True)

        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "released"

    async def test_a_completed_run_is_charged_its_filament_whatever_the_mode(self, db_session, shop):
        assert await self._charge(db_session, shop, status="completed", partial_filament="none")

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.description) == (-1.2, "Print charge: Benchy")


class TestARunWithNoFilamentToCharge:
    async def test_failed_without_filament_data_and_nothing_else_is_no_charge(self, db_session, shop):
        """As before."""
        assert not await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], run_status="failed", filament_usage=(None, None)
        )
        await db_session.commit()

        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "released"

    async def test_failed_without_filament_data_pays_its_energy_and_wear(self, db_session, shop):
        assert await apply_print_charge_for_archive(
            db_session,
            shop["archive"],
            run_entry_id=shop["run"],
            run_status="failed",
            filament_usage=(None, None),
            energy_cost=0.08,
            wear_cost=0.25,
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.33
        assert charge.description == (
            "Print charge: Benchy (energy 0.08 + wear 0.25) [failed: insufficient filament data]"
        )
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_failed_print_of_an_archive_without_a_cost_pays_its_energy_and_wear(self, db_session, shop):
        (await db_session.get(PrintArchive, shop["archive"])).cost = None
        await db_session.commit()

        assert await apply_print_charge_for_archive(
            db_session,
            shop["archive"],
            run_entry_id=shop["run"],
            run_status="failed",
            filament_usage=(50.0, 100.0),
            wear_cost=0.25,
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.25

    async def test_completed_print_of_an_archive_without_a_cost_pays_its_energy_and_wear(self, db_session, shop):
        """Energy and wear are charged whenever the policy includes them: a
        print on free filament still used the printer and the power."""
        (await db_session.get(PrintArchive, shop["archive"])).cost = None
        await db_session.commit()

        assert await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], energy_cost=0.08, wear_cost=0.25
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.33
        assert charge.description == "Print charge: Benchy (energy 0.08 + wear 0.25)"
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    @pytest.mark.parametrize("status", ["completed", "failed"])
    async def test_a_run_whose_filament_cost_is_given_as_nothing_pays_its_energy_and_wear(
        self, db_session, shop, status
    ):
        """The caller's own figure for the filament (``base_cost_override``),
        nothing for a run on free spools, although its archive has a cost."""
        assert await apply_print_charge_for_archive(
            db_session,
            shop["archive"],
            run_entry_id=shop["run"],
            run_status=status,
            base_cost_override=0.0,
            filament_usage=(50.0, 100.0) if status == "failed" else None,
            energy_cost=0.08,
            wear_cost=0.25,
        )
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.33
        suffix = " [failed: 50.0% filament (50.0g/100.0g)]" if status == "failed" else ""
        assert charge.description == f"Print charge: Benchy (energy 0.08 + wear 0.25){suffix}"
        assert bool(PARTIAL_SUFFIX.search(charge.description)) == (status == "failed")
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    @pytest.mark.parametrize("extras", [{}, {"energy_unavailable": True}, {"energy_cost": 0.0, "wear_cost": 0.0}])
    async def test_completed_without_a_filament_cost_and_nothing_else_is_no_charge(self, db_session, shop, extras):
        """As before: no transaction, and the reservation is released."""
        assert not await apply_print_charge_for_archive(
            db_session, shop["archive"], run_entry_id=shop["run"], base_cost_override=0.0, **extras
        )
        await db_session.commit()

        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "released"


async def _set(db_session, **settings: str) -> None:
    db_session.add_all([Settings(key=key, value=value) for key, value in settings.items()])
    await db_session.commit()


DEFAULT = ChargePolicy(partial_filament="proportional", energy=False, wear=False)
OUTCOMES = ["completed", "failed", "cancelled", "aborted"]


class TestThePolicy:
    @pytest.mark.parametrize("status", OUTCOMES)
    async def test_nothing_saved_is_the_charge_as_it_was(self, db_session, shop, status):
        assert await print_charge_policy(db_session, status) == DEFAULT == ChargePolicy()

    async def test_completed_prints_have_their_own_switches(self, db_session, shop):
        await _set(db_session, billing_completed_energy="true")
        assert await print_charge_policy(db_session, "completed") == DEFAULT._replace(energy=True)
        await _set(db_session, billing_completed_wear="true")
        assert await print_charge_policy(db_session, "completed") == DEFAULT._replace(energy=True, wear=True)
        for status in OUTCOMES[1:]:
            assert await print_charge_policy(db_session, status) == DEFAULT, status

    @pytest.mark.parametrize("status", OUTCOMES[1:])
    async def test_failed_and_cancelled_prints_have_theirs(self, db_session, shop, status):
        await _set(db_session, billing_failed_energy="true")
        assert await print_charge_policy(db_session, status) == DEFAULT._replace(energy=True)
        await _set(db_session, billing_failed_wear="true")
        assert await print_charge_policy(db_session, status) == DEFAULT._replace(energy=True, wear=True)
        assert await print_charge_policy(db_session, "completed") == DEFAULT

    @pytest.mark.parametrize("mode", ["proportional", "full", "none"])
    async def test_the_filament_mode_is_for_prints_that_did_not_finish(self, db_session, shop, mode):
        await _set(db_session, billing_failed_filament=mode)
        for status in OUTCOMES[1:]:
            assert (await print_charge_policy(db_session, status)).partial_filament == mode
        assert (await print_charge_policy(db_session, "completed")).partial_filament == "proportional"

    async def test_a_mode_it_does_not_know_is_proportional(self, db_session, shop):
        await _set(db_session, billing_failed_filament="double")
        assert await print_charge_policy(db_session, "failed") == DEFAULT

    @pytest.mark.parametrize("status", OUTCOMES)
    async def test_the_default_while_billing_is_off(self, db_session, status):
        await _set(
            db_session,
            billing_completed_energy="true",
            billing_completed_wear="true",
            billing_failed_filament="full",
            billing_failed_energy="true",
            billing_failed_wear="true",
        )
        assert await print_charge_policy(db_session, status) == DEFAULT


async def _defer(db_session, shop, **charge) -> None:
    run = await db_session.get(PrintLogEntry, shop["run"])
    defer_print_charge(run, shop["archive"], {"run_entry_id": shop["run"], "run_status": "completed", **charge})
    await db_session.commit()


class TestAChargeWaitingForItsEnergy:
    async def test_it_is_written_with_the_energy_and_no_longer_waits(self, db_session, shop):
        await _defer(db_session, shop, wear_cost=0.25)
        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "active"

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.print_run_id) == (-1.53, "run-1")
        assert charge.description == "Print charge: Benchy (filament 1.20 + energy 0.08 + wear 0.25)"
        assert await _pending(db_session, shop["run"]) is None
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_it_is_written_once(self, db_session, shop):
        await _defer(db_session, shop)
        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        assert not await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        # Kept again by a repeated end of the same run: the run is charged already.
        await _defer(db_session, shop)
        assert not await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        assert len(await _charges(db_session)) == 1
        assert await _pending(db_session, shop["run"]) is None

    async def test_without_a_reading_it_is_written_without_energy(self, db_session, shop):
        await _defer(db_session, shop, wear_cost=0.25)

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=None)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -1.45
        assert charge.description == "Print charge: Benchy (filament 1.20 + wear 0.25; energy not available)"

    async def test_a_partial_runs_filament_usage_is_kept(self, db_session, shop):
        await _defer(db_session, shop, run_status="failed", filament_usage=(50.0, 100.0))

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -0.68
        assert "[failed: 50.0% filament (50.0g/100.0g)]" in charge.description

    async def test_the_filament_mode_it_was_kept_with_is_the_one_charged(self, db_session, shop):
        """Resolved when the print ended: a setting changed while the charge
        waits does not alter it."""
        await _defer(db_session, shop, run_status="failed", filament_usage=(50.0, 100.0), partial_filament="full")
        db_session.add(Settings(key="billing_failed_filament", value="none"))
        await db_session.commit()

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -1.28
        assert "[failed: full filament]" in charge.description

    async def test_one_already_waiting_is_kept(self, db_session, shop):
        """A second end of the same run (a reconnect closing it again) knows
        neither its queue item nor its user: it does not replace the charge."""
        await _defer(db_session, shop, charged_user_id=shop["user"], print_queue_id=77)
        kept = await _pending(db_session, shop["run"])

        await _defer(db_session, shop, charged_user_id=None, print_queue_id=None)

        assert await _pending(db_session, shop["run"]) == kept
        assert json.loads(kept)["print_queue_id"] == 77

    async def test_energy_marked_as_not_to_be_charged_is_left_out(self, db_session, shop):
        """Read from another plug than the run's start reading: not charged
        when passed, nor from the figure saved on the run (a restart)."""
        from backend.app.services.finance_billing import distrust_pending_charge_energy

        await _defer(db_session, shop)
        run = await db_session.get(PrintLogEntry, shop["run"])
        run.energy_cost = 281.4
        distrust_pending_charge_energy(run)
        await db_session.commit()

        assert await apply_pending_print_charge(db_session, shop["run"])
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -1.2
        assert charge.description == "Print charge: Benchy (filament 1.20; energy not available)"
        assert await _pending(db_session, shop["run"]) is None

    async def test_a_charge_that_fails_keeps_waiting(self, db_session, shop, monkeypatch):
        """Its error is the caller's to report; the next startup tries again."""
        from backend.app.services import finance_billing

        await _defer(db_session, shop)

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        with pytest.raises(RuntimeError):
            await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.rollback()

        assert await _pending(db_session, shop["run"]) is not None
        assert await _hold_status(db_session, shop["hold"]) == "active"


class TestARestartBeforeTheCharge:
    """The print ended and the backend stopped before its energy was read."""

    @pytest.fixture
    def sessions(self, test_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def test_startup_writes_it_and_consumes_the_reservation(self, db_session, shop, sessions):
        await _defer(db_session, shop, wear_cost=0.25)

        assert await recover_pending_print_charges(sessions) == 1

        [charge] = await _charges(db_session)
        assert charge.amount == -1.45
        assert charge.description == "Print charge: Benchy (filament 1.20 + wear 0.25; energy not available)"
        assert await _pending(db_session, shop["run"]) is None
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_energy_saved_before_the_restart_is_charged(self, db_session, shop, sessions):
        await _defer(db_session, shop)
        run = await db_session.get(PrintLogEntry, shop["run"])
        run.energy_cost = 0.08
        await db_session.commit()

        await recover_pending_print_charges(sessions)

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.description) == (-1.28, "Print charge: Benchy (filament 1.20 + energy 0.08)")

    async def test_a_second_startup_charges_nothing_more(self, db_session, shop, sessions):
        await _defer(db_session, shop)
        await recover_pending_print_charges(sessions)

        assert await recover_pending_print_charges(sessions) == 0
        assert len(await _charges(db_session)) == 1

    async def test_nothing_waiting_nothing_written(self, db_session, shop, sessions):
        assert await recover_pending_print_charges(sessions) == 0
        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "active"

    async def test_one_that_cannot_be_read_is_dropped_and_the_others_written(self, db_session, shop, sessions):
        broken = PrintLogEntry(archive_id=shop["archive"], status="completed", pending_charge="{not json")
        db_session.add(broken)
        await db_session.flush()
        broken_id = broken.id
        await _defer(db_session, shop)

        assert await recover_pending_print_charges(sessions) == 2

        assert len(await _charges(db_session)) == 1
        assert await _pending(db_session, broken_id) is None


async def test_what_is_kept_is_what_the_charge_takes(db_session, shop):
    await _defer(db_session, shop, charged_user_id=shop["user"], base_cost_override=2.0, filament_usage=(1.0, 2.0))

    kept = json.loads(await _pending(db_session, shop["run"]))
    assert kept == {
        "archive_id": shop["archive"],
        "run_entry_id": shop["run"],
        "run_status": "completed",
        "charged_user_id": shop["user"],
        "base_cost_override": 2.0,
        "filament_usage": [1.0, 2.0],
    }


class TestStartupWiring:
    """The lifespan is too large to run here (printers, MQTT, schedulers), so
    its call is read from the source, as test_oidc_env_startup.py does. What
    the call does is covered above."""

    @staticmethod
    def _source() -> str:
        import inspect

        from backend.app.main import lifespan

        return inspect.getsource(lifespan)

    async def test_startup_settles_the_charges_a_restart_interrupted(self):
        source = self._source()
        call = source.index("recover_pending_print_charges(async_session")
        assert source[source.rindex("\n", 0, call) + 1 : call].strip().endswith("await")

    async def test_after_the_migrations_and_before_printers_connect(self):
        """The column must exist, and a printer reporting an end again must
        find the interrupted charge already written."""
        source = self._source()
        call = source.index("recover_pending_print_charges(async_session")
        assert source.index("await init_db()") < call < source.index("init_printer_connections(")


class TestAWaitingChargeWhoseRunIsDeleted:
    """The charge is kept on the run row: whatever deletes the row writes the
    charge first."""

    async def test_the_charge_is_written_before_the_row_goes(self, db_session, shop):
        from backend.app.services.finance_billing import settle_pending_print_charges

        await _defer(db_session, shop, wear_cost=0.25)

        assert await settle_pending_print_charges(db_session, PrintLogEntry.archive_id == shop["archive"]) == 1
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.amount == -1.45
        assert charge.description == "Print charge: Benchy (filament 1.20 + wear 0.25; energy not available)"
        assert await _pending(db_session, shop["run"]) is None
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_only_the_runs_asked_for(self, db_session, shop):
        from backend.app.services.finance_billing import settle_pending_print_charges

        await _defer(db_session, shop)

        assert await settle_pending_print_charges(db_session, PrintLogEntry.id == shop["run"] + 1) == 0

        assert await _charges(db_session) == []
        assert await _pending(db_session, shop["run"]) is not None

    async def test_one_that_fails_is_logged_and_the_rest_of_the_transaction_kept(
        self, db_session, shop, monkeypatch, caplog, reported
    ):
        from backend.app.services import finance_billing

        await _defer(db_session, shop)

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        (await db_session.get(PrintArchive, shop["archive"])).print_name = "Renamed in the same request"
        with caplog.at_level("ERROR", logger="backend.app.services.finance_billing"):
            assert await finance_billing.settle_pending_print_charges(db_session) == 1
        await db_session.commit()

        assert "waiting print charge could not be written" in caplog.text
        assert reported == [(shop["run"], "ledger unavailable")], "the lost charge is reported, not only logged"
        db_session.expire_all()
        assert (await db_session.get(PrintArchive, shop["archive"])).print_name == "Renamed in the same request"

    @pytest.mark.parametrize("held_by", ["its archive", "its queue item", "the charge's queue item"])
    async def test_one_that_fails_gives_back_what_its_print_reserved(self, db_session, shop, monkeypatch, held_by):
        """The run goes with its charge unwritten: nothing is left to consume
        the reservation, which would count against the budget for good and
        keep the cost centre from being deleted."""
        from backend.app.services import finance_billing

        item_id = await _queue_hold(db_session, shop) if held_by != "its archive" else None
        if held_by == "its queue item":
            (await db_session.get(PrintLogEntry, shop["run"])).queue_item_id = item_id
        await _defer(db_session, shop, **({"print_queue_id": item_id} if held_by == "the charge's queue item" else {}))

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        assert await finance_billing.settle_pending_print_charges(db_session) == 1
        await db_session.commit()

        assert await _charges(db_session) == []
        assert await _hold_status(db_session, shop["hold"]) == "released"

    async def test_a_run_dropped_as_left_open_is_charged_first(self, db_session, shop):
        """A run still open on a printer when its next print starts is
        dropped: its charge, kept at its end, is written before that."""
        from backend.app.models.print_log import RUN_OPEN
        from backend.app.services.print_log import discard_open_runs

        run = await db_session.get(PrintLogEntry, shop["run"])
        run.status, run.printer_id = RUN_OPEN, 4
        await _defer(db_session, shop, wear_cost=0.25)

        [dropped] = await discard_open_runs(db_session, 4)
        await db_session.commit()

        assert dropped.id == shop["run"]
        [charge] = await _charges(db_session)
        assert (charge.amount, charge.print_run_id) == (-1.45, "run-1")
        assert await _hold_status(db_session, shop["hold"]) == "consumed"
        assert await _pending(db_session, shop["run"]) is None, "and the run is gone"

    async def test_one_that_fails_takes_back_only_its_own_writes(self, db_session, shop, monkeypatch):
        """Each settle has a savepoint: the one that fails half way leaves
        nothing behind, and the charge settled before it stays."""
        from backend.app.services import finance_billing

        second = PrintLogEntry(archive_id=shop["archive"], status="completed", billing_run_id="run-2")
        db_session.add(second)
        await db_session.flush()
        second_id = second.id
        await _defer(db_session, shop)
        defer_print_charge(second, shop["archive"], {"run_entry_id": second_id, "run_status": "completed"})
        await db_session.commit()
        charges_for_real = finance_billing.apply_print_charge_for_archive

        async def fails_half_way(db, archive_id, **charge):
            if charge["run_entry_id"] != second_id:
                return await charges_for_real(db, archive_id, **charge)
            db.add(WalletTransaction(transaction_type="print_charge", amount=-9.0, print_run_id="half-written"))
            await db.flush()
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", fails_half_way)
        assert await finance_billing.settle_pending_print_charges(db_session) == 2
        await db_session.commit()

        assert [c.print_run_id for c in await _charges(db_session)] == ["run-1"]
        assert await _pending(db_session, shop["run"]) is None
        assert await _pending(db_session, second_id) is not None

    async def test_a_charge_whose_run_is_already_gone_is_said(self, db_session, shop, caplog):
        with caplog.at_level("WARNING", logger="backend.app.services.finance_billing"):
            assert not await apply_pending_print_charge(db_session, 9999, energy_cost=0.08)

        assert "Run 9999 is gone" in caplog.text
        assert await _charges(db_session) == []


class TestAStartupRecoveryThatFails:
    @pytest.fixture
    def sessions(self, test_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def test_it_is_reported_and_the_charge_keeps_waiting(self, db_session, shop, sessions, monkeypatch):
        from backend.app.services import finance_billing

        await _defer(db_session, shop)
        reported = []

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        async def report(run, error):
            reported.append((run.id, run.archive_id, str(error)))

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        assert await recover_pending_print_charges(sessions, on_failure=report) == 0

        assert reported == [(shop["run"], shop["archive"], "ledger unavailable")]
        assert await _pending(db_session, shop["run"]) is not None
        assert await _hold_status(db_session, shop["hold"]) == "active"

    async def test_one_that_fails_at_every_start_is_reported_once(self, db_session, shop, sessions, monkeypatch):
        """Tried again at each start, and written when it can be; the alert
        is not sent again for the same charge."""
        from backend.app.services import finance_billing

        await _defer(db_session, shop, wear_cost=0.25)
        reported = []
        charges_for_real = finance_billing.apply_print_charge_for_archive

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        async def report(run, error):
            reported.append(run.id)

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        assert await recover_pending_print_charges(sessions, on_failure=report) == 0
        assert await recover_pending_print_charges(sessions, on_failure=report) == 0
        assert reported == [shop["run"]]

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", charges_for_real)
        assert await recover_pending_print_charges(sessions, on_failure=report) == 1
        [charge] = await _charges(db_session)
        assert charge.amount == -1.45
        assert await _pending(db_session, shop["run"]) is None

    async def test_a_report_that_fails_is_sent_again_at_the_next_start(self, db_session, shop, sessions, monkeypatch):
        from backend.app.services import finance_billing

        await _defer(db_session, shop)
        calls = []

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        async def report(run, error):
            calls.append(run.id)
            if len(calls) == 1:
                raise RuntimeError("no provider")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        for _ in range(3):
            assert await recover_pending_print_charges(sessions, on_failure=report) == 0
        assert len(calls) == 2

    async def test_a_report_that_fails_does_not_stop_the_others(self, db_session, shop, sessions, monkeypatch):
        from backend.app.services import finance_billing

        await _defer(db_session, shop)

        async def refuses(*args, **kwargs):
            raise RuntimeError("ledger unavailable")

        async def report(run, error):
            raise RuntimeError("no provider")

        monkeypatch.setattr(finance_billing, "apply_print_charge_for_archive", refuses)
        assert await recover_pending_print_charges(sessions, on_failure=report) == 0

    async def test_startup_reports_with_the_billing_failed_alert(self):
        """The call in the lifespan, read from its source as above."""
        assert "on_failure=_report_recovered_charge_failed" in TestStartupWiring._source()

    async def test_the_alert_is_the_one_a_prints_end_sends(self, test_engine):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app import main

        run = SimpleNamespace(id=7, printer_id=3, printer_name="P1S", print_name="Benchy", archive_id=11)
        with (
            patch.object(main, "ws_manager") as ws,
            patch.object(main, "notification_service") as notif,
            patch.object(main, "async_session", async_sessionmaker(test_engine, class_=AsyncSession)),
        ):
            ws.broadcast = AsyncMock()
            notif.on_billing_charge_failed = AsyncMock()
            await main._report_recovered_charge_failed(run, RuntimeError("ledger unavailable"))

        ws.broadcast.assert_awaited_once_with(
            {
                "type": "billing_charge_failed",
                "printer_id": 3,
                "printer_name": "P1S",
                "filename": "Benchy",
                "archive_id": 11,
            }
        )
        assert notif.on_billing_charge_failed.await_args.args[:5] == (3, "P1S", "Benchy", 11, "ledger unavailable")


async def _queue_hold(db_session, shop) -> int:
    """The reservation as a queued print holds it: by its queue item, whose
    id is returned. The item is one of a library file's print, which an
    archive deleted in the test does not take with it (foreign key)."""
    from backend.app.models.print_queue import PrintQueueItem

    item = PrintQueueItem(status="completed", position=1)
    db_session.add(item)
    await db_session.flush()
    hold = await db_session.get(BudgetReservation, shop["hold"])
    hold.source_type, hold.source_id = "print_queue", item.id
    await db_session.commit()
    return item.id


class TestAWaitingChargeWhoseArchiveChanged:
    """The charge is the run's: written for the archive the run points at
    when it is written, not the one it was kept for."""

    async def test_it_is_written_for_the_archive_the_run_is_on_now(self, db_session, shop):
        """A merge moved the run to another archive of the same file."""
        await _defer(db_session, shop)
        survivor = PrintArchive(
            filename="benchy.3mf",
            file_path="archives/test/benchy-first.3mf",
            file_size=1,
            print_name="Benchy",
            status="completed",
            cost=1.2,
            created_by_id=shop["user"],
        )
        db_session.add(survivor)
        await db_session.flush()
        survivor_id = survivor.id
        (await db_session.get(PrintLogEntry, shop["run"])).archive_id = survivor_id
        await db_session.commit()

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.print_archive_id, charge.amount) == (survivor_id, -1.28)

    @pytest.mark.parametrize("queue_item_known_by", ["the charge", "the run"])
    async def test_one_whose_archive_is_gone_is_reported_and_stops_waiting(
        self, db_session, shop, reported, queue_item_known_by
    ):
        """Nothing is left to charge: not cleared in silence, and the
        reservation is not held for good. It lost its archive with the
        archive (SET NULL), and is found by its queue item."""
        item_id = await _queue_hold(db_session, shop)
        if queue_item_known_by == "the run":
            (await db_session.get(PrintLogEntry, shop["run"])).queue_item_id = item_id
        await _defer(db_session, shop, **({"print_queue_id": item_id} if queue_item_known_by == "the charge" else {}))
        await db_session.execute(delete(PrintArchive).where(PrintArchive.id == shop["archive"]))
        await db_session.commit()

        assert not await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        assert await _charges(db_session) == []
        [(run_id, error)] = reported
        assert run_id == shop["run"] and "archive was deleted" in error
        assert await _pending(db_session, shop["run"]) is None
        assert await _hold_status(db_session, shop["hold"]) == "released"

    async def test_startup_reports_it_once(self, db_session, shop, reported, test_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        await _defer(db_session, shop, print_queue_id=await _queue_hold(db_session, shop))
        await db_session.execute(delete(PrintArchive).where(PrintArchive.id == shop["archive"]))
        await db_session.commit()

        assert await recover_pending_print_charges(sessions) == 1
        assert await recover_pending_print_charges(sessions) == 0

        assert len(reported) == 1
        assert await _hold_status(db_session, shop["hold"]) == "released"


class TestAWaitingChargeWhoseQueueItemOrCostCentreIsGone:
    """Deleted while the charge waited for its energy. The wallet transaction
    points at both, and PostgreSQL refused it for either (foreign key), at
    every retry and every startup; SQLite wrote the ids of rows that are not
    there."""

    async def test_a_queue_item_that_is_gone_is_left_out(self, db_session, shop):
        await _defer(db_session, shop, print_queue_id=4242)

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.print_queue_id) == (-1.28, None)
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_a_queue_item_that_is_still_there_is_kept(self, db_session, shop):
        from backend.app.models.print_queue import PrintQueueItem

        item = PrintQueueItem(archive_id=shop["archive"], status="completed", position=1)
        db_session.add(item)
        await db_session.flush()
        item_id = item.id
        await _defer(db_session, shop, print_queue_id=item_id)

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.print_queue_id == item_id

    @pytest.mark.parametrize("cost_centre", ["the run's", "none"])
    async def test_a_queued_print_whose_queue_item_is_gone_is_charged_as_with_it(self, db_session, shop, cost_centre):
        """A queued print nobody queued (login off) is charged to its cost
        centre without a user, and with no cost centre to nobody. Without its
        queue item it is not the archive's creator and cost centre who pay:
        they are another print's when the archive is printed again."""
        from backend.app.models.print_queue import PrintQueueItem

        item = PrintQueueItem(archive_id=shop["archive"], status="completed", position=1)
        db_session.add(item)
        await db_session.flush()
        run = await db_session.get(PrintLogEntry, shop["run"])
        lab = run.cost_center_id
        if cost_centre == "none":
            run.cost_center_id = None
        await _defer(db_session, shop, charged_user_id=None, print_queue_id=item.id)
        await db_session.delete(item)
        await db_session.commit()

        charged = await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        assert await _pending(db_session, shop["run"]) is None
        if cost_centre == "none":
            assert not charged and await _charges(db_session) == []
            return
        [charge] = await _charges(db_session)
        assert (charge.amount, charge.user_id, charge.cost_center_id) == (-1.28, None, lab)
        assert charge.print_queue_id is None
        assert await _hold_status(db_session, shop["hold"]) == "consumed"

    async def test_a_cost_centre_that_is_gone_is_left_out(self, db_session, shop):
        """Neither the one kept with the charge nor the run's own: charged to
        the archive's."""
        run = await db_session.get(PrintLogEntry, shop["run"])
        lab = run.cost_center_id
        run.cost_center_id = 9898
        await _defer(db_session, shop, cost_center_id=9898)

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert (charge.amount, charge.cost_center_id) == (-1.28, lab)
        db_session.expire_all()
        assert (await db_session.get(PrintLogEntry, shop["run"])).cost_center_id is None

    async def test_a_cost_centre_that_is_still_there_is_kept(self, db_session, shop):
        other = CostCenter(name="Workshop", is_active=True, is_private=False)
        db_session.add(other)
        await db_session.flush()
        other_id = other.id
        await _defer(db_session, shop, cost_center_id=other_id)

        assert await apply_pending_print_charge(db_session, shop["run"], energy_cost=0.08)
        await db_session.commit()

        [charge] = await _charges(db_session)
        assert charge.cost_center_id == other_id


class TestTheAlertForAChargeLostAfterItsPrintsEnd:
    async def test_it_is_the_one_a_prints_end_sends(self, monkeypatch, test_engine):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from backend.app.services import finance_billing

        monkeypatch.undo()  # the real alert, not the recorder above
        tasks = []
        run = SimpleNamespace(id=7, printer_id=3, printer_name="P1S", print_name="Benchy", archive_id=11)
        with (
            patch("backend.app.core.websocket.ws_manager.broadcast", AsyncMock()) as broadcast,
            patch(
                "backend.app.services.notification_service.notification_service.on_billing_charge_failed", AsyncMock()
            ) as notify,
            patch("backend.app.core.database.async_session", async_sessionmaker(test_engine, class_=AsyncSession)),
            patch(
                "backend.app.core.tasks.spawn_background_task",
                side_effect=lambda coro, name=None: tasks.append(asyncio.ensure_future(coro)),
            ),
        ):
            await finance_billing.report_print_charge_failed(run, RuntimeError("ledger unavailable"))
            await asyncio.gather(*tasks)

        broadcast.assert_awaited_once_with(
            {
                "type": "billing_charge_failed",
                "printer_id": 3,
                "printer_name": "P1S",
                "filename": "Benchy",
                "archive_id": 11,
            }
        )
        assert notify.await_args.args[:5] == (3, "P1S", "Benchy", 11, "ledger unavailable")
