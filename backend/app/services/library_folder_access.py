"""Who sees which library folder, and who may write into it (#3201).

A folder has an owner (``created_by_id``, the user who made it) and can be
marked ``shared`` by an admin. A user with ``library:read_all`` (or any
caller when auth is off) sees every folder. An API key is passed in as its
``ApiKeyActor`` and treated as its owner within its scopes. A user with only
``library:read_own`` sees:

  - folders they own,
  - shared folders,
  - folders holding one of their own files,
  - and the parents needed to reach any of those, for navigation only.

They may write into (upload, extract, move files, create subfolders in)
their own folders and shared ones, plus the root. Everything else is hidden:
a folder they can't see answers 404, exactly like another user's file.

A folder made without a user (auth off, a key without an owner) is created shared, so
switching auth on later doesn't hide it. Folders from before #3201 got an
owner or the shared flag from the upgrade backfill in ``core/database.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.permissions import Permission
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User

if TYPE_CHECKING:
    from backend.app.core.auth import ApiKeyActor


def sees_all_folders(user: User | ApiKeyActor | None) -> bool:
    """True for ``library:read_all``, and for ``None`` (auth off)."""
    return user is None or user.has_permission(Permission.LIBRARY_READ_ALL.value)


def owns(folder: LibraryFolder, user: User | ApiKeyActor | None) -> bool:
    return user is not None and folder.created_by_id is not None and folder.created_by_id == user.id


def can_write_folder(folder: LibraryFolder, user: User | ApiKeyActor | None) -> bool:
    """May ``user`` put files or subfolders into ``folder``."""
    return sees_all_folders(user) or owns(folder, user) or bool(folder.shared)


def can_rename_folder(folder: LibraryFolder, user: User | ApiKeyActor | None) -> bool:
    if user is None or user.has_permission(Permission.LIBRARY_UPDATE_ALL.value):
        return True
    return user.has_permission(Permission.LIBRARY_UPDATE_OWN.value) and owns(folder, user)


@dataclass
class FolderIndex:
    """Every folder plus who owns the files in it, loaded in two queries.

    ``file_owners`` counts trashed files too: a folder delete cascades to
    them, so they decide who may delete it just like live files do.
    """

    folders: dict[int, LibraryFolder] = field(default_factory=dict)
    children: dict[int | None, list[int]] = field(default_factory=dict)
    file_owners: dict[int, set[int | None]] = field(default_factory=dict)
    live_file_owners: dict[int, set[int | None]] = field(default_factory=dict)

    def subtree(self, folder_id: int) -> list[int]:
        """``folder_id`` and every folder below it."""
        out: list[int] = []
        seen: set[int] = set()
        stack = [folder_id]
        while stack:
            fid = stack.pop()
            if fid in seen:  # a parent_id loop must not hang the request
                continue
            seen.add(fid)
            out.append(fid)
            stack.extend(self.children.get(fid, []))
        return out


async def load_folder_index(db: AsyncSession, *, with_files: bool = True) -> FolderIndex:
    """Every folder, and with ``with_files`` who owns the files in each.

    Without the files, visibility and delete rules are only right for users
    who see everything and may delete everything.
    """
    index = FolderIndex()
    for folder in (await db.execute(select(LibraryFolder))).scalars().all():
        index.folders[folder.id] = folder
        index.children.setdefault(folder.parent_id, []).append(folder.id)
    if not with_files:
        return index
    rows = await db.execute(
        select(LibraryFile.folder_id, LibraryFile.created_by_id, LibraryFile.deleted_at.is_(None))
        .where(LibraryFile.folder_id.isnot(None))
        .distinct()
    )
    for folder_id, owner_id, live in rows.all():
        index.file_owners.setdefault(folder_id, set()).add(owner_id)
        if live:
            index.live_file_owners.setdefault(folder_id, set()).add(owner_id)
    return index


def visible_folder_ids(index: FolderIndex, user: User | ApiKeyActor | None) -> set[int] | None:
    """The folders ``user`` may see, or ``None`` for all of them."""
    if sees_all_folders(user):
        return None
    assert user is not None
    reachable = {
        fid
        for fid, folder in index.folders.items()
        if owns(folder, user) or folder.shared or user.id in index.live_file_owners.get(fid, set())
    }
    visible = set(reachable)
    for fid in reachable:
        parent = index.folders[fid].parent_id
        while parent is not None and parent not in visible and parent in index.folders:
            visible.add(parent)
            parent = index.folders[parent].parent_id
    return visible


def folder_delete_blocker(index: FolderIndex, folder: LibraryFolder, user: User | ApiKeyActor | None) -> str | None:
    """Why ``user`` may NOT delete ``folder``, or None if they may.

    ``library:delete_all`` deletes anything. With ``library:delete_own`` a user
    deletes a folder they own when everything under it, folders and files
    (trashed ones too), is theirs as well. A folder without an owner, made
    before #3201, keeps the old rule (#1781): only when it is truly empty.
    """
    if user is None or user.has_permission(Permission.LIBRARY_DELETE_ALL.value):
        return None
    if not user.has_permission(Permission.LIBRARY_DELETE_OWN.value):
        return "Deleting folders requires library:delete_own or library:delete_all"
    if folder.is_external:
        return "External folders can only be deleted by users with library:delete_all"
    if folder.project_id is not None or folder.archive_id is not None:
        return "Folders linked to a project or archive can only be deleted by users with library:delete_all"

    if folder.created_by_id is None:
        if index.children.get(folder.id):
            return "Only empty folders can be deleted without library:delete_all"
        if index.file_owners.get(folder.id):
            return "Only empty folders can be deleted without library:delete_all (the folder may contain trashed files)"
        return None

    if not owns(folder, user):
        return "Only the folder's owner can delete it without library:delete_all"
    for fid in index.subtree(folder.id):
        sub = index.folders[fid]
        if fid != folder.id and not owns(sub, user):
            return "The folder contains folders of other users; deleting it requires library:delete_all"
        if sub.is_external or sub.project_id is not None or sub.archive_id is not None:
            return "The folder contains external or linked folders; deleting it requires library:delete_all"
        if index.file_owners.get(fid, set()) - {user.id}:
            return (
                "The folder contains files of other users (the folder may contain trashed files); "
                "deleting it requires library:delete_all"
            )
    return None


async def get_visible_folder(db: AsyncSession, folder_id: int, user: User | ApiKeyActor | None) -> LibraryFolder:
    """The folder, or 404 when it doesn't exist or ``user`` can't see it."""
    folder = (await db.execute(select(LibraryFolder).where(LibraryFolder.id == folder_id))).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not sees_all_folders(user):
        visible = visible_folder_ids(await load_folder_index(db), user)
        if visible is not None and folder.id not in visible:
            raise HTTPException(status_code=404, detail="Folder not found")
    return folder


async def get_writable_folder(db: AsyncSession, folder_id: int, user: User | ApiKeyActor | None) -> LibraryFolder:
    """The folder ``user`` puts something into: 404 when unseen, 403 when only seen."""
    folder = await get_visible_folder(db, folder_id, user)
    if not can_write_folder(folder, user):
        raise HTTPException(
            status_code=403,
            detail="You can only add to your own folders and folders shared with everyone",
        )
    return folder


async def default_import_folder(db: AsyncSession, name: str, user: User | None) -> LibraryFolder:
    """The top-level folder an import lands in when none was chosen.

    The first one is created shared, so every importer's models land side by
    side as before #3201. If an admin made it private, a user who can't write
    to it gets a folder of the same name of their own instead of being
    refused.
    """
    candidates = (
        (
            await db.execute(
                select(LibraryFolder)
                .where(
                    LibraryFolder.name == name,
                    LibraryFolder.parent_id.is_(None),
                    LibraryFolder.is_external.is_(False),
                )
                .order_by(LibraryFolder.id)
            )
        )
        .scalars()
        .all()
    )
    for folder in candidates:
        if can_write_folder(folder, user):
            return folder
    folder = LibraryFolder(
        name=name,
        parent_id=None,
        created_by_id=user.id if user else None,
        shared=not candidates,
    )
    db.add(folder)
    await db.flush()
    return folder
