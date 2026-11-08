"""Personal access codes for virtual printers (#1430).

A slicer signs in to a printer with one shared access code, so a virtual
printer could never tell who sent a file. Each user can have a code of their
own: entered in their slicer instead of the virtual printer's code, it is the
same sign-in for the slicer, and Bambuddy knows the upload is theirs.

The code is checked at every sign-in, against what the user may do now: the
account must be active, hold the permissions the virtual printer's mode needs
and, for a virtual printer with a target printer, be allowed that printer
(#1727). So deactivating someone, removing a permission or replacing a code
takes effect at the next sign-in. Only while auth is enabled; never for proxy
mode, whose sign-in goes to the real printer.
"""

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.models.virtual_printer import VP_MODE_LIBRARY, VP_MODE_QUEUE, VP_MODE_REVIEW, normalize_vp_mode

logger = logging.getLogger(__name__)

CODE_LENGTH = 8

# What a user must be allowed to do to send to a virtual printer in a mode:
# every mode stores the file in the File Manager (review mode once approved),
# queue mode also queues it.
MODE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    VP_MODE_LIBRARY: (Permission.LIBRARY_UPLOAD.value,),
    VP_MODE_REVIEW: (Permission.LIBRARY_UPLOAD.value,),
    VP_MODE_QUEUE: (Permission.LIBRARY_UPLOAD.value, Permission.QUEUE_CREATE.value),
}


async def _code_in_use(db: AsyncSession, code: str) -> bool:
    from backend.app.models.printer import Printer
    from backend.app.models.virtual_printer import VirtualPrinter

    for column in (User.slicer_access_code, VirtualPrinter.access_code, Printer.access_code):
        if (await db.execute(select(column).where(column == code).limit(1))).first() is not None:
            return True
    return False


async def new_code(db: AsyncSession) -> str:
    """A fresh code: 8 digits like a printer's, used by no user, virtual printer or printer.

    A virtual printer checks its own code first, so a personal code equal to
    one would never name its user.
    """
    for _ in range(50):
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
        if not await _code_in_use(db, code):
            return code
    raise RuntimeError("could not find a free access code")


async def code_for(db: AsyncSession, user: User, *, renew: bool = False) -> str:
    """The user's code, made on first use or when ``renew`` (the old one stops working). Commits."""
    if user.slicer_access_code and not renew:
        return user.slicer_access_code
    code = await new_code(db)
    user.slicer_access_code = code
    await db.commit()
    logger.info("New virtual printer access code for user %s", user.id)
    return code


async def user_for_code(db: AsyncSession, code: str, *, mode: str, target_printer_id: int | None) -> User | None:
    """The user a sign-in with ``code`` stands for, or None to refuse it.

    None also when auth is off, for proxy mode, or when the user may not send
    to this virtual printer now.
    """
    from backend.app.core.auth import is_auth_enabled
    from backend.app.core.printer_scope import resolve_user_printer_scope

    needed = MODE_PERMISSIONS.get(normalize_vp_mode(mode) or "")
    if needed is None or len(code) != CODE_LENGTH or not code.isdigit():
        return None
    if target_printer_id is not None:
        # The slicer then shows that printer's live state through the VP.
        needed = (*needed, Permission.PRINTERS_READ.value)
    if not await is_auth_enabled(db):
        return None
    user = (
        await db.execute(
            select(User).where(User.slicer_access_code == code).options(selectinload(User.groups)).limit(1)
        )
    ).scalar_one_or_none()
    if user is None:
        return None
    if not user.is_active:
        logger.info("Virtual printer sign-in refused for user %s: account disabled", user.id)
        return None
    if not user.has_all_permissions(*needed):
        logger.info("Virtual printer sign-in refused for user %s: needs %s", user.id, ", ".join(needed))
        return None
    if target_printer_id is not None and not (await resolve_user_printer_scope(db, user)).allows(target_printer_id):
        logger.info("Virtual printer sign-in refused for user %s: printer %s not theirs", user.id, target_printer_id)
        return None
    return user


async def may_control_printer(db: AsyncSession, user_id: int, printer_id: int) -> bool:
    """Whether the user may send commands (stop, pause, G-code, AMS) to the printer now.

    For a slicer signed in to a virtual printer with a personal code: its
    commands reach the real printer only with printers:control on it.
    """
    from backend.app.core.auth import is_auth_enabled
    from backend.app.core.printer_scope import resolve_user_printer_scope

    if not await is_auth_enabled(db):
        return False
    user = (
        await db.execute(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not user.has_permission(Permission.PRINTERS_CONTROL.value):
        return False
    return (await resolve_user_printer_scope(db, user)).allows(printer_id)
