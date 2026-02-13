# Beta Test Plan — Spool Inventory & Related Features

## Prerequisites

- At least one printer connected with AMS
- At least one Bambu Lab spool (RFID) loaded in AMS
- At least one spool without RFID tag
- At least one empty AMS slot (or slot with non-BL filament)
- A 3MF file ready to print (small/fast test print recommended)

---

## 1. Filament Tracking Mode Switching

**Location:** Settings > Filament

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 1.1 | Default mode | Open Settings > Filament | "Built-in Inventory" card is selected (green border), info panel shows RFID/usage/catalog bullet points |
| 1.2 | Switch to Spoolman | Click "Spoolman" card | Spoolman config appears (URL input, Sync Mode, connection status). Built-in info panel disappears |
| 1.3 | Switch back | Click "Built-in Inventory" card | Spoolman config disappears, built-in info panel reappears |
| 1.4 | Persistence | Switch mode, reload page | Selected mode persists after reload |
| 1.5 | Spoolman disabled state | Select Spoolman without URL, check Connect button | Connect button should be disabled when URL is empty |

---

## 2. Spool Management

**Location:** Inventory page (sidebar)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 2.1 | Add spool | Click "+ Add Spool", fill material/brand/color/weight, save | Spool appears in table with correct info |
| 2.2 | Edit spool | Click a spool row, change fields, save | Changes reflected in table |
| 2.3 | Remaining weight | Edit spool > Additional section > adjust remaining weight | Remaining weight = label_weight - weight_used, slider/input updates correctly |
| 2.4 | Spool catalog | Edit spool > Additional > Empty Spool Weight dropdown | Pre-defined spool weights shown, selecting one updates the field |
| 2.5 | Color picker | Edit spool > Color section | Recent colors, brand palettes, and hex input all work |
| 2.6 | PA profile tab | Edit spool > PA Profile tab | Matching K-profiles shown grouped by printer/nozzle (requires calibration data) |
| 2.7 | Archive spool | Archive a spool from context menu or edit | Spool moves to "Archived" tab |
| 2.8 | Delete spool | Delete a spool | Spool removed from all views |
| 2.9 | Summary cards | Check top of inventory page | Total Inventory, Total Consumed, By Material, In Printer, Low Stock cards show correct values |
| 2.10 | Filters | Try Active/Archived/All tabs, material/brand dropdowns, search | Filtering works correctly |
| 2.11 | View modes | Toggle between Table and Card view | Both views show correct spool data |

---

## 3. AMS Slot Assignment

**Location:** Printers page, AMS hover cards

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 3.1 | Assign spool | Hover over an empty/non-BL AMS slot > click "Assign Spool" | Modal opens showing only manual (non-BL) spools |
| 3.2 | BL spools filtered | Open assign modal | Bambu Lab spools (with RFID tags) are NOT in the list |
| 3.3 | Assigned spools filtered | Assign spool A to slot 1, then open assign modal for slot 2 | Spool A is NOT in the list (already assigned) |
| 3.4 | Current slot spool visible | Open assign for slot that already has a spool | The currently assigned spool IS still shown (for reassignment) |
| 3.5 | Confirm assignment | Select spool, click "Assign Spool" | Slot shows the assigned spool info on hover |
| 3.6 | Unassign spool | Hover over assigned slot > click "Unassign" | Spool removed from slot, slot shows default info |
| 3.7 | BL slot — no buttons | Hover over a slot with a Bambu Lab spool (RFID) | No "Assign Spool" or "Unassign" buttons shown |
| 3.8 | Empty slot display | Check an empty AMS slot | Shows type from AMS data or "Empty" (localized) |

---

## 4. Auto-Unlink on BL Spool Insertion

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 4.1 | BL spool replaces manual | Assign a manual spool to a slot, then physically insert a BL spool into that slot | Assignment automatically removed, slot now shows BL spool info |
| 4.2 | Log message | Check backend logs after 4.1 | Log shows "Auto-unlink: spool X AMSY-TZ — Bambu Lab spool detected" |

---

## 5. Spool Tag Linking

**Location:** Printers page, AMS hover cards for BL spools

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 5.1 | Link modal | Hover over BL spool slot > click "Link to Spool" (if available) | Modal opens showing only untagged spools |
| 5.2 | Search filter | Type in search box | Spools filtered by material/brand/color |
| 5.3 | Link spool | Click a spool in the list | Success toast, modal closes, spool now linked to tag |
| 5.4 | Tagged spools hidden | After linking spool A, open link modal again | Spool A is no longer in the list |

---

## 6. Usage Tracking — BL Spools (AMS Remain%)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 6.1 | Session capture | Start a print with a BL spool | Backend log shows "Captured start remain% for printer X (N trays)" |
| 6.2 | Completed print | Let print finish | Spool's weight_used increases by (delta% * label_weight). Check inventory page remaining weight |
| 6.3 | Failed/aborted print | Start and cancel a print mid-way | Usage still tracked based on remain% delta at time of stop |
| 6.4 | No double-tracking | Complete a print with BL spool | Only AMS delta tracking applies (no 3MF fallback for this spool) |

---

## 7. Usage Tracking — Non-BL Spools (3MF Estimates)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 7.1 | Assign and print | Assign a manual spool to AMS slot, start a print using that slot | Backend log shows "3MF fallback available" at print start |
| 7.2 | Completed print | Let print finish | Spool's weight_used increases by the 3MF estimated used_g. Check inventory page |
| 7.3 | Failed print scaling | Start a print, cancel at ~50% | Usage = 3MF estimate * (progress/100). E.g., 100g estimate at 50% = ~50g tracked |
| 7.4 | Multi-slot print | Print using multiple filament slots (some BL, some manual) | BL spools tracked via remain% delta, manual spools via 3MF. No double-counting |
| 7.5 | Slot mapping | Use a spool in AMS slot 5 (second AMS unit) | Correctly maps to AMS 1, Tray 0. Check usage history shows correct ams_id/tray_id |

---

## 8. Edge Cases

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 8.1 | No AMS data | Print from external spool (no AMS) | No crash, usage tracking gracefully skipped |
| 8.2 | No archive | Print without 3MF archive available | AMS delta path still works for BL spools, 3MF path skipped gracefully |
| 8.3 | Spool refilled | If remain% goes UP between start and end (spool swapped/refilled) | Negative delta skipped, no negative usage recorded |
| 8.4 | Rapid print start/stop | Start and immediately cancel a print | No errors, minimal or zero usage tracked |
| 8.5 | Concurrent printers | Print on two printers simultaneously | Each printer tracked independently, no cross-contamination |

---

## 9. UI/UX Checks

| # | Test | Steps | Expected |
|---|------|-------|----------|
| 9.1 | Mobile layout | Open on mobile or narrow browser | Inventory page, assign modal, and settings all responsive |
| 9.2 | Dark/light mode | Toggle theme | All inventory UI elements properly themed |
| 9.3 | Language switching | Switch to German and Japanese | All inventory strings translated (no hardcoded English) |
| 9.4 | Keyboard nav | Use keyboard shortcuts on printers page | AMS slot interactions accessible |

---

## Reporting Issues

When reporting a bug, please include:
- Test case number (e.g., "Beta 9.1 failed")
- Browser + version
- Screenshot or screen recording
- Backend logs (if relevant — check Docker logs)
- Steps to reproduce if different from above
