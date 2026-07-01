# Research: Purge-Line Failures Traced to MQTT Calibration Flags, Not Developer Mode

_Written as a standalone research report (undated in the original, carried over from
`print-farm-exploration/print-quality-issues-research.md`), added to this knowledge base
2026-07-01. Status: this is analysis/research, not a confirmed-and-fixed incident — the
diagnostic and fix steps below have not been confirmed as done. Verify against current
BamBuddy behavior (workflow default calibration flags, actual MQTT payload) before
citing this as still-accurate for the current fork version._

**The intermittent purge lines going off-bed are almost certainly caused by BamBuddy's
MQTT `project_file` command sending `bed_levelling: false` (or omitting the field
entirely), not by Developer Mode itself.** Developer Mode is purely a communication-layer
change that opens MQTT/FTP access — it does not alter firmware behavior, calibration
routines, or coordinate systems in any way. The real culprit is the set of boolean
calibration flags embedded in the MQTT print command, which BamBuddy may set differently
than BambuStudio does. In a farm context, this interacts with stale homing data and
printer state transitions to produce the intermittent failures.

This report synthesizes findings from the OpenBambuAPI reverse-engineering
documentation, BamBuddy's GitHub issues and changelog, Bambu Lab community forums,
captured MQTT traffic analysis, and the P1S startup G-code architecture.

---

## The MQTT print command controls calibration, not the firmware or Developer Mode

When any software — BambuStudio, BamBuddy, or any MQTT client — starts a print on a
Bambu printer, it sends a `print.project_file` MQTT command. This command contains
**five boolean flags that control which pre-print calibration routines run**:

```json
{
    "print": {
        "command": "project_file",
        "bed_levelling": true,
        "flow_cali": true,
        "vibration_cali": true,
        "layer_inspect": true,
        "timelapse": false,
        ...
    }
}
```

**These flags are the only mechanism controlling calibration.** They are not embedded in
the `.gcode.3mf` file and are not determined by the printer firmware. Whichever software
sends the print command decides whether calibration runs. If `bed_levelling` is `false`
or missing, the printer skips bed mesh probing and reuses cached homing/mesh data. If
that cached data is stale — because the bed was disturbed during part removal, the
printer was power-cycled, or temperature changed — the printer's coordinate origin
drifts, and the purge line (which uses hardcoded absolute coordinates) prints off the
bed edge.

Inside the `.gcode.3mf`, the P1S startup G-code uses firmware-level conditional blocks
controlled by these flags:

```gcode
M1002 judge_flag g29_before_print_flag
M622 J1
    G29 A X{...} Y{...}   ;bed leveling only if flag is set
M623
```

The `g29_before_print_flag` is set by the `bed_levelling` field in the MQTT command.
When it's `false`, the entire G29 bed-leveling block is skipped. The purge line code
that follows uses absolute coordinates (**X18→X240, Y1→Y11** on the P1S front edge),
which assume homing has correctly established the origin.

---

## BamBuddy's default print options are the most likely root cause

