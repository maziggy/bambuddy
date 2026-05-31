# Bambuddy Fork — Development Notes

> Lessons from the WLED integration + CI fix marathon (May 30-31 2026).
> Read this before starting any new feature.

---

## What our fork adds to upstream files

These are the **only** fork-specific changes in each shared file. When a file
drifts far from upstream, the fix is `git checkout upstream/dev -- <file>` and
then re-apply just these sections.

### `backend/app/main.py`
- `poll_enclosure_sensors()`, `start_enclosure_polling()`, `stop_enclosure_polling()` functions
- `poll_storage_sensors()`, `start_storage_polling()`, `stop_storage_polling()` functions
- `start/stop_storage_polling()` called in lifespan startup/shutdown (after enclosure polling)
- `storage` router import and `app.include_router(storage.router, ...)`
- Call `start/stop_enclosure_polling()` in the lifespan startup/shutdown
- WLED status hook block inside `on_printer_status_change()` (after the dedup check)
- Three router registrations: `enclosure.router`, `enclosure_fan.router`, `wled.router`
- Import: `from backend.app.services.wled import wled_service`
- Import: `from backend.app.api.routes import enclosure, enclosure_fan` (added after `discovery`)

### `backend/app/api/routes/printers.py`
- Import: `from backend.app.services.homeassistant import homeassistant_service`
- Enclosure injection block in `get_printer_status()` — inserted after the chamber-temp
  filter block, before the archive/plate resolve block. Injects HA temp/humidity/fan
  readings into `temperatures` dict when the printer has HA entities configured.

### `frontend/src/pages/PrintersPage.tsx`
- Imports: `WledTestConnectionResult` (added to api/client type import), `EnclosureFanHistoryModal`, `EnclosureHistoryModal`
- Printer card: `showFanHistory` and `showEnclosureHistory` state vars
- Printer card JSX: full enclosure section (temp/humidity tiles, fan indicator, hints) before Smart Plug Controls
- Printer card JSX: `<EnclosureFanHistoryModal>` and `<EnclosureHistoryModal>` renders
- EditPrinterModal form state: `wled_enabled`, `wled_host`, `wled_port`, `wled_api_key` + `wledTestResult`/`wledTestLoading`
- EditPrinterModal submit: WLED field assignments before `updateMutation.mutate(data)`
- EditPrinterModal JSX: full WLED LED Strip section (after ROI section, before save button)

---

## Pre-commit checklist

```bash
# Before starting any new feature:
git fetch upstream && git rebase upstream/dev

# After any backend file change:
python -c "from backend.app.api.routes.printers import router; print('OK')"
python -c "from backend.app import main; print('OK')"

# Before every commit:
python -m ruff check backend/
python -m ruff format --check backend/

# After any change to frontend/src/i18n/locales/en.ts:
cd frontend && node scripts/check-i18n-parity.mjs

# Before using any Permission.* value in a new route:
grep -n "SETTINGS_\|PRINTERS_\|ADMIN" backend/app/core/permissions.py
```

---

## Key rules

### 1. Never patch diverged files function-by-function
When `main.py`, `printers.py`, `database.py`, or `PrintersPage.tsx` has drifted
significantly from upstream, **replace the whole file** with upstream then re-apply
the fork additions listed above. Patching individual missing pieces reveals a new
missing piece every CI run.

### 2. Import cascade danger
One missing schema class (e.g. `FilaSwitchResponse`) breaks `printers.py` →
`main.py` fails to load → 80+ tests fail with cryptic errors. Always run the
import verification commands above after touching any backend file.

### 3. i18n covers all 8 locales
`en.ts` is the reference. Every key must also exist in `de`, `es`, `fr`, `it`,
`ja`, `pt-BR`, `zh-CN`, `zh-TW`. The parity script also rejects strings that are
identical to English unless they're in the cognate allowlists. Add technical
format strings (like `{{name}} · WLED {{version}} · {{leds}} LEDs`) to the
allowlists in `frontend/scripts/check-i18n-parity.mjs`.

