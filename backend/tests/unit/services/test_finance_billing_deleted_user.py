"""A print still running when its user is deleted is billed to its cost centre.

At completion neither the queue row's billing user nor the archive's creator is
left, and the charge returned "has no creator ID" before it released the budget
reservation: the cost centre's reservation stayed active for good and its ledger
missed the print. It is now charged to its cost centre without a user (shown
as "No user", as is a print that never had one with login off), also the
user's own private one their personal prints go through. Only a print with no cost centre at all has nobody to charge. Deleting
the user together with their items releases what their prints still held.
"""

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.finance import BudgetReservation, CostCenter, WalletTransaction
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.finance_billing import apply_print_charge_for_archive
from backend.tests.unit.services.test_finance_service_billing import enable_billing

pytestmark = pytest.mark.asyncio


async def _orphaned_print(db_session, *, cost_center: bool):
    """A completed print whose user was deleted while it ran: no creator, no
    billing user, and an active reservation from its dispatch."""
    await enable_billing(db_session)
    center = None
    if cost_center:
        center = CostCenter(name="Lab", is_active=True, is_private=False)
        db_session.add(center)
        await db_session.flush()
    archive = PrintArchive(
        filename="part.3mf",
        file_path="archives/test/part.3mf",
        file_size=1,
        status="completed",
        cost=5.0,
        created_by_id=None,
        cost_center_id=center.id if center else None,
    )
    db_session.add(archive)
    await db_session.flush()
    item = PrintQueueItem(
        archive_id=archive.id,
        cost_center_id=center.id if center else None,
        estimated_cost=5.0,
        position=1,
        status="printing",
        created_by_id=None,
        billing_run_id="run-of-a-deleted-user",
    )
    db_session.add(item)
    await db_session.flush()
    reservation = None
    if center:  # budgets, and so reservations, exist per cost centre only
        reservation = BudgetReservation(
            cost_center_id=center.id,
            amount=5.0,
            status="active",
            source_type="print_queue",
            source_id=item.id,
            print_archive_id=archive.id,
        )
        db_session.add(reservation)
    await db_session.commit()
    return archive, item, reservation, center


async def _charge(db_session, archive, item, center):
    return await apply_print_charge_for_archive(
        db_session,
        archive.id,
        charged_user_id=None,
        cost_center_id=center.id if center else None,
        print_queue_id=item.id,
        print_run_id="run-of-a-deleted-user",
    )


async def _charges(db_session) -> list[WalletTransaction]:
    return list((await db_session.scalars(select(WalletTransaction))).all())


async def test_a_cost_centre_print_of_a_deleted_user_is_charged_to_the_cost_centre(db_session):
    archive, item, reservation, center = await _orphaned_print(db_session, cost_center=True)

    assert await _charge(db_session, archive, item, center) is True
    await db_session.commit()

    (charge,) = await _charges(db_session)
    assert charge.user_id is None
    assert charge.cost_center_id == center.id
    assert charge.amount == -5.0
    assert charge.balance_after == -5.0
    assert charge.print_archive_id == archive.id
    await db_session.refresh(reservation)
    assert reservation.status == "consumed"


async def test_a_personal_print_of_a_deleted_user_charges_nobody(db_session):
    """No cost centre at all (started while billing was off): nobody is left to
    charge, and it holds no reservation."""
    archive, item, _reservation, center = await _orphaned_print(db_session, cost_center=False)

    assert await _charge(db_session, archive, item, center) is False
    await db_session.commit()

    assert await _charges(db_session) == []


async def test_the_same_run_is_not_charged_twice(db_session):
    archive, item, _reservation, center = await _orphaned_print(db_session, cost_center=True)
    await _charge(db_session, archive, item, center)
    await db_session.commit()

    assert await _charge(db_session, archive, item, center) is False
    await db_session.commit()

    assert len(await _charges(db_session)) == 1