BamBuddy exposes calibration flags through its **Settings → Workflow** panel (upstream
feature #858, "Configurable Default Print Options"). This feature lets users set
defaults for bed levelling, flow calibration, vibration calibration, first-layer
inspection, and timelapse. The key problem: **if these defaults were never explicitly
configured, or were configured with `bed_levelling` disabled, every print sent from
BamBuddy's queue or archive skips bed leveling.**

BambuStudio shows calibration checkboxes in a dialog every time you send a print, with
`bed_leveling` defaulting to `true`. Users visually confirm the setting each time.
BamBuddy, by contrast, applies workflow defaults silently — there's no per-print visual
confirmation unless the user opens the print dialog manually. In a farm context where
prints are queued and dispatched automatically, the defaults are all that matter.

The intermittent nature of the failures fits this explanation precisely:

- **Prints succeed** when the cached homing/mesh data happens to still be accurate (e.g.
  immediately after a successful print where the bed wasn't disturbed).
- **Prints fail** when the cached data is stale (after part removal shifted the bed,
  after a power cycle cleared the cache, or after thermal drift changed the mesh).
- **Queue items created before defaults were configured** carry their own saved print
  options, which may differ from the current defaults.

Additionally, the `bambulabs_api` Python library (used as a reference by some MQTT
tools) exposes only `flow_calibration` in its `start_print_3mf()` method — it doesn't
even expose bed leveling as a configurable parameter, suggesting it may omit or hardcode
the field.

---

## A field-name spelling discrepancy adds another failure mode

A notable finding across the ecosystem: **the MQTT field name for bed leveling has
inconsistent spelling between sources**. The OpenBambuAPI community documentation uses
`bed_levelling` (British, double-L); captured traffic from Bambu's own mobile app
(Bambu Handy) shows `bed_leveling` (American, single-L). BamBuddy's user-facing text
uses "bed levelling" (British).

| Source | Field name | Spelling |
|---|---|---|
| OpenBambuAPI docs | `bed_levelling` | British |
| Bambu Handy (captured traffic) | `bed_leveling` | American |
| BamBuddy UI text | "bed levelling" | British |
| `bambulabs_api` library | `bed_levelling` | British |

Both spellings appear to work with current firmware — no confirmed reports attribute
failures specifically to this mismatch. However, it introduces a risk: **if a firmware
update tightens field validation, or a firmware version only recognizes one spelling,
the unrecognized field would be silently ignored** and the firmware would apply its
default (likely `false`/skip). This could explain a regression appearing with no obvious
configuration change.

---

## Race conditions in farm environments compound the problem

Several documented firmware and protocol behaviors create race-condition risks in print
farms:

**Printer state transitions are fragile.** BamBuddy's own issue tracker documents
problems with state detection: upstream issue #790 ("State is FINISH but completion NOT
triggered") caused diagnostic floods in farm setups because `gcode_state` wasn't
transitioning cleanly from `FINISH` back to `IDLE`. (This fork's own Fix 1 in
[`fixes.md`](fixes.md) addresses a closely related completion-detection gap.) If
BamBuddy dispatches a new job while the printer is still in a transitional state, the
firmware may not properly reinitialize the coordinate system.

**P1 series printers send only delta MQTT updates** (see [`printers.md`](printers.md)).
BamBuddy's view of printer state can become incomplete if it misses even one MQTT report
message — a missed state update could cause BamBuddy to believe the printer is idle when
it isn't, triggering a premature dispatch.

**FTP upload timing matters.** BamBuddy uploads the `.gcode.3mf` via FTPS, then sends
the MQTT print command. If the MQTT command fires before the FTP upload completes —
possible under network latency or with large files — the printer may read a truncated
file, corrupting the startup G-code sequence including the purge-line coordinates.

**Bambu has explicitly acknowledged MQTT race conditions** in firmware release notes:
"Prevent conflicts with Bambu Cloud service & potential damage to printer hardware by
prohibiting crucial printer controls through the local MQTT Broker while the printer is
logged into Bambu Cloud service... two asynchronous channels were controlling the
printer simultaneously. The commands from these two channels would execute in an
overlapping manner without any order assurance." While that specific fix addressed
cloud+local conflicts, it confirms the firmware's MQTT command processing is inherently
susceptible to ordering issues.

---

## Developer Mode is definitively not the cause

Developer Mode on Bambu Lab printers is **exclusively a communication/security change**.
Per the Bambu Lab wiki, it:

- Opens the local MQTT broker (port 8883/TLS), FTP server, and live stream for
  third-party access.
- Disables MQTT command authentication/verification (added in firmware 01.08.03.00+).
- Forces LAN-Only mode, disabling cloud features.

It does **not** change firmware calibration behavior, alter the startup G-code sequence,
affect bed origin or homing routines, modify how the printer interprets G-code, or skip
any automatic calibration. Developer Mode is a prerequisite for BamBuddy to function at
all — it's innocent here; BamBuddy's MQTT command construction is where the actual issue
lies. That said, Developer Mode does indirectly enable the problem, in that it's what
allows third-party tools to send print commands with calibration flags different from
what BambuStudio would send.

---

## Concrete debugging steps and fixes (not confirmed done)

**Immediate check — verify calibration defaults:**
1. Open BamBuddy Settings → Workflow and confirm `bed_levelling` defaults to **true**.
2. Check `flow_cali` and `vibration_cali` defaults too.
3. Audit existing queue items — items created before defaults were configured carry
   their own saved print options and may need editing or recreating.
4. When manually starting prints, verify the bed-leveling checkbox in the print dialog.

**Diagnostic verification — capture the actual MQTT payload:**
1. Enable debug logging and inspect the exact `project_file` JSON sent to the printer.
2. Verify the field spelling (`bed_levelling` vs `bed_leveling`) and confirm it's a
   boolean `true`, not a string or missing.
3. Compare field-by-field against a captured BambuStudio payload to the same printer.

**Farm-specific hardening, not implemented as of this writing:**
1. A minimum delay between job completion and next dispatch (5–10s), so the printer's
   state machine fully transitions to `IDLE` before the next command.
2. Issuing a `pushall` MQTT command before a new print, and verifying `gcode_state` is
   `IDLE` from the authoritative response, rather than trusting cached state.
3. Verifying the FTP upload fully completed (file size/checksum) before sending the
   MQTT print command.

**G-code-level workaround, if MQTT flags can't be trusted:** modify the Machine Start
G-code in the slicer profile to unconditionally run G28 (homing) and optionally G29 (bed
leveling), removing the dependency on the MQTT flags entirely. As a more drastic option,
some farm operators remove the purge line from start G-code entirely — the 105mm
filament purge into the purge chute (which runs regardless of calibration flags) is
sufficient for nozzle priming on its own.

## Conclusion

The root cause is architectural: Bambu's design delegates calibration decisions to the
sending software via MQTT flags, not to the firmware or the sliced file. This works fine
with BambuStudio, which surfaces those flags in every send dialog. A farm-queue tool
like BamBuddy makes those flags hidden configuration that's easy to misconfigure or
overlook. The fix, if this is confirmed as the active failure mode on this farm: ensure
`bed_levelling: true` is actually being sent in every print command, verify BamBuddy's
workflow defaults, audit queued items, and consider state-verification delays between
farm jobs.
