"""Comma-separated id lists for the bulk endpoints that serve page-wide requests."""

from fastapi import HTTPException

# Enough for any farm's Printers page in one request; the frontend batcher
# splits anything longer.
MAX_IDS = 500


def parse_id_list(value: str) -> list[int]:
    """Parse ``"3,1,3"`` into ``[3, 1]``: ids in first-seen order, duplicates dropped.

    Raises 422 on anything that isn't a positive integer, or more than
    ``MAX_IDS`` ids.
    """
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        # isdigit() alone also passes "²" (which int() then rejects) and other
        # scripts' digits
        if not (part.isascii() and part.isdigit()) or int(part) <= 0:
            raise HTTPException(422, f"Invalid id: {part!r}")
        if int(part) not in ids:
            ids.append(int(part))
    if len(ids) > MAX_IDS:
        raise HTTPException(422, f"At most {MAX_IDS} ids per request")
    return ids