async def test_a_private_cost_centre_of_a_deleted_user_is_charged(db_session):
    """With billing on a "personal" print goes through the user's own private
    cost centre, which stays after the user (its owner set to none)."""
    from backend.app.models.finance import CostCenter

    archive, item, reservation, center = await _orphaned_print(db_session, cost_center=True)
    private = await db_session.get(CostCenter, center.id)
    private.is_private = True
    private.owner_user_id = None
    await db_session.commit()

    assert await _charge(db_session, archive, item, center) is True
    await db_session.commit()

    (charge,) = await _charges(db_session)
    assert charge.user_id is None and charge.cost_center_id == center.id
    await db_session.refresh(reservation)
    assert reservation.status == "consumed"


async def test_deleting_a_user_with_their_items_releases_their_reservations(async_client, db_session):
    """Their archives and queue items are deleted, so no completion can settle
    the reservations (it finds no archive): they held budget for good, and the
    cost centre could not be deleted."""
    from backend.app.models.user import User

    user = User(username="leaver", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    archive, item, reservation, _center = await _orphaned_print(db_session, cost_center=True)
    archive.created_by_id = user.id
    item.created_by_id = user.id
    item.status = "pending"  # not printing yet: it goes, and so does what it held
    user_id, reservation_id = user.id, reservation.id
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{user_id}", params={"delete_items": "true"})
    assert response.status_code in (200, 204), response.text

    db_session.expire_all()
    reservation = await db_session.get(BudgetReservation, reservation_id)
    assert reservation.status == "released"
    assert reservation.released_at is not None


async def test_a_reprint_by_a_deleted_user_is_not_put_on_the_archives_owner(db_session):
    """A queue print's user is the one who queued it. A reprint reuses the
    source archive, whose creator is someone else: that owner did not print it."""
    from backend.app.models.user import User

    owner = User(username="archive_owner", password_hash="x", role="user")
    db_session.add(owner)
    await db_session.commit()
    archive, item, _reservation, center = await _orphaned_print(db_session, cost_center=True)
    archive.created_by_id = owner.id  # the source archive; the queue row's user was deleted
    await db_session.commit()

    assert await _charge(db_session, archive, item, center) is True
    await db_session.commit()

    (charge,) = await _charges(db_session)
    assert charge.user_id is None


async def test_deleting_a_user_with_their_items_keeps_a_print_still_running(async_client, db_session):
    """A running reprint of another user's archive: its queue row went, and the
    completion then charged the archive's owner, who never printed it. Running,
    it is detached (no user) instead, and billed to its cost centre as such."""
    from backend.app.models.user import User

    owner = User(username="archive_owner", password_hash="x", role="user")
    leaver = User(username="leaver", password_hash="x", role="user")
    db_session.add_all([owner, leaver])
    await db_session.commit()
    archive, item, reservation, center = await _orphaned_print(db_session, cost_center=True)
    archive.created_by_id = owner.id  # the source archive of the reprint
    item.created_by_id = leaver.id  # printing
    leaver_id, item_id, reservation_id = leaver.id, item.id, reservation.id
    archive_id, center_id = archive.id, center.id
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{leaver_id}", params={"delete_items": "true"})
    assert response.status_code in (200, 204), response.text

    db_session.expire_all()
    kept = await db_session.get(PrintQueueItem, item_id)
    assert kept is not None and kept.created_by_id is None
    assert (await db_session.get(BudgetReservation, reservation_id)).status == "active"

    archive = await db_session.get(PrintArchive, archive_id)
    center = await db_session.get(CostCenter, center_id)
    assert await _charge(db_session, archive, kept, center) is True
    await db_session.commit()
    (charge,) = await _charges(db_session)
    assert charge.user_id is None
    assert (await db_session.get(BudgetReservation, reservation_id)).status == "consumed"


async def test_deleting_a_user_with_their_items_keeps_their_own_archive_still_printing(async_client, db_session):
    """Their own archive of a running print: deleted, the completion found no
    archive and the print went unbilled. Detached, it is billed as "No user"."""
    from backend.app.models.user import User

    leaver = User(username="leaver", password_hash="x", role="user")
    db_session.add(leaver)
    await db_session.commit()
    archive, item, _reservation, _center = await _orphaned_print(db_session, cost_center=True)
    archive.status = "printing"
    archive.created_by_id = leaver.id
    item.created_by_id = leaver.id
    leaver_id, archive_id = leaver.id, archive.id
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{leaver_id}", params={"delete_items": "true"})
    assert response.status_code in (200, 204), response.text

    db_session.expire_all()
    kept = await db_session.get(PrintArchive, archive_id)
    assert kept is not None and kept.created_by_id is None


async def test_a_print_whose_cost_comes_out_as_zero_releases_its_reservation(db_session):
    """An older or imported archive with grams but no stored cost is reserved
    from the estimate; at completion its cost is none, and the charge returned
    before releasing the reservation, which then held budget for good."""
    archive, item, reservation, center = await _orphaned_print(db_session, cost_center=True)
    archive.cost = None
    reservation_id = reservation.id
    await db_session.commit()

    assert await _charge(db_session, archive, item, center) is False
    await db_session.commit()

    assert await _charges(db_session) == []
    db_session.expire_all()
    assert (await db_session.get(BudgetReservation, reservation_id)).status == "released"


async def test_deleting_a_user_with_their_items_keeps_their_archive_another_user_is_printing(async_client, db_session):
    """Another user's reprint of their archive, dispatched (queue row printing)
    before the printer's start marked the archive printing: deleted, the archive
    took that user's queue row with it and their print was never billed."""
    from backend.app.models.user import User

    leaver = User(username="leaver", password_hash="x", role="user")
    printer_user = User(username="reprinter", password_hash="x", role="user")
    db_session.add_all([leaver, printer_user])
    await db_session.commit()
    archive, item, reservation, _center = await _orphaned_print(db_session, cost_center=True)
    archive.created_by_id = leaver.id  # still "completed": the start has not come yet
    item.created_by_id = printer_user.id  # their row, printing
    leaver_id, archive_id, item_id, reservation_id = leaver.id, archive.id, item.id, reservation.id
    printer_user_id = printer_user.id
    await db_session.commit()

    response = await async_client.delete(f"/api/v1/users/{leaver_id}", params={"delete_items": "true"})
    assert response.status_code in (200, 204), response.text

    db_session.expire_all()
    kept = await db_session.get(PrintArchive, archive_id)
    assert kept is not None and kept.created_by_id is None
    assert (await db_session.get(PrintQueueItem, item_id)).created_by_id == printer_user_id
    assert (await db_session.get(BudgetReservation, reservation_id)).status == "active"


async def test_a_billing_user_deleted_just_before_the_charge_counts_as_none(db_session):
    """The completion read the queue row's user, then the user was deleted
    before the charge (seconds of usage and archive work in between): the id
    names nobody. PostgreSQL refused the wallet for it (FK) and the reservation
    stayed active; SQLite wrote a wallet and a charge for a missing user."""
    from backend.app.models.finance import UserWallet

    archive, item, reservation, center = await _orphaned_print(db_session, cost_center=True)
    reservation_id = reservation.id
    gone_user_id = 987654  # no such user any more

    assert (
        await apply_print_charge_for_archive(
            db_session,
            archive.id,
            charged_user_id=gone_user_id,
            cost_center_id=center.id,
            print_queue_id=item.id,
            print_run_id="run-of-a-deleted-user",
        )
        is True
    )
    await db_session.commit()

    (charge,) = await _charges(db_session)
    assert charge.user_id is None
    assert await db_session.scalar(select(UserWallet).where(UserWallet.user_id == gone_user_id)) is None
    db_session.expire_all()
    assert (await db_session.get(BudgetReservation, reservation_id)).status == "consumed"
