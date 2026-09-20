"""The one answer both inventory modes give when a tag is already taken.

Linking an RFID tag lives in two routes -- ``inventory.py`` for the built-in
inventory and ``spoolman_inventory.py`` for Spoolman mode -- and they used to
refuse a duplicate with two different sentences, only one of which named the
spool holding the tag (#3110). A client cannot act on prose, so both now raise
the structured detail built here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

# Which identifier collided: separate columns on a built-in spool, separate
# lengths inside Spoolman's ``extra.tag``. A client that offers to move the tag
# needs to know which of the two it is moving.
TagField = Literal["tag_uid", "tray_uuid"]

_FIELD_LABELS: dict[TagField, str] = {"tag_uid": "Tag UID", "tray_uuid": "Tray UUID"}


def tag_already_linked(field: TagField, holder_id: int) -> HTTPException:
    """409 naming the active spool that already carries this tag.

    The frontend renders the user-facing message via i18n on ``code``;
    ``message`` is an English fallback for non-UI clients (curl / scripts).
    ``holder_id`` is what lets a caller offer to move the tag rather than only
    report that it is taken.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "tag_already_linked",
            "message": f"{_FIELD_LABELS[field]} is already linked to spool {holder_id}",
            "spool_id": holder_id,
            "field": field,
        },
    )
