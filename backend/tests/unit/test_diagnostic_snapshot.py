"""Tests for the diagnostic snapshot helper that aggregates connection,
virtual-printer, and log-health diagnostics for the support bundle and
bug-report submission paths (#1506 follow-up).

The helper has three hard requirements:

- Always returns the three top-level keys, even when sections are empty.
- Fail-soft per probe — a single crash doesn't break the snapshot.
- Bounded total runtime — concurrent gather caps wall-clock to the slowest probe.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.diagnostic_snapshot import collect_diagnostic_snapshot


def _make_db_with_printers_and_vps(printers: list, vps: list):
    """Stub AsyncSession whose two .execute() calls return printers then VPs."""
    printers_result = MagicMock()
    printers_result.scalars.return_value.all.return_value = printers
    vps_result = MagicMock()
    vps_result.scalars.return_value.all.return_value = vps
    db = MagicMock()
    # Two execute calls — printer query, then VP query (order matches the
    # helper). side_effect cycles through the queue.
    db.execute = AsyncMock(side_effect=[printers_result, vps_result])
    return db


@pytest.mark.asyncio
async def test_snapshot_always_returns_three_top_level_keys_when_empty():
    """No printers, no VPs — still get the three keys (empty lists, empty
    log-health). Callers downstream rely on the shape being stable."""
    db = _make_db_with_printers_and_vps([], [])
    with patch(
        "backend.app.services.diagnostic_snapshot._run_log_health",
        new=AsyncMock(return_value={"findings": []}),
    ):
        out = await collect_diagnostic_snapshot(db)
    assert set(out.keys()) == {"connection_diagnostics", "vp_diagnostics", "log_health"}
    assert out["connection_diagnostics"] == []
    assert out["vp_diagnostics"] == []
    assert out["log_health"] == {"findings": []}


@pytest.mark.asyncio
async def test_snapshot_runs_diagnostic_per_active_printer():
    """Each active printer gets a connection check; each enabled VP gets a
    setup check. Result list length matches the input lists."""
    printers = [
        SimpleNamespace(id=1, name="P1S", ip_address="192.168.1.10", serial_number="01S00A", access_code="abc123"),
        SimpleNamespace(id=2, name="X1C", ip_address="192.168.1.11", serial_number="C11Y00", access_code="xyz456"),
    ]
    vps = [SimpleNamespace(id=10, name="VP-1")]
    db = _make_db_with_printers_and_vps(printers, vps)

    fake_conn = SimpleNamespace(model_dump=lambda: {"checks": []})
    fake_vp = SimpleNamespace(model_dump=lambda: {"checks": []})

    with (
        patch(
            "backend.app.services.printer_diagnostic.run_connection_diagnostic",
            new=AsyncMock(return_value=fake_conn),
        ),
        patch(
            "backend.app.services.virtual_printer.virtual_printer_manager.get_instance",
            return_value=None,
        ),
        patch(
            "backend.app.services.virtual_printer.diagnostic.run_vp_diagnostic",
            new=AsyncMock(return_value=fake_vp),
        ),
        patch(
            "backend.app.services.diagnostic_snapshot._run_log_health",
            new=AsyncMock(return_value={"findings": []}),
        ),
    ):
        out = await collect_diagnostic_snapshot(db)

    assert len(out["connection_diagnostics"]) == 2
    assert out["connection_diagnostics"][0]["printer_id"] == 1
    assert out["connection_diagnostics"][1]["printer_id"] == 2
    assert all("result" in entry for entry in out["connection_diagnostics"])
    assert len(out["vp_diagnostics"]) == 1
    assert out["vp_diagnostics"][0]["vp_id"] == 10
    assert "result" in out["vp_diagnostics"][0]


@pytest.mark.asyncio
async def test_snapshot_fails_soft_when_single_printer_diagnostic_raises():
    """A crash inside one printer's diagnostic emits an error marker for that
    printer, but the snapshot's other sections still complete. This is the
    whole point of including diagnostics in the bundle — a partial result
    beats a 500."""
    printers = [
        SimpleNamespace(id=1, name="ok", ip_address="1.1.1.1", serial_number="s1", access_code="a"),
        SimpleNamespace(id=2, name="bad", ip_address="2.2.2.2", serial_number="s2", access_code="b"),
    ]
    db = _make_db_with_printers_and_vps(printers, [])

    fake_ok = SimpleNamespace(model_dump=lambda: {"status": "ok"})

    async def diag(ip_address, **_):
        if ip_address == "2.2.2.2":
            raise RuntimeError("simulated crash")
        return fake_ok

    with (
        patch("backend.app.services.printer_diagnostic.run_connection_diagnostic", new=AsyncMock(side_effect=diag)),
        patch(
            "backend.app.services.diagnostic_snapshot._run_log_health",
            new=AsyncMock(return_value={"findings": []}),
        ),
    ):
        out = await collect_diagnostic_snapshot(db)

    # Both printers represented; the crashing one carries an `error` field.
    assert len(out["connection_diagnostics"]) == 2
    ok_entry = next(e for e in out["connection_diagnostics"] if e["printer_id"] == 1)
    bad_entry = next(e for e in out["connection_diagnostics"] if e["printer_id"] == 2)
    assert "result" in ok_entry
    assert "error" not in ok_entry
    assert "error" in bad_entry
    assert "simulated crash" in bad_entry["error"]
    # Log-health still completes despite the per-printer crash.
    assert out["log_health"] == {"findings": []}


@pytest.mark.asyncio
async def test_snapshot_emits_timed_out_marker_when_probe_exceeds_cap():
    """If a single probe stalls past the per-diagnostic timeout, the entry
    is marked `timed_out` rather than blocking the whole snapshot. Patch
    the timeout small so the test runs fast."""
    printers = [SimpleNamespace(id=1, name="slow", ip_address="1.1.1.1", serial_number="s", access_code="a")]
    db = _make_db_with_printers_and_vps(printers, [])

    async def slow_diag(*a, **k):
        import asyncio

        await asyncio.sleep(5)  # well past the patched cap below
        return SimpleNamespace(model_dump=lambda: {})

    with (
        patch(
            "backend.app.services.printer_diagnostic.run_connection_diagnostic", new=AsyncMock(side_effect=slow_diag)
        ),
        patch("backend.app.services.diagnostic_snapshot._PER_DIAGNOSTIC_TIMEOUT_SECONDS", 0.05),
        patch(
            "backend.app.services.diagnostic_snapshot._run_log_health",
            new=AsyncMock(return_value={"findings": []}),
        ),
    ):
        out = await collect_diagnostic_snapshot(db)

    assert len(out["connection_diagnostics"]) == 1
    assert out["connection_diagnostics"][0]["error"] == "timed_out"


@pytest.mark.asyncio
async def test_snapshot_masks_ip_addresses_in_all_diagnostic_fields():
    """The diagnostic schemas embed raw IPv4 in three places — the top-level
    ``PrinterDiagnosticResult.ip_address``, the network-mode check's
    ``params.{printer_ip, host_ip}``, and the VP diagnostic's
    ``params.bind_ip``. None of those should leak into the submitted
    snapshot. Sanitization runs after the per-probe gather; both DB-known
    IPs (covered by sensitive_strings → "[IP]") and host / VP-bind IPs
    (caught by the IPv4 regex fallback) end up redacted.
    """
    printers = [
        SimpleNamespace(
            id=1, name="Workshop", ip_address="192.168.255.131", serial_number="01S00ABC123", access_code="abcd1234"
        )
    ]
    vps = [SimpleNamespace(id=10, name="VP-Workshop")]
    db = _make_db_with_printers_and_vps(printers, vps)

    fake_conn = SimpleNamespace(
        model_dump=lambda: {
            "ip_address": "192.168.255.131",
            "overall": "ok",
            "checks": [
                {
                    "id": "network_mode",
                    "status": "warn",
                    "params": {"printer_ip": "192.168.255.131", "host_ip": "192.168.255.16"},
                }
            ],
        }
    )
    fake_vp = SimpleNamespace(
        model_dump=lambda: {
            "overall": "ok",
            "checks": [{"id": "bind_interface", "status": "pass", "params": {"bind_ip": "192.168.254.2"}}],
        }
    )

    with (
        patch(
            "backend.app.services.log_reader.collect_sensitive_strings",
            new=AsyncMock(
                return_value={
                    "Workshop": "[PRINTER]",
                    "192.168.255.131": "[IP]",
                    "01S00ABC123": "[SERIAL]",
                    "abcd1234": "[ACCESS_CODE]",
                }
            ),
        ),
        patch(
            "backend.app.services.printer_diagnostic.run_connection_diagnostic", new=AsyncMock(return_value=fake_conn)
        ),
        patch("backend.app.services.virtual_printer.virtual_printer_manager.get_instance", return_value=None),
        patch("backend.app.services.virtual_printer.diagnostic.run_vp_diagnostic", new=AsyncMock(return_value=fake_vp)),
        patch(
            "backend.app.services.diagnostic_snapshot._run_log_health",
            new=AsyncMock(return_value={"findings": [{"sample": "Connecting to 10.0.0.5..."}]}),
        ),
    ):
        out = await collect_diagnostic_snapshot(db)

    # Conection diagnostic — top-level ip_address and check params both masked.
    conn_entry = out["connection_diagnostics"][0]
    assert conn_entry["printer_name"] == "[PRINTER]"
    assert conn_entry["result"]["ip_address"] == "[IP]"
    check_params = conn_entry["result"]["checks"][0]["params"]
    assert check_params["printer_ip"] == "[IP]"
    assert check_params["host_ip"] == "[IP]"  # not in DB; caught by regex fallback

    # VP diagnostic — bind_ip masked (regex fallback; never in DB).
    vp_entry = out["vp_diagnostics"][0]
    assert vp_entry["result"]["checks"][0]["params"]["bind_ip"] == "[IP]"

    # Log-health findings — IPs in log samples also masked (regex applies
    # recursively through the dict, not just to known fields).
    assert "10.0.0.5" not in str(out["log_health"])
    assert "[IP]" in out["log_health"]["findings"][0]["sample"]

    # Sanity: no raw IPv4 anywhere in the serialized snapshot.
    import json
    import re as _re

    serialized = json.dumps(out)
    raw_ipv4 = _re.search(
        r"\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]\d|\d)){3}\b", serialized
    )
    assert raw_ipv4 is None, f"raw IPv4 leaked into snapshot: {raw_ipv4.group()}"


@pytest.mark.asyncio
async def test_snapshot_runs_probes_concurrently_not_sequentially():
    """Total wall-clock for N printers should be O(slowest), not O(sum) —
    this is what makes the feature usable on a fleet. Set each probe to
    take 0.2 s; with 4 printers, sequential is 0.8 s, concurrent is 0.2 s.
    Allow margin for scheduling and the test still catches a regression
    to sequential execution.
    """
    import time

    printers = [
        SimpleNamespace(id=i, name=f"P{i}", ip_address=f"1.1.1.{i}", serial_number=f"s{i}", access_code="a")
        for i in range(4)
    ]
    db = _make_db_with_printers_and_vps(printers, [])

    async def slow_diag(*a, **k):
        import asyncio

        await asyncio.sleep(0.2)
        return SimpleNamespace(model_dump=lambda: {"ok": True})

    with (
        patch(
            "backend.app.services.printer_diagnostic.run_connection_diagnostic", new=AsyncMock(side_effect=slow_diag)
        ),
        patch(
            "backend.app.services.diagnostic_snapshot._run_log_health",
            new=AsyncMock(return_value={"findings": []}),
        ),
    ):
        start = time.monotonic()
        out = await collect_diagnostic_snapshot(db)
        elapsed = time.monotonic() - start

    assert len(out["connection_diagnostics"]) == 4
    # Concurrent should be ~0.2 s; sequential would be ~0.8 s. Use 0.5 s
    # as the threshold — slack enough for slow CI, tight enough to catch
    # a regression to sequential execution.
    assert elapsed < 0.5, f"snapshot ran sequentially: {elapsed:.2f}s for 4 x 0.2s probes"
