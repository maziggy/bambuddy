"""Integration tests for the /gcode-viewer static-file routes.

Covers two behaviours added by the GCode viewer PR:

1. Route ordering — /gcode-viewer/* is served by explicit @app.get routes
   that are registered before the /{full_path:path} SPA catch-all, so the
   GCode viewer is never accidentally served the React app HTML.

2. Path-traversal guard — requests for paths that escape gcode_viewer/
   (e.g. /gcode-viewer/../main.py) must return 403, not the file contents.

Plus tests for the archive G-code endpoint behaviour the viewer depends on:
``?plate=N`` resolution including zero-padded filenames, and the ``has_gcode``
flag on the plates endpoint that gates the frontend plate picker.
"""

import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient


class TestGCodeViewerRouteOrdering:
    """Verify the /gcode-viewer routes are reachable and distinct from the SPA."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gcode_viewer_index_does_not_fall_through_to_spa(self, async_client: AsyncClient):
        """GET /gcode-viewer/ must not return the React SPA index.html.

        If route ordering is broken the SPA catch-all returns 200 with
        Content-Type: text/html and a <div id="root"> body.  The correct
        response is either 200 (gcode_viewer/index.html present) or 404
        (directory absent in CI) — never the SPA shell.
        """
        response = await async_client.get("/gcode-viewer/")
        # 200 or 404 are both acceptable depending on whether gcode_viewer/
        # exists in the test environment; the SPA catch-all always returns 200.
        assert response.status_code in (200, 404)
        # If a body came back it must NOT be the React SPA shell.
        assert b'<div id="root">' not in response.content

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gcode_viewer_no_trailing_slash_falls_through_to_spa(self, async_client: AsyncClient):
        """GET /gcode-viewer (no trailing slash) must fall through to the SPA.

        Only /gcode-viewer/ (trailing slash) should serve the raw viewer — that
        form is what the iframe in GCodeViewerPage requests. The bare path is
        the SPA route the user navigates to; reloading it must re-enter the
        React layout rather than serve the iframe contents standalone.
        """
        response = await async_client.get("/gcode-viewer", follow_redirects=False)
        # SPA catch-all serves 200 with the React index.html (which contains
        # <div id="root">). If the build output isn't present the catch-all
        # may 404 — both outcomes are acceptable here; the key invariant is
        # that we do NOT serve the standalone PrettyGCode index.html (which
        # starts with <!doctype html> and contains "PrettyGCode").
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            assert b"PrettyGCode" not in response.content


class TestGCodeViewerPathTraversal:
    """Verify the path-traversal guard on /gcode-viewer/{file_path:path}.

    HTTP clients (and servers) normalise plain `..` segments before the
    request reaches a route handler, so `/gcode-viewer/../x` becomes `/x`
    and hits the SPA catch-all rather than our guard — that normalisation is
    itself a defence layer.  The actual at-risk form is URL-encoded dots
    (`%2E%2E`) which survive normalisation and land in {file_path:path} as
    the literal string `../x`.  We test that form here.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_encoded_dotdot_traversal_is_forbidden(self, async_client: AsyncClient):
        """GET /gcode-viewer/%2E%2E/main.py must return 403.

        %2E%2E URL-decodes to .. which is not normalised away by httpx/
        Starlette, so it reaches _gcode_viewer_response as '../main.py'.
        Path.is_relative_to(gcode_viewer_dir) then blocks it with 403.
        """
        response = await async_client.get("/gcode-viewer/%2E%2E/main.py")
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_encoded_nested_dotdot_traversal_is_forbidden(self, async_client: AsyncClient):
        """GET /gcode-viewer/js/%2E%2E/%2E%2E/main.py must return 403."""
        response = await async_client.get("/gcode-viewer/js/%2E%2E/%2E%2E/main.py")
        assert response.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_nonexistent_safe_path_returns_404(self, async_client: AsyncClient):
        """A safe but nonexistent path returns 404, not 403."""
        response = await async_client.get("/gcode-viewer/does-not-exist.js")
        assert response.status_code == 404


def _write_3mf(
    path: Path,
    plate_gcode: dict[int, str] | None = None,
    plate_filenames: dict[int, str] | None = None,
    include_png_for: list[int] | None = None,
) -> None:
    """Write a synthetic Bambu-style 3MF zip at *path*.

    Parameters let a single test pin one specific shape:

    - ``plate_gcode`` — {plate_index: gcode_text} written at
      ``Metadata/plate_{index}.gcode``. Use for the normal (sliced) case.
    - ``plate_filenames`` — {plate_index: custom_filename} written with the
      raw filename verbatim. Use for zero-padded names (plate_01.gcode) etc.
    - ``include_png_for`` — plate indices to add PNG stubs for. Use to
      simulate source-only archives (PNG/JSON present, no .gcode).

    Leaving all three empty produces an archive that the plates endpoint
    will parse as empty (no plates).
    """
    plate_gcode = plate_gcode or {}
    plate_filenames = plate_filenames or {}
    include_png_for = include_png_for or []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, text in plate_gcode.items():
            zf.writestr(f"Metadata/plate_{idx}.gcode", text)
        for idx, filename in plate_filenames.items():
            zf.writestr(f"Metadata/{filename}", f"; stub for plate {idx}\n")
        for idx in include_png_for:
            zf.writestr(f"Metadata/plate_{idx}.png", b"\x89PNG\r\n\x1a\n")
            zf.writestr(f"Metadata/plate_{idx}.json", b'{"bbox_objects": []}')


