# Spoolman Inventory UI — Test Plan

**Build under test:** either branch `feature/spoolman-inventory-ui` OR docker image `bambuddy:spoolman-test_20260505` (both contain the merge with dev as of 2026-05-05)
**Issued:** 2026-05-05

---

## How to use this plan

**Run ALL tests in Local (internal) mode first. Only after the entire Local pass is complete do you switch to Spoolman mode and run everything again. Do not interleave the two modes.**

1. **Pass 1 — Local (internal DB) mode.** Confirm Spoolman is **disabled** in Settings, then walk top-to-bottom through every section (0 → A → B → C → D → E), filling the **"Pass 1 (Local)"** column on every row. Rows tagged Spoolman-only get `N/A` for this pass. Do not touch Section F yet.
2. **Pass 2 — Spoolman mode.** Only when Pass 1 is fully done: enable Spoolman in Settings, paste the Spoolman URL, save, **fully reload the browser**. Then walk the same sections 0 → A → B → C → D → E top-to-bottom again, filling the **"Pass 2 (Spoolman)"** column. Rows tagged Local-only get `N/A` for this pass.
3. **Section F — Final state diff.** Run only once, after Pass 2 completes.

A few ground rules for both passes:
- **The "Verify (slicer)" steps are MANDATORY** — see [How to verify in the slicer](#how-to-verify-in-the-slicer) below for the exact protocol. Most testers skip these. Don't. The slicer is the only place that proves AMS slot config actually applied — Bambuddy's UI can show a green checkmark while the printer's calibration table is unchanged.
- **Mark each row P (pass) / F (fail) / B (blocked)**. For F/B, paste a one-line note + screenshot link.
- **Stop and file a bug** the moment you hit an F. Don't keep going on a broken path — downstream results become noise.
- **Do not flip Spoolman on/off mid-pass.** If you accidentally do, restart the current pass.

---

## How to verify in the slicer

Whenever a row says **Verify (slicer)**, do this — it is the only way to catch silent failures where Bambuddy's UI says "applied" but the printer's calibration table never changed.

### Pick OrcaSlicer over BambuStudio

> **BambuStudio has a known bug**: the printer's AMS panel will not show custom (user / cloud) flow-dynamics profiles **unless** you have first visited *Calibration → Flow Dynamics → Manage Results* at least once in this BambuStudio session. Without that step, the AMS panel silently falls back to "Default" even when the printer actually has the right cali_idx applied — making it impossible to tell whether Bambuddy did its job or not.
>
> **Use OrcaSlicer for these checks whenever possible.** OrcaSlicer's AMS panel reads the printer's calibration table directly and does not have this caveat.
>
> If you must use BambuStudio, **open *Calibration → Flow Dynamics → Manage Results* once at the start of your session** (you can close it immediately afterwards), otherwise every "Verify (slicer)" step in this plan will give a false negative.

### The verification protocol

For each "Verify (slicer)" step:

1. Open the slicer (OrcaSlicer preferred). Connect to the printer being tested.
2. Open the **Device** tab → AMS panel for the right unit + slot.
3. **Click the slot** to open the slot detail modal/panel.
4. Confirm three things, in this order:
   - **a) Filament preset name** — must match the spool's `slicer_filament_name` (or for cloud presets, the cloud preset name). Must **not** be "Default" or a fallback like "Generic PLA". Take a screenshot if it looks wrong before troubleshooting — race conditions are real.
   - **b) K-profile (Flow Dynamics) selection** — must match the K-profile you assigned (by name or slot index). For "no stored K-profile" tests, the live cali_idx the slot already had should be preserved.
   - **c) Re-open the modal** — close it and click the slot again. The values from (a) and (b) must persist. Flicker-to-Default or flicker-to-blank counts as a fail.
5. If ANY of (a)/(b)/(c) is wrong, the row is a fail — even if Bambuddy's own UI looks correct.

### Test both Bambu Lab and non-Bambu Lab spools

