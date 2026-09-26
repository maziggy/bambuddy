"""``move_library_photos`` edge cases (#3077).

The happy path is pinned by the scheduler's cleanup tests; this covers what
they cannot reach — a name already taken in the destination, and a move that
fails halfway.
"""

import pytest

from backend.app.core.config import settings
from backend.app.utils.library_paths import library_photos_dir, move_library_photos


@pytest.fixture
def photo_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    destination = tmp_path / "archives" / "1" / "photos"
    source = library_photos_dir(7)
    source.mkdir(parents=True)
    return source, destination


def test_moves_every_photo_and_drops_the_directory(photo_dirs):
    source, destination = photo_dirs
    (source / "a1b2c3d4.jpg").write_bytes(b"one")
    (source / "e5f6a7b8.png").write_bytes(b"two")

    moved = move_library_photos(7, ["a1b2c3d4.jpg", "e5f6a7b8.png"], destination)

    assert moved == ["a1b2c3d4.jpg", "e5f6a7b8.png"]
    assert (destination / "a1b2c3d4.jpg").read_bytes() == b"one"
    assert not source.exists()


def test_renames_around_a_name_the_destination_already_holds(photo_dirs):
    source, destination = photo_dirs
    (source / "a1b2c3d4.jpg").write_bytes(b"library")
    destination.mkdir(parents=True)
    (destination / "a1b2c3d4.jpg").write_bytes(b"archive")

    moved = move_library_photos(7, ["a1b2c3d4.jpg"], destination)

    assert moved != ["a1b2c3d4.jpg"]
    assert moved[0].endswith(".jpg")
    assert (destination / "a1b2c3d4.jpg").read_bytes() == b"archive"
    assert (destination / moved[0]).read_bytes() == b"library"


def test_a_traversal_name_is_skipped_and_keeps_the_directory(photo_dirs):
    # A stored name is uuid-generated, so this only happens to a row someone
    # has written to by hand — but the photos are then left alone rather than
    # swept away by a cleanup that could not move them.
    source, destination = photo_dirs
    (source / "a1b2c3d4.jpg").write_bytes(b"one")

    moved = move_library_photos(7, ["../escape.jpg", "a1b2c3d4.jpg"], destination)

    assert moved == ["a1b2c3d4.jpg"]
    assert source.is_dir()


def test_a_file_without_photos_is_a_no_op(photo_dirs):
    source, destination = photo_dirs
    source.rmdir()

    assert move_library_photos(7, [], destination) == []
    assert not destination.exists()