@pytest.fixture
def _patch_archive_base_dir(monkeypatch, tmp_path):
    """Point archive file_path resolution at *tmp_path* for this test."""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    return tmp_path


class TestArchiveGcodePlateParam:
    """The viewer passes ``?plate=N`` for multi-plate archives."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plate_param_returns_that_plate(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """GET /archives/{id}/gcode?plate=2 returns Metadata/plate_2.gcode."""
        tmp = _patch_archive_base_dir
        threemf = tmp / "multi.3mf"
        _write_3mf(
            threemf,
            plate_gcode={1: "G0 ; plate 1\n", 2: "G1 X0 Y0 ; plate 2\n"},
        )
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="multi.3mf", file_path="multi.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/gcode?plate=2")

        assert response.status_code == 200
        assert "plate 2" in response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plate_param_zero_padded_filename_resolves(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """plate_01.gcode reports as plate 1 from /plates — /gcode?plate=1 must find it.

        Regression: the original exact-string match on ``Metadata/plate_1.gcode``
        missed zero-padded filenames exported by some slicers, so the picker
        showed plate 1 as selectable but the viewer 404'd on selection.
        """
        tmp = _patch_archive_base_dir
        threemf = tmp / "padded.3mf"
        with zipfile.ZipFile(threemf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Metadata/plate_01.gcode", "G0 ; padded plate\n")
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="padded.3mf", file_path="padded.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/gcode?plate=1")

        assert response.status_code == 200
        assert "padded plate" in response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_missing_plate_returns_404(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """Requesting a plate index the archive doesn't contain returns 404."""
        tmp = _patch_archive_base_dir
        threemf = tmp / "only_plate_2.3mf"
        _write_3mf(threemf, plate_gcode={2: "G0\n"})
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="only_plate_2.3mf", file_path="only_plate_2.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/gcode?plate=1")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_plate_param_returns_first_plate(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """Omitting ?plate falls back to the first gcode in the archive.

        Preserves the pre-plate-param behaviour — existing callers that don't
        know about plates still get something sensible back.
        """
        tmp = _patch_archive_base_dir
        threemf = tmp / "single.3mf"
        _write_3mf(threemf, plate_gcode={1: "G0 ; only plate\n"})
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="single.3mf", file_path="single.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/gcode")

        assert response.status_code == 200
        assert "only plate" in response.text

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plate_param_rejects_zero_and_negative(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """``?plate=0`` or negative must 400 — not silently fall through."""
        tmp = _patch_archive_base_dir
        threemf = tmp / "any.3mf"
        _write_3mf(threemf, plate_gcode={1: "G0\n"})
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="any.3mf", file_path="any.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/gcode?plate=0")

        assert response.status_code == 400


class TestArchivePlatesHasGcode:
    """The ``has_gcode`` flag on /plates gates the frontend plate picker."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_has_gcode_true_when_gcode_files_present(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """Sliced multi-plate 3MF → has_gcode=true."""
        tmp = _patch_archive_base_dir
        threemf = tmp / "sliced.3mf"
        _write_3mf(threemf, plate_gcode={1: "G0\n", 2: "G1\n"})
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="sliced.3mf", file_path="sliced.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/plates")

        assert response.status_code == 200
        data = response.json()
        assert data["has_gcode"] is True

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_has_gcode_false_for_source_only_archive(
        self,
        async_client: AsyncClient,
        archive_factory,
        printer_factory,
        _patch_archive_base_dir,
    ):
        """Source-only 3MF (PNG/JSON only, no gcode) → has_gcode=false.

        Regression for the archive-69 bug: the PNG/JSON fallback path made the
        plates endpoint report plate indices that the gcode endpoint couldn't
        actually serve, so every viewer preview 404'd. The frontend now uses
        has_gcode to suppress the picker + show a toast instead.
        """
        tmp = _patch_archive_base_dir
        threemf = tmp / "project.3mf"
        _write_3mf(threemf, include_png_for=[1, 2, 3])  # no .gcode at all
        printer = await printer_factory()
        archive = await archive_factory(printer.id, filename="project.3mf", file_path="project.3mf")

        response = await async_client.get(f"/api/v1/archives/{archive.id}/plates")

        assert response.status_code == 200
        data = response.json()
        assert data["has_gcode"] is False
        # The endpoint still reports plates (from JSON/PNG) — the flag is what
        # the frontend keys on, not an empty plate list.
        assert len(data["plates"]) == 3
