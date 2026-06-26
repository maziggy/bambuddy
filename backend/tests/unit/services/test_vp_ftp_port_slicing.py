"""Tests for the per-VP FTP passive-port slice helper (#1646).

Each VP is allocated a non-overlapping 10-port slice from the
PASSIVE_PORT_BASE pool. Bridge-mode Docker users only have to expose
`PASSIVE_SLICE_SIZE * N_vps` ports instead of the historical 1001-port
pool that spawned ~2000 docker-proxy host processes (~3.5 GB RAM).

Slicing properties pinned here:
  - Slice 0 covers PASSIVE_PORT_BASE..+SLICE_SIZE-1 (the only slice that
    aligns with the compose file's narrowest default exposure).
  - Each subsequent vp_id advances by exactly SLICE_SIZE — no overlap, no
    gap.
  - vp_ids beyond MAX_SLOTS wrap around (modulo) so installs that have
    churned through many VPs over time still produce a valid in-range
    slice; same-slot collisions fall back to the per-session 10-attempt
    random retry, which is the same behaviour as pre-#1646.
  - Defensive: a non-positive vp_id (shouldn't occur, but DBs are
    surprising) clamps to slot 0 rather than producing a negative port.
"""

from __future__ import annotations

import pytest

from backend.app.services.virtual_printer.ftp_server import (
    PASSIVE_MAX_SLOTS,
    PASSIVE_PORT_BASE,
    PASSIVE_SLICE_SIZE,
    compute_passive_port_slice,
)


class TestComputePassivePortSlice:
    def test_vp_id_one_starts_at_base(self):
        port_min, port_max = compute_passive_port_slice(1)
        assert port_min == PASSIVE_PORT_BASE
        assert port_max == PASSIVE_PORT_BASE + PASSIVE_SLICE_SIZE - 1

    def test_consecutive_vp_ids_get_adjacent_non_overlapping_slices(self):
        slice1_min, slice1_max = compute_passive_port_slice(1)
        slice2_min, slice2_max = compute_passive_port_slice(2)
        slice3_min, slice3_max = compute_passive_port_slice(3)
        assert slice1_max + 1 == slice2_min  # no gap
        assert slice2_max + 1 == slice3_min  # no gap
        # Slice width matches the constant — no off-by-one.
        assert slice1_max - slice1_min + 1 == PASSIVE_SLICE_SIZE
        assert slice2_max - slice2_min + 1 == PASSIVE_SLICE_SIZE
        assert slice3_max - slice3_min + 1 == PASSIVE_SLICE_SIZE

    def test_no_two_distinct_vp_ids_within_max_slots_share_a_port(self):
        seen: dict[int, int] = {}
        for vp_id in range(1, PASSIVE_MAX_SLOTS + 1):
            lo, hi = compute_passive_port_slice(vp_id)
            for port in range(lo, hi + 1):
                assert port not in seen, f"VP {vp_id} clashes with VP {seen[port]} on port {port}"
                seen[port] = vp_id

    def test_wraps_modulo_max_slots(self):
        """VP id past MAX_SLOTS lands on the same slice as its modulo-N
        neighbour — the per-session retry handles the rare collision."""
        first = compute_passive_port_slice(1)
        wrapped = compute_passive_port_slice(PASSIVE_MAX_SLOTS + 1)
        assert wrapped == first

    def test_top_slot_is_within_base_pool(self):
        """The last valid slot must stay below PASSIVE_PORT_BASE +
        MAX_SLOTS*SLICE_SIZE so the slice never escapes the pool that
        the docker-compose comments document for users."""
        _, hi = compute_passive_port_slice(PASSIVE_MAX_SLOTS)
        assert hi < PASSIVE_PORT_BASE + PASSIVE_MAX_SLOTS * PASSIVE_SLICE_SIZE

    @pytest.mark.parametrize("bad_id", [0, -1, -999])
    def test_non_positive_ids_clamp_to_slot_zero(self, bad_id):
        """Defensive: a bad vp_id from a corrupted row mustn't produce a
        negative port and crash asyncio.start_server."""
        assert compute_passive_port_slice(bad_id) == compute_passive_port_slice(1)


class TestFTPServerHonoursPerInstanceRange:
    """`VirtualPrinterFTPServer` now stores the slice on `self`. Two
    instances constructed with different slices must hand each
    `FTPSession` the right per-instance range — no class-level leak."""

    def test_two_instances_independent_ranges(self, tmp_path):
        from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer

        cert = tmp_path / "cert.pem"
        cert.write_text("not a real cert")
        key = tmp_path / "key.pem"
        key.write_text("not a real key")

        a = VirtualPrinterFTPServer(
            upload_dir=tmp_path,
            access_code="x",
            cert_path=cert,
            key_path=key,
            passive_port_min=50000,
            passive_port_max=50009,
        )
        b = VirtualPrinterFTPServer(
            upload_dir=tmp_path,
            access_code="x",
            cert_path=cert,
            key_path=key,
            passive_port_min=50050,
            passive_port_max=50059,
        )

        assert (a.passive_port_min, a.passive_port_max) == (50000, 50009)
        assert (b.passive_port_min, b.passive_port_max) == (50050, 50059)
        # Mutating one must not affect the other (regression guard against
        # the pre-fix class-constant layout).
        b.passive_port_min = 60000
        assert a.passive_port_min == 50000

    def test_default_construction_gives_a_one_slice_window(self, tmp_path):
        """A consumer that doesn't pass passive_port_min/max should still
        get a valid, minimal range — handy for tests and direct callers."""
        from backend.app.services.virtual_printer.ftp_server import VirtualPrinterFTPServer

        cert = tmp_path / "cert.pem"
        cert.write_text("x")
        key = tmp_path / "key.pem"
        key.write_text("x")

        server = VirtualPrinterFTPServer(
            upload_dir=tmp_path,
            access_code="x",
            cert_path=cert,
            key_path=key,
        )
        assert server.passive_port_min == PASSIVE_PORT_BASE
        assert server.passive_port_max - server.passive_port_min + 1 == PASSIVE_SLICE_SIZE
