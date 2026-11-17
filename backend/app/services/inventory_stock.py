"""Built-in inventory stock per filament identity (#3590).

Entries are keyed like a filament override: type + colour, plus the variant
(``tray_info_idx``) only where it is needed to tell variants apart. Spoolman
mode has no built-in stock, so everything here answers ``None``.

Why the variant is rare: the tray reports its own idx after a K-profile
realign, a per-model preset, a cloud GFS resolve or a manual load, so an
override carrying an idx the tray does not report could never match. An entry
therefore carries an idx only when the active inventory holds more than one
distinct specific idx for the same type + colour; otherwise the group is one
idx-less entry per type + colour.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.services.inventory_mode import spoolman_owns_assignments
from backend.app.services.spool_identity import identity_matches, spool_override_identity
from backend.app.utils.filament_types import canonical_filament_type


@dataclass
class StockEntry:
    type: str
    color: str
    tray_info_idx: str
    subtype: str
    color_name: str
    rgba: str | None
    extra_colors: str | None
    effect_type: str | None
    spool_count: int  # active spools not known to be empty (unknown weight counts as present)
    remaining_grams: float  # unknown weights add 0 g
    empty_spool_count: int
    archived_only: bool = False  # only archived spools matched: known, none left


def _most_common(values: list[str]) -> str:
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else ""


async def inventory_stock(
    db: AsyncSession, *, include_archived: bool = False, collapse_idx: bool = True
) -> list[StockEntry] | None:
    """Stock grouped by type + colour (+ idx when variants differ); None in Spoolman mode.

    Counts and grams come from active spools only. ``include_archived`` adds
    archived-only groups (count 0) so a lookup can tell "ran out" from "never
    stocked". ``collapse_idx=False`` keeps every specific idx as its own entry,
    so a lookup for one variant does not count another's spools (#3590).
    """
    if await spoolman_owns_assignments(db):
        return None

    query = select(Spool).order_by(Spool.id)
    if not include_archived:
        query = query.where(Spool.archived_at.is_(None))
    spools = (await db.execute(query)).scalars().all()

    idents = [(s, spool_override_identity(s)) for s in spools]
    # Distinct specific idx per type + colour over the ACTIVE spools decide
    # which groups keep their idx.
    idx_seen: dict[tuple[str, str], set[str]] = {}
    for s, ident in idents:
        if ident.type and ident.tray_info_idx and s.archived_at is None:
            idx_seen.setdefault((canonical_filament_type(ident.type), ident.color), set()).add(ident.tray_info_idx)

    groups: dict[tuple[str, str, str], list[tuple[Spool, str]]] = {}
    for s, ident in idents:
        if not ident.type:
            continue
        base = (canonical_filament_type(ident.type), ident.color)
        idx = ident.tray_info_idx if not collapse_idx or len(idx_seen.get(base, ())) > 1 else ""
        groups.setdefault((*base, idx), []).append((s, ident.type.upper()))

    entries: list[StockEntry] = []
    for (_canon, color, idx), pairs in groups.items():
        members = [s for s, _ in pairs]
        # Aliases (PA-CF / PAHT-CF) share one entry; show the most common spelling.
        type_ = _most_common([t for _, t in pairs])
        active = [s for s in members if s.archived_at is None]
        count = 0
        empty = 0
        grams = 0.0
        for s in active:
            if (s.label_weight or 0) <= 0:
                count += 1  # weight unknown: present, never "none left"
                continue
            remaining = max(0.0, s.label_weight - (s.weight_used or 0))
            if remaining > 0:
                count += 1
                grams += remaining
            else:
                empty += 1
        rep = (active or members)[0]
        entries.append(
            StockEntry(
                type=type_,
                color=color,
                tray_info_idx=idx,
                subtype=_most_common([s.subtype or "" for s in members]),
                color_name=_most_common([s.color_name or "" for s in members]),
                rgba=rep.rgba,
                extra_colors=rep.extra_colors,
                effect_type=rep.effect_type,
                spool_count=count,
                remaining_grams=round(grams, 1),
                empty_spool_count=empty,
                archived_only=not active,
            )
        )
    entries.sort(key=lambda e: (e.spool_count == 0, e.type, e.color, e.tray_info_idx))
    return entries


def stock_in(entries: Iterable[StockEntry], type: str, color: str, tray_info_idx: str) -> tuple[int, float] | None:
    """(spool_count, remaining_grams) of the entries matching an override.

    None when no entry matches: the inventory never knew this filament. An
    override without idx matches every variant of its type + colour.
    """
    known = False
    count = 0
    grams = 0.0
    for e in entries:
        if identity_matches(type, color, tray_info_idx, e.type, e.color, e.tray_info_idx):
            known = True
            count += e.spool_count
            grams += e.remaining_grams
    return (count, grams) if known else None


async def stock_for(db: AsyncSession, type: str, color: str, tray_info_idx: str) -> tuple[int, float] | None:
    """Stock for one override; None = unknown (Spoolman, or never in the inventory)."""
    entries = await inventory_stock(db, include_archived=True, collapse_idx=False)
    if entries is None:
        return None
    return stock_in(entries, type, color, tray_info_idx)