Every "Verify (slicer)" row must be exercised with **both** of these spool kinds during a pass. Don't run all tests with only one kind:

| Spool kind | Why it matters | What to use as the test spool |
|---|---|---|
| **Bambu Lab spool** with auto-detected RFID and cloud preset (`GFL05`, `GFA05`, etc.) | Tests the cloud preset lookup path, builtin filament_id mapping, and the "tray_info_idx already known" branch. | Any genuine Bambu PLA Basic / PETG HF / etc. spool with the original tag. |
| **Non-Bambu Lab spool** assigned to a local or cloud user preset (`PFUS*`, `PFSP*`, or local-id integer slicer_filament) | Tests the user-preset path, the cloud-detail lookup fallback, and the K-profile resolution code that the recent fixes (`219bad76`, `8777dbea`, `b3aa8f3f`) target. This is where most regressions hide. | Polymaker / Sunlu / generic PLA with a custom slicer preset and a stored K-profile. Ideally one with calibration values that visibly differ from the Bambu spool above. |

Within each section (B and C in particular), do the row once with the Bambu spool, then once with the non-Bambu spool. If results differ, **the result of the row is the worse of the two**, and the difference itself is something to file.

---

## Pre-flight setup

### Required environment
- [ ] Bambuddy running, either built from branch `feature/spoolman-inventory-ui` **or** pulled from docker image `bambuddy:spoolman-test_20260505`
- [ ] At least **two** Bambu Lab printers connected (single-printer setups miss multi-printer assignment bugs)
- [ ] At least **one printer with a multi-AMS configuration** (dual AMS / AMS HT) — single-AMS users miss "wrong-AMS" routing
- [ ] **OrcaSlicer** (preferred) installed on a machine that can reach the printers. BambuStudio acceptable but see the bug warning under [How to verify in the slicer](#how-to-verify-in-the-slicer).
- [ ] (Spoolman mode only) Spoolman instance reachable, with at least 5 spools pre-populated

### Required test spools
You need physical spools of **both** kinds available for AMS-config tests in Sections B and C. Do not run those sections with only one kind:
- [ ] At least **one Bambu Lab spool** with original RFID tag intact (auto-detected cloud preset path — `GFL05`, `GFA05`, etc.)
- [ ] At least **one non-Bambu Lab spool** assigned to a custom slicer preset with a stored K-profile (Polymaker / Sunlu / Overture / generic PLA — exercises the user-preset path and the K-profile resolution that the recent fixes target)

### Login + auth
- [ ] Two user accounts exist: an **admin** and a **non-admin operator**
- [ ] At least one **API key** created for the admin

### Initial data state — capture before testing
Take a snapshot so you can tell whether something changed unexpectedly:

```bash
# from your Bambuddy admin shell, or via the API:
curl -s ${BAMBUDDY_URL}/api/v1/inventory/spools | jq 'length'
curl -s ${BAMBUDDY_URL}/api/v1/inventory/assignments | jq 'length'
curl -s ${BAMBUDDY_URL}/api/v1/spoolman/inventory/spools | jq 'length'   # spoolman mode
curl -s ${BAMBUDDY_URL}/api/v1/spoolman/inventory/slot-assignments | jq 'length'
```

Record the counts. Re-check at the end of testing.

---

## 0 — Pass entry sanity

This section runs at the **start of each pass** to confirm you are in the right mode before any other testing. Do not flip Spoolman on/off in the middle of a pass.

### 0a. Pass 1 entry (Local) — confirm Local mode

| # | Step | Verify | Pass 1 (Local) |
|---|---|---|---|
| 0a.1 | Settings → Spoolman → confirm "Spoolman Enabled" is **OFF**, save if you had to change it | Toast confirms save (if changed). | _ |
| 0a.2 | Fully reload the browser. Open `/inventory` | Page renders **local** spool list. Count matches local DB (`GET /api/v1/inventory/spools`). | _ |
| 0a.3 | Settings → Spool Catalog tab | Shows **local** catalog table | _ |

### 0b. Pass 2 entry (Spoolman) — confirm Spoolman mode

| # | Step | Verify | Pass 2 (Spoolman) |
|---|---|---|---|
| 0b.1 | Settings → Spoolman → toggle ON, paste Spoolman URL, save | Toast confirms save. No console errors. | _ |
| 0b.2 | Fully reload the browser. Open `/inventory` | Page renders **Spoolman** spool list. Count matches Spoolman (`GET /api/v1/spoolman/inventory/spools`). | _ |
| 0b.3 | Break the Spoolman URL (`http://invalid:9999`), save, reload | Inventory page surfaces a clear error/disabled state — does **not** silently fall back to local | _ |
| 0b.4 | Restore the Spoolman URL, save, reload | Spool list returns | _ |
| 0b.5 | Settings → Spool Catalog tab | Shows **Spoolman filament table** (not local catalog) | _ |

**Stop and file a bug if 0a or 0b fail for the pass you are in.** Mode-switch is the foundation; everything downstream is invalid otherwise.

---

## A — Inventory page (`/inventory`)

### A1. Spool list rendering

| # | Step | Verify (UI) | Verify (DB / API) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|
| A1.1 | Open `/inventory` | Tabs visible: Spools, Assignments. Spool count chip is correct. | List length matches `GET /inventory/spools` (or `/spoolman/inventory/spools`) | _ | _ |
| A1.2 | Each spool card shows: color swatch, name, material, brand, weight bar | Color swatch reflects rgba/extra_colors. Multi-color shows gradient. Sparkle effect renders for sparkle spools. | — | _ | _ |
| A1.3 | Hover a spool card → FilamentHoverCard | Shows nozzle temp range, K-profiles list, slicer preset name | — | _ | _ |
| A1.4 | Filter by material (PLA / PETG / ABS chip) | Only matching spools remain | — | _ | _ |
| A1.5 | Search by name / RFID UID | Match works for both substrings | — | _ | _ |
| A1.6 | Sort by Recent / Name / Weight | Ordering correct in both directions | — | _ | _ |

### A2. Spool CRUD

| # | Step | Verify (UI) | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|
| A2.1 | Click **+ New Spool** → SpoolFormModal opens | Form has: name, material, brand, color picker, weight, NFC fields, K-profile section, **slicer preset picker** | — | _ | _ |
| A2.2 | (Local) Fill all fields, click **Pick from Catalog** → catalog modal opens | Catalog list shown. Picking entry pre-fills name/weight. | catalog row referenced | _ | N/A |
| A2.3 | (Spoolman) **Pick from Filament Catalog** → Spoolman filament table shown | Selecting a Spoolman filament pre-fills material/brand/color | uses Spoolman filament_id | N/A | _ |
| A2.4 | Save new spool | Toast "Spool created". Card appears in list. | row exists | _ | _ |
| A2.5 | Edit spool — change weight, save | Weight bar updates, card re-renders | row updated | _ | _ |
| A2.6 | Edit spool — change `storage_location`, save | Field persists across reload — **no round-trip duplication** (regression check) | column persists exactly | _ | _ |
| A2.7 | Edit spool — set NFC `tag_uid` to 14-char hex, save | Saves OK (column was widened to VARCHAR(32)) | persisted | _ | _ |
| A2.8 | Edit spool — set color to multi-color (2+ extra_colors), save | Swatch shows gradient | extra_colors persisted | _ | _ |
| A2.9 | Archive spool | Card disappears from default view; appears under "Archived" filter | `archived=true` | _ | _ |
| A2.10 | Restore archived spool | Card returns to active list | `archived=false` | _ | _ |
| A2.11 | Delete spool (active, no assignments) | Confirmation prompt → row removed | row gone | _ | _ |
| A2.12 | Delete spool **currently assigned to AMS** | Either prevents delete or unassigns first — **must not silently leave a dangling assignment** | no orphan assignment row | _ | _ |

### A3. Catalog management

The catalog UI is mode-aware: Local mode shows the local catalog; Spoolman mode shows the Spoolman filament table. Section 0a.3 / 0b.5 already covered "the right table is shown for this pass" — A3 covers editing.

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| A3.1 | Open Settings → Spool Catalog | (Local) catalog table editable. (Spoolman) filament table editable. | _ | _ |
| A3.2 | Edit a catalog/filament entry's name and `spool_weight`, save | Changes persist; spools using that entry pick up new spool_weight (regression — `28fa66a3` "stamp on apply to all") | _ | _ |
| A3.3 | Add a new catalog/filament entry | Saves and is selectable in SpoolFormModal | _ | _ |
| A3.4 | Delete a catalog/filament entry not in use | Removes cleanly | _ | _ |

### A4. K-profile section in SpoolFormModal

| # | Step | Verify (UI) | Verify (slicer) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|
| A4.1 | Edit spool → K-Profiles section | List of stored K-profiles per (printer, nozzle, extruder) | — | _ | _ |
| A4.2 | Add a K-profile row: pick printer, set nozzle 0.4, K-value 0.020, slot_id 5 | Saves; row visible after reload | row in `k_profile` table | _ | _ |
| A4.3 | Edit existing K-profile, change cali_idx (slot_id) | Updates without creating duplicate | row updated, no second row | _ | _ |
| A4.4 | Delete K-profile | Removed from list | row gone | _ | _ |

### A5. Assignments tab

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| A5.1 | Switch to Assignments tab | Each row shows: spool name, **printer_name**, **AMS label** ("AMS-A", "HT-A", "External"), tray ID | _ | _ |
| A5.2 | Click "View in printer card" on an assignment | Routes to `/printers` and opens that printer's card | _ | _ |
| A5.3 | Unassign from row | Assignment disappears; spool returns to unassigned pool | _ | _ |

### A6. Deep-link from external context

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| A6.1 | Click a spool from the printer-card hover (deep-link) | `/inventory?spool=<id>` opens with that spool **scrolled into view + highlighted** | _ | _ |
| A6.2 | Deep-link to a spool that's archived | Page surfaces it (auto-includes archived) | _ | _ |

---

## B — Printer card AMS slot cards (`/printers`)

These tests exercise the AMS slot UI, the AssignSpoolModal, and the ConfigureAmsSlotModal — and the K-profile cascade work. **The slicer-verification step is the most important part of this section.**

> **Reminder before you start B:** every "Verify (slicer)" row must be run **twice** — once with a Bambu Lab spool (cloud preset path), once with a non-Bambu Lab spool (user-preset path). See [How to verify in the slicer](#how-to-verify-in-the-slicer) for the protocol. Use OrcaSlicer if at all possible.

### B1. AMS slot rendering on printer card

| # | Step | Verify (UI) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| B1.1 | Open `/printers`, expand a printer card | All AMS units shown with correct count of slots (4 per regular AMS, 1 for HT, 2 for External/VT) | _ | _ |
| B1.2 | Each slot shows: color, material/type label, weight % bar, K-profile indicator if set | Colors match the loaded filament's RGBA. Empty slots are visually distinct. | _ | _ |
| B1.3 | Hover a configured slot → FilamentHoverCard | Shows preset name, K-value, calibration source, **printer_name + AMS label** | _ | _ |
| B1.4 | Multi-printer setup: each printer's AMS only shows assignments for that printer | No cross-contamination | _ | _ |

### B2. Assign spool to a **loaded** AMS slot (immediate-apply path)

> Loaded slot = a slot the printer already reports filament in. Assignment fires MQTT immediately.

| # | Step | Verify (UI) | **Verify (slicer) — MANDATORY** | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|---|
| B2.1 | Click an empty-of-assignment but **physically loaded** AMS slot | AssignSpoolModal opens. Title shows correct AMS label + tray ID. | — | — | _ | _ |
| B2.2 | Spool list in the modal | **Already-assigned spools are excluded** (regression check — bug we fixed) | — | — | _ | _ |
| B2.3 | Pick a spool with a K-profile already stored for this printer/nozzle/extruder, confirm | Toast "Assigned!" closes within ~1.5s | Open BambuStudio → Device → AMS panel → click the slot → modal shows: <br>• preset name = the spool's slicer_filament_name (NOT "Default") <br>• K-value matches the spool's K-profile <br>• cali_idx matches the K-profile's slot_id | `SpoolAssignment` row created with non-empty `fingerprint_type`; `configured=true`; `pending_config=false` | _ | _ |
| B2.4 | After B2.3, **close and reopen the slot detail modal in BambuStudio** | Same preset / K-value / cali_idx persists across re-open (no flicker to "Default") | — | — | _ | _ |
| B2.5 | Pick a spool with **no** K-profile stored for this slot, confirm | Toast "Assigned!" | Slicer slot detail shows live cali_idx preserved (i.e. not reset to -1 / "Default") **if the slot already had a calibration** | row created; `extrusion_cali_sel` was published | _ | _ |
| B2.6 | Pick a spool whose `slicer_filament` is a PFUS\* cloud preset | Toast OK | Slicer shows the **cloud preset name**, not a generic fallback | tray_info_idx resolved via cloud lookup (check logs) | _ | _ |
| B2.7 | Pick a spool whose K-profile cascades from RFID tag scan | K-profile auto-populates from tag, persists after assignment | Slicer cali_idx matches the cascaded K-profile (regression — Phase 13 fix) | k_profile row + assignment both correct | _ | _ |

### B3. Assign spool to an **empty** AMS slot (deferral / SpoolBuddy primary workflow)

> Empty slot = printer reports `tray_type=""`. Bambu firmware silently drops MQTT for these, so Bambuddy persists the assignment and replays MQTT when the spool is physically inserted.

| # | Step | Verify (UI) | **Verify (slicer)** | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|---|
| B3.1 | Click an empty AMS slot, pick spool, confirm | Toast: **"Assigned. Slot will configure when you insert the spool."** Modal closes after ~2.5s. | Slicer slot is still empty / unconfigured (correct — nothing was published yet) | row created with empty fingerprint_type; `pending_config=true` | _ | _ |
| B3.2 | Now physically insert filament into that slot. Wait for AMS state push (~3–5s) | Slot card UI updates with material/color from the live tray | Slicer slot detail shows: spool's preset name, K-value, cali_idx — **as if it had been an immediate-apply assign** | `SpoolAssignment.fingerprint_type` now stamped; second `ams_set_filament_setting` published in logs | _ | _ |
| B3.3 | After B3.2, push another AMS update (e.g. wait 30s for next telemetry) | Slot card stable | **MQTT does NOT re-fire** (logs show it was skipped because fingerprint already stamped) | no duplicate publish in logs | _ | _ |

### B4. Configure-Slot modal (independent of assignment)

> Right-click slot → "Configure slot" — used to set/change preset+K-profile without changing the assigned spool.

| # | Step | Verify (UI) | **Verify (slicer) — MANDATORY** | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|
| B4.1 | Right-click a configured slot → Configure | ConfigureAmsSlotModal opens with preset and K-profile pre-filled from current state | — | _ | _ |
| B4.2 | Change preset to a different one with stored K-profile, save | Toast OK; modal closes | Slicer shows new preset name + new K-value + new cali_idx **all aligned**. Re-open slicer modal — values persist. | _ | _ |
| B4.3 | Change preset to one **without** stored K-profile, save | Toast OK | Slicer shows new preset; cali_idx falls back to current live cali_idx (not zero, not "Default") | _ | _ |
| B4.4 | Change K-profile (slot_id) to a different cali index, save | Toast OK | Slicer cali_idx matches the new slot_id within ~1s | _ | _ |
| B4.5 | Apply config to **slot with empty AMS tray** (tray_type=="") | Backend behaviour mirrors B3 — pending until insert | After insert, slicer reflects the configured preset + K-profile | _ | _ |
| B4.6 | (REGRESSION) Try a path where K-profile **silently dropped to default** previously | K-profile from form is what ends up in slicer — never silently zeroed. (Fix #219bad76, three apply paths.) Test via: assign spool with K-profile → ConfigureSlot opens with K-profile filled → save without changing anything → confirm cali_idx **unchanged** in slicer. | _ | _ |

### B5. Unassign spool from slot

| # | Step | Verify (UI) | **Verify (slicer)** | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|---|
| B5.1 | Right-click assigned slot → Unassign | Card returns to "no spool linked" state | Slicer side: previous preset / cali_idx remain (we don't actively clear printer state — just our DB) | `SpoolAssignment` row removed | _ | _ |
| B5.2 | After unassign, hover the slot | No "linked spool" info shown | — | — | _ | _ |
| B5.3 | (UI tidy) "Unassign" button is hidden in places where it would be redundant (regression check on the Printers page action menu) | No duplicate "Open in Inventory" or "Unassign" entries | — | — | _ | _ |

### B6. AMS labelling

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| B6.1 | Standard AMS unit IDs 0–3 → labels "AMS-A" through "AMS-D" | — | _ | _ |
| B6.2 | HT AMS IDs 128–135 → labels "HT-A" through "HT-H" | — | _ | _ |
| B6.3 | External / VT slot (id 254/255) → "External" | — | _ | _ |
| B6.4 | User-edited AMS friendly name → shows on hover card and assignment list | — | _ | _ |

---

## C — SpoolBuddy frontend (`/spoolbuddy`)

These tests run on a **paired SpoolBuddy device** (kiosk on a Pi or a desktop browser pointed at `/spoolbuddy`). Same Local-vs-Spoolman pass.

> **Reminder before you start C:** rows that touch AMS slot config (especially C4 weigh-and-assign and C5 AMS page) must be run with **both** a Bambu Lab spool and a non-Bambu Lab spool, and verified in the slicer per [How to verify in the slicer](#how-to-verify-in-the-slicer). Use OrcaSlicer if at all possible.

### C1. Dashboard rendering

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| C1.1 | Open `/spoolbuddy` | Top bar: connection state, mode chip ("Local" or "Spoolman" — unified label, regression check) | _ | _ |
| C1.2 | Quick menu / bottom nav | Tabs: Dashboard, Inventory, AMS, Calibration, Settings | _ | _ |
| C1.3 | Status bar shows weight reading | If scale absent, shows clear "scale not connected" — not a silent zero | _ | _ |

### C2. NFC tag flow — link to spool

| # | Step | Verify (UI) | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|
| C2.1 | Place a Bambu RFID tag on the reader | TagDetectedModal opens, shows tag UID + auto-decoded material/color | scanned UID logged | _ | _ |
| C2.2 | (Bambu auto-detected tag, never linked) "Assign to AMS" button is **disabled** with explanation tooltip (regression check) | — | — | _ | _ |
| C2.3 | Click **Link to existing spool** → spool list opens | Search works; can select | — | _ | _ |
| C2.4 | Confirm link | SpoolInfoCard appears + success toast (regression — `d8811a77`) | spool's `tag_uid` (NOT bambu tray_type code) updated (regression — `be48c60e`) | _ | _ |
| C2.5 | Place same tag again | Modal opens at the linked-spool view directly (no re-link prompt) | — | _ | _ |
| C2.6 | (Spoolman) link a non-Bambu NFC tag (14-hex-char UID) | Saves OK (column widening regression) | tag_uid persisted | N/A | _ |

### C4. Weigh-and-assign workflow

| # | Step | Verify (UI) | **Verify (slicer)** | Verify (DB) | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|---|---|
| C4.1 | Place spool on scale, place its tag | Live weight readout updates; spool info card shown | — | — | _ | _ |
| C4.2 | (Regression) Negative scale reading shown when tare not yet applied | Doesn't crash; shows the negative number rather than zero-clamping (`05d03062`) | — | — | _ | _ |
| C4.3 | Click "Assign to AMS" → AssignToAmsModal opens | Lists AMS slots across all reachable printers; **disabled for already-assigned spools** with clear tooltip (`f3a475ca`) | — | — | _ | _ |
| C4.4 | Pick an empty AMS slot → confirm | Toast: "Assigned. Slot will configure when you insert the spool." | Slicer empty (correct — pending) | row with `pending_config=true` | _ | _ |
| C4.5 | Insert spool into slot | After AMS push, full configuration replayed | **Slicer slot detail shows correct preset + K-value + cali_idx** | fingerprint stamped; full publish in logs | _ | _ |
| C4.6 | Pick a **loaded** AMS slot → confirm | Toast: "Assigned!" | Slicer immediately reflects new spool's preset + K-profile | row with `pending_config=false` | _ | _ |

### C5. SpoolBuddy AMS page

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| C5.1 | Open AMS page | All paired printers listed; tap a printer → its AMS units shown | _ | _ |
| C5.2 | Tap a slot card | Same Configure-Slot modal as desktop (or unified equivalent) | _ | _ |
| C5.3 | Apply changes from SpoolBuddy AMS page | Same effect + slicer visibility as B4 | _ | _ |

### C6. SpoolBuddy inventory page

| # | Step | Verify | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| C6.1 | Open Inventory page in SpoolBuddy | Spool list mirrors desktop /inventory (same backend, same mode) | _ | _ |
| C6.2 | (Regression) UI labels on Local vs Spoolman views are unified — same wording, no mode-specific divergence (#`05d819d1`) | "Spool weight", "Storage location", "Tag UID" reads identical in both | _ | _ |
| C6.3 | Edit spool from SpoolBuddy → save | Reflects in desktop /inventory after refresh | _ | _ |

---

## D — Cross-cutting / specific regression cases

These are bugs the recent fix commits addressed. **If any of these reproduces, file a P0.** The tests are written from the user's perspective — no backend knowledge needed.

| # | Regression | What to do | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| D1 | K-profile silently lost when assigning a spool | Assign a spool that has a stored K-profile (visible in the spool's edit form). Open OrcaSlicer's slot detail for that AMS slot — the **Flow Dynamics** value must show the spool's K-profile name, **not** "Default" or empty. | _ | _ |
| D2 | Changes from Configure-Slot don't stick | Right-click a slot → Configure → pick a different K-profile → Save. In OrcaSlicer's slot detail, **close and re-open** the modal — the K-profile must be the new one, not flicker back to a different value. | _ | _ |
| D3 | Custom-preset spool loses both preset and K-profile in slicer | Assign a spool that uses a **custom slicer preset** (one you imported into BambuStudio yourself, *not* a Bambu factory preset). In the slicer's slot detail, **both** the preset name **and** the K-profile must show the spool's values, not a generic fallback like "Generic PLA" / "Default". | _ | _ |
| D4 | Already-assigned spools shown in the picker | Open AssignSpoolModal on any AMS slot → the spool list must **exclude** spools that are already assigned to another slot. | _ | _ |
| D5 | Save hangs when removing extra colors from a multi-color spool | Edit a multi-color spool → remove all the extra colors so it becomes a single-color spool → click Save. Save must complete (no spinner that runs forever). | _ | _ |
| D6 | RFID tag scan doesn't bring its K-profile across | Scan a Bambu RFID tag (or a linked NFC-tagged spool) that has stored K-profiles → the spool form's K-Profiles section must auto-populate from the tag, with no manual entry needed. | _ | _ |
| D7 | Labels and wording differ between Local and Spoolman modes | After completing both passes, compare the same form / modal side-by-side in each mode — labels must read the same (e.g. "Spool weight", "Storage location", "Tag UID"). | _ | _ |
| D8 | Assignments tab missing printer name and AMS label | Inventory → Assignments tab → each row must display the printer's name (e.g. "X1C-living-room") and the AMS label (e.g. "AMS-A", "HT-A", "External") — not just bare numbers. | _ | _ |
| D9 | Storage location modified silently on save | Edit a spool → set "Storage location" to exactly `Shelf 3, slot B` → Save → reload → Edit again. The field must read **exactly** `Shelf 3, slot B` — no extra spaces, no quote characters, no duplication. | _ | _ |
| D10 | Bulk weight update doesn't apply to all spools | In the catalog (or Spoolman filament list), edit an entry's spool_weight → Save / Apply → return to inventory. **Every** spool linked to that catalog entry must show the new weight, not just the most recent one. | _ | _ |
| D11 | Spoolman on a private LAN IP is rejected | Set the Spoolman URL to a private LAN address (e.g. `http://192.168.1.50:7912` or `http://10.0.0.20:7912`) → Save → reload. Spoolman pages must load. (Earlier builds blocked private IPs as a security measure; this confirms the fix.) | N/A | _ |
| D12 | API key can no longer read settings *(skip if you don't use API keys)* | Settings → API Keys → create a key. From a terminal: `curl -H "Authorization: Bearer <key>" <bambuddy-url>/api/v1/settings` — must return the settings JSON, not "403 Forbidden". | _ | _ |

---

## E — Multi-user / permission tests

Run with the **non-admin operator** account.

| # | Step | Expected | Pass 1 (Local) | Pass 2 (Spoolman) |
|---|---|---|---|---|
| E1 | View `/inventory` | Allowed (read) | _ | _ |
| E2 | Create / edit / delete spool | Allowed if has `INVENTORY_UPDATE`; 403 if not | _ | _ |
| E3 | Assign spool to AMS | Allowed if has `INVENTORY_UPDATE`; 403 if not | _ | _ |
| E4 | Configure slot via Configure-Slot modal | Allowed only with proper permission | _ | _ |
| E5 | (API key) Read settings via API key | 200 (regression — was 403) | _ | _ |

---

## F — Final state diff

Run this **once** after Pass 2 completes (i.e. after both passes are done). Re-run the snapshot from Pre-flight and verify:

- [ ] Local spool count change matches what you intentionally created
- [ ] Local assignment count is back to baseline (or matches your final intentional state)
- [ ] Spoolman counts likewise
- [ ] No orphan rows: `SELECT COUNT(*) FROM spool_assignment WHERE spool_id NOT IN (SELECT id FROM spool)` should be 0
- [ ] No leftover `pending_config` rows: assignments where you completed the insert step have `fingerprint_type IS NOT NULL`

---

## How to file a failure

For each F row, please post a comment on issue **TBD** with:
- The row number (e.g. **B2.4**)
- One-line description of what you observed vs expected
- Bambuddy version (`/api/v1/version`)
- Printer model + firmware
- Slicer + version
- A screenshot of the slicer's slot detail modal (for any B/C section failure)
- Bambuddy logs from the relevant 60s window (`docker logs bambuddy --since 1m`)

---

## Skip / N/A guidance

- **No SpoolBuddy hardware:** skip Section C entirely in both passes. Note this at the top of your report.
- **No Spoolman instance:** skip Pass 2 (Spoolman) entirely, plus row D11. Run only Pass 1 (Local).
- **Single AMS unit:** skip B6, but still run B1–B5 on that one unit (in both passes).
- **Single printer:** skip B1.4 in both passes. Still run all per-printer tests on the one you have.
- **No NFC reader:** skip C2 in both passes.

---

*End of plan. ~120 distinct verification points, ~80 of which require slicer-side confirmation. Yes, this is a lot — that is the point.*