### 4. Permission enum — grep before you use
The enum uses names like `SETTINGS_UPDATE` (not `SETTINGS_WRITE`), `SETTINGS_READ`,
`PRINTERS_CONTROL`, `PRINTERS_READ`, `PRINTERS_CREATE`. Wrong names only fail at
test time, not at startup.

### 5. Build workflow targets `dev`
`fork-sync-build.yml` triggers on push to `dev` and builds the Docker image.
The auto-sync job (merging upstream into a feature branch) was removed — do
upstream syncs manually to avoid merge conflicts.

---

## Filament Storage Monitoring file inventory

| File | Purpose |
|------|---------|
| `backend/app/models/storage_unit.py` | `StorageUnit` ORM model (name, type, HA entities, notes) |
| `backend/app/models/storage_reading.py` | `StorageReading` ORM model (time-series temp/humidity) |
| `backend/app/api/routes/storage.py` | REST endpoints: CRUD + `/history` |
| `backend/app/core/database.py` | Migrations: `storage_units` + `storage_readings` tables |
| `backend/app/services/homeassistant.py` | `poll_storage_unit()`, `get_cached_storage()`, `invalidate_storage_cache()` |
| `frontend/src/api/client.ts` | `StorageUnit`, `StorageHistoryResponse` types + API methods |
| `frontend/src/i18n/locales/en.ts` | `nav.storage` + `storage.*` keys (35 keys) |
| `frontend/src/pages/FilamentStoragePage.tsx` | Main page: unit cards, add/edit/delete, filter tabs |
| `frontend/src/components/StorageHistoryModal.tsx` | Recharts temp + humidity history charts |
| `frontend/src/components/Layout.tsx` | `Thermometer` icon import + `storage` nav item |
| `frontend/src/App.tsx` | `FilamentStoragePage` import + `/storage` route |

---

## WLED feature file inventory

| File | Purpose |
|------|---------|
| `backend/app/services/wled.py` | WLED HTTP client, state map, cache |
| `backend/app/api/routes/wled.py` | REST endpoints (test-connection, presets, test-effect, invalidate-cache) |
| `backend/app/models/printer.py` | `wled_enabled`, `wled_host`, `wled_port`, `wled_api_key` columns |
| `backend/app/schemas/printer.py` | `WledStateConfig` in `AppSettings`; `WledTestConnectionResult` |
| `backend/app/core/database.py` | Migration: `ALTER TABLE printers ADD COLUMN wled_*` |
| `frontend/src/api/client.ts` | `testWledConnection()`, `triggerWledTestEffect()`, `getWledPresets()`, `invalidateWledCache()` |
| `frontend/src/i18n/locales/en.ts` | `printers.wled.*` and `settings.wled*` keys |
| `frontend/src/pages/SettingsPage.tsx` | Global WLED settings card (state map, enable toggle) |
| `frontend/src/pages/PrintersPage.tsx` | Per-printer WLED section in EditPrinterModal |

## HA Enclosure Sensors file inventory

| File | Purpose |
|------|---------|
| `backend/app/services/homeassistant.py` | HA REST client, entity listing, enclosure polling |
| `backend/app/api/routes/enclosure.py` | Enclosure readings endpoints |
| `backend/app/api/routes/enclosure_fan.py` | Fan run history endpoints |
| `backend/app/models/enclosure_reading.py` | `EnclosureReading` ORM model |
| `backend/app/models/enclosure_fan_run.py` | `EnclosureFanRun` ORM model |
| `backend/app/core/database.py` | Migration: `enclosure_readings` table, HA entity columns on printers |
| `frontend/src/components/EnclosureHistoryModal.tsx` | Temp/humidity chart modal |
| `frontend/src/components/EnclosureFanHistoryModal.tsx` | Fan run history modal |
