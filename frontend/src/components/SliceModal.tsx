import { Cloud, CloudOff, Cog, Loader2, Package, RefreshCw, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type PresetRef,
  type PresetSource,
  type SliceBundleSpec,
  type SliceJobProgress,
  type SliceRequest,
  type SlicerBundle,
  type SlicerCloudStatus,
  type UnifiedPreset,
  type UnifiedPresetsBySlot,
  type UnifiedPresetsResponse,
} from '../api/client';
import { useSliceJobTracker } from '../contexts/SliceJobTrackerContext';
import { useToast } from '../contexts/ToastContext';
import { PlatePickerModal } from './PlatePickerModal';
import type { PlateFilament } from '../types/plates';
import { normalizeColorForCompare, colorsAreSimilar } from '../utils/amsHelpers';
import {
  presetCompatibility,
  buildCompatibilityIndex,
  EMPTY_COMPATIBILITY_INDEX,
  type PrinterCompatibilityIndex,
} from '../utils/slicerPrinterMatch';

export type SliceSource =
  | { kind: 'libraryFile'; id: number; filename: string }
  | { kind: 'archive'; id: number; filename: string };

interface SliceModalProps {
  source: SliceSource;
  onClose: () => void;
}

type Slot = 'printer' | 'process' | 'filament';

// Lookup priority: local → orca_cloud → cloud → standard. Local imports
// outrank everything else because the user explicitly imported them for
// this install; Orca Cloud comes next; Bambu Cloud after that; standard
// (bundled) is the final fallback. The backend does NOT dedup tiers —
// every group renders its full set so the user can pick a same-named
// preset from a lower-priority source if they want to override the
// auto-pick.
const SLICE_MODAL_TIER_ORDER = ['local', 'orca_cloud', 'cloud', 'standard'] as const;

function pickDefault(by: UnifiedPresetsResponse, slot: Slot): PresetRef | null {
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    const list = by[tier][slot];
    if (list.length > 0) {
      return { source: list[0].source, id: list[0].id };
    }
  }
  return null;
}

// Resolve a PresetRef back to its UnifiedPreset within the named slot, or
// null if it no longer resolves (e.g. the preset was deleted between the
// listing fetch and selection).
function findPreset(
  by: UnifiedPresetsResponse,
  ref: PresetRef | null,
  slot: Slot,
): UnifiedPreset | null {
  if (!ref) return null;
  return by[ref.source][slot].find((p) => p.id === ref.id) ?? null;
}

// Find a preset by exact name across tiers (local → cloud → standard). Used
// to honour the printer / process preset names a 3MF was prepared with.
function findPresetByName(
  by: UnifiedPresetsResponse,
  slot: Slot,
  name: string | null | undefined,
): PresetRef | null {
  if (!name) return null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    const p = by[tier][slot].find((x) => x.name === name);
    if (p) return { source: p.source, id: p.id };
  }
  return null;
}

// Process default: honour the process preset the 3MF was prepared with
// (preferredName) when it's available and not incompatible with the selected
// printer; otherwise the first preset compatible with the printer in tier
// order, then the first whose compatibility is merely unknown, then plain
// priority. Keeps the pre-pick honest with both the embedded config and the
// printer filter instead of blindly taking list[0] (#1325).
function pickProcessDefault(
  by: UnifiedPresetsResponse,
  printerName: string | null,
  compatIndex: PrinterCompatibilityIndex,
  preferredName?: string | null,
): PresetRef | null {
  const preferred = findPresetByName(by, 'process', preferredName);
  if (preferred) {
    const p = findPreset(by, preferred, 'process');
    if (p && presetCompatibility(p, 'process', printerName, compatIndex) !== 'mismatch') {
      return preferred;
    }
  }
  for (const wanted of ['match', 'unknown'] as const) {
    for (const tier of SLICE_MODAL_TIER_ORDER) {
      for (const p of by[tier].process) {
        if (presetCompatibility(p, 'process', printerName, compatIndex) === wanted) {
          return { source: p.source, id: p.id };
        }
      }
    }
  }
  return pickDefault(by, 'process');
}

const TIER_BONUS: Record<PresetSource, number> = {
  local: 1.75,
  orca_cloud: 1.5,
  cloud: 1.0,
  standard: 0.5,
};

function pickFilamentForSlot(
  by: UnifiedPresetsResponse,
  required: { type: string; color: string },
  printerName: string | null,
  compatIndex: PrinterCompatibilityIndex,
): PresetRef | null {
  // Score every filament preset against the plate slot's required (type,
  // colour) and pick the highest. Mirrors the AMS slot-mapping match in the
  // print/schedule modal: type match dominates, exact-colour-match bumps over
  // similar-colour-match, and a small per-tier bonus breaks ties so cloud
  // user customisations win over standard bundled fallbacks of equal merit.
  const reqType = required.type.trim().toUpperCase();
  const reqColor = normalizeColorForCompare(required.color);

  let best: { ref: PresetRef; score: number } | null = null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    for (const p of by[tier].filament) {
      let score = 0;
      const presetType = (p.filament_type ?? '').trim().toUpperCase();
      const presetColor = normalizeColorForCompare(p.filament_colour ?? '');
      if (reqType && presetType && reqType === presetType) score += 10;
      if (reqColor && presetColor) {
        if (presetColor === reqColor) score += 5;
        else if (colorsAreSimilar(p.filament_colour ?? '', required.color)) score += 2;
      }
      score += TIER_BONUS[tier];
      // Demote printer-incompatible filaments (#1325): a penalty rather than a
      // hard skip so the pick still degrades gracefully if every filament
      // mismatches the selected printer.
      if (presetCompatibility(p, 'filament', printerName, compatIndex) === 'mismatch') {
        score -= 100;
      }
      if (best == null || score > best.score) {
        best = { ref: { source: p.source, id: p.id }, score };
      }
    }
  }
  // Fall back to plain priority pick if every preset scored 0+tier (i.e. no
  // metadata matched). The fallback is exactly the single-color default —
  // first preset in the highest-priority non-empty tier.
  if (best == null) return pickDefault(by, 'filament');
  return best.ref;
}

function toRefValue(ref: PresetRef | null): string {
  // The HTML `<select>` value space is flat strings; encode source + id so
  // the same preset name can live in multiple tiers without collision.
  return ref ? `${ref.source}:${ref.id}` : '';
}

function fromRefValue(raw: string): PresetRef | null {
  if (!raw) return null;
  const idx = raw.indexOf(':');
  if (idx < 0) return null;
  const source = raw.slice(0, idx) as PresetSource;
  const id = raw.slice(idx + 1);
  if (source !== 'orca_cloud' && source !== 'cloud' && source !== 'local' && source !== 'standard') return null;
  return { source, id };
}

// Inline spinner for the filament-requirements query. The backend runs a
// preview slice on first open of an unsliced project file (cached after);
// on a complex multi-color model that's a real slice — multi-second to
// multi-minute. The static "Analyzing plate filaments…" string left
// users wondering whether anything was happening, so the spinner now
// shows elapsed seconds, polls the sidecar's --pipe progress (via the
// /slicer/preview-progress proxy) for live stage + percent, and after ~5s
// surfaces a "this is a one-time slice — repeat opens are instant"
// note so users don't worry it'll be slow forever.
//
// requestId: a UUID generated by the modal when the filament-requirements
// fetch starts. Forwarded to the sidecar via the API call AND used here
// to poll the matching progress snapshot. Same id, two consumers.
function FilamentAnalysisSpinner({
  requestId,
  sourceName,
}: {
  requestId: string;
  sourceName: string;
}) {
  const { t } = useTranslation();
  const { showPersistentToast, dismissToast } = useToast();
  const [elapsed, setElapsed] = useState(0);
  const [progress, setProgress] = useState<SliceJobProgress | null>(null);
  // Defensive decode — see prettifyFilename comment in SliceJobTrackerContext.
  let prettyName = sourceName;
  try {
    prettyName = decodeURIComponent(sourceName);
  } catch {
    /* keep raw on malformed encoding */
  }

  // Elapsed-time tick.
  useEffect(() => {
    const startedAt = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  // Progress polling — once per second while the spinner is mounted.
  // Mirrors the slice-job tracker's cadence. Sidecar 404s during the
  // race window between fetch start and progressStore.start() are
  // swallowed by the API method (returns null) so we keep polling.
  useEffect(() => {
    let cancelled = false;
    const id = setInterval(async () => {
      if (cancelled) return;
      const snap = await api.getPreviewSliceProgress(requestId);
      if (!cancelled && snap) setProgress(snap);
    }, 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [requestId]);

  // Mirror the spinner's contents into a persistent toast so the user
  // sees activity even when their cursor is elsewhere on the page.
  // Dismissed in the parent's effect when the requirements arrive.
  const toastId = `slice-preview-${requestId}`;
  useEffect(() => {
    const hasUseful = progress && progress.stage && progress.total_percent > 0;
    const elapsedStr = formatElapsed(elapsed);
    if (hasUseful) {
      showPersistentToast(
        toastId,
        t(
          'slice.previewWithProgress',
          'Analyzing {{name}} — {{stage}} ({{percent}}%) — {{elapsed}}',
          {
            name: prettyName,
            stage: progress!.stage,
            percent: Math.min(100, Math.max(0, Math.round(progress!.total_percent))),
            elapsed: elapsedStr,
          },
        ),
        'loading',
      );
    } else {
      showPersistentToast(
        toastId,
        t('slice.previewToast', {
          name: prettyName,
          elapsed: elapsedStr,
        }),
        'loading',
      );
    }
    return () => {
      dismissToast(toastId);
    };
  }, [elapsed, progress, prettyName, showPersistentToast, dismissToast, t, toastId]);

  const stage = progress?.stage;
  const percent = progress?.total_percent;
  const inlineLabel =
    stage && typeof percent === 'number' && percent > 0
      ? `${stage} (${Math.min(100, Math.max(0, Math.round(percent)))}%)`
      : t('slice.analyzingPlateFilaments');
  return (
    <div className="flex flex-col gap-1 text-bambu-gray text-sm py-2">
      <div className="flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" />
        {inlineLabel}
        <span className="text-xs tabular-nums">{elapsed}s</span>
      </div>
      {elapsed >= 5 && (
        <div className="text-xs text-bambu-gray/70 pl-6">
          {t(
            'slice.analyzingPlateFilamentsHint',
            'Running a preview slice to discover which AMS slots this plate uses. Cached after — re-opening is instant.',
          )}
        </div>
      )}
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m < 60) return `${m}m ${remS}s`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h ${remM}m`;
}

export function SliceModal({ source, onClose }: SliceModalProps) {
  const { t } = useTranslation();
  const { trackJob } = useSliceJobTracker();
  const queryClient = useQueryClient();

  const [printerPreset, setPrinterPreset] = useState<PresetRef | null>(null);
  const [processPreset, setProcessPreset] = useState<PresetRef | null>(null);
  // One filament ref per plate slot, in plate order. For STL / single-plate /
  // single-color sources this is a one-element array; multi-color 3MFs get one
  // entry per AMS slot the plate uses. Pre-pick (effect below) initialises
  // each slot from the source plate's required (type, colour).
  const [filamentPresets, setFilamentPresets] = useState<(PresetRef | null)[]>([]);
  // Bundle dispatch (alternative to the preset triplet). When non-null, the
  // SliceModal hides the cloud/local/standard preset dropdowns and shows
  // bundle-scoped pickers (process + per-slot filament from the chosen
  // bundle's contents). Submit routes through the backend's bundle dispatch
  // (`SliceRequest.bundle`) which skips PresetRef resolution.
  const [selectedBundleId, setSelectedBundleId] = useState<string | null>(null);
  const [bundleProcessName, setBundleProcessName] = useState<string | null>(null);
  const [bundleFilamentNames, setBundleFilamentNames] = useState<(string | null)[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // null = plate not yet picked (or single-plate / non-3MF — picker is skipped
  // and we'll backfill 1 at submit time). Set to a 1-indexed plate number once
  // the user picks one (or implicitly for single-plate sources).
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);
  // "Slice all plates" mode: sends ``plate=0`` to the backend which forwards
  // ``--slice 0`` to the BS CLI, producing a single output 3MF whose
  // ``Metadata/plate_N.gcode`` entries are *all* plates sliced together —
  // one archive, one file, all plates. Distinct from the per-plate
  // ``selectedPlate`` mode (which slices just that one plate). Filament
  // selection in this mode covers every slot the project defines, not
  // just the slots the currently-visible plate happens to use — see
  // ``allProjectFilamentSlots`` below.
  const [sliceAllPlates, setSliceAllPlates] = useState(false);
  // Build-plate override (#1337). null = inherit from the process preset
  // (the default). Set to a canonical slicer enum value to patch
  // curr_bed_type into the resolved process JSON before slicing — needed
  // because the process preset's default plate (typically "Cool Plate") is
  // incompatible with high-temp filaments like ABS / ASA / PC, and the
  // user had no way to switch plates without cloning the preset.
  const [bedType, setBedType] = useState<string | null>(null);

  const platesQuery = useQuery({
    queryKey: ['slicePlates', source.kind, source.id],
    queryFn: async () => {
      if (source.kind === 'libraryFile') {
        return api.getLibraryFilePlates(source.id);
      }
      return api.getArchivePlates(source.id);
    },
    staleTime: 60_000,
  });

  const isMultiPlate =
    !!platesQuery.data?.is_multi_plate && (platesQuery.data?.plates?.length ?? 0) > 1;
  // Single-plate / non-3MF / fetch failure: skip the picker, default to plate 1
  // at submit time so the backend's existing default behaviour is preserved.
  const needsPlatePicker = isMultiPlate && selectedPlate == null;

  // Per-plate filament requirements via the same endpoint the print/schedule
  // modal uses. Reusing it here keeps the SliceModal honest with whatever
  // logic that endpoint applies (slice_info parsing, future enhancements for
  // unsliced project files, dual-nozzle fields, etc.) instead of duplicating
  // extraction. plate_id is always sent: single-plate falls through to plate
  // 1 server-side; multi-plate uses the user's pick.
  const effectivePlateId = selectedPlate ?? 1;
  // Generate a request_id per (source, plate) pair so the backend's
  // preview-slice and the FilamentAnalysisSpinner's progress poll share
  // the same id. useMemo keeps it stable across renders within the same
  // pair; switching plates regenerates so a stale poll doesn't bleed
  // progress between plates.
  const previewRequestId = useMemo(() => {
    const random =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    // Tag the id with the (source, plate) so logs/Network panel show which
    // pair owns the poll. Also lets the lint rule see the deps in use.
    return `${source.kind}-${source.id}-p${effectivePlateId}-${random}`;
  }, [source.kind, source.id, effectivePlateId]);
  const filamentReqsQuery = useQuery({
    queryKey: ['sliceFilamentReqs', source.kind, source.id, effectivePlateId],
    queryFn: async () => {
      if (source.kind === 'libraryFile') {
        return api.getLibraryFileFilamentRequirements(source.id, effectivePlateId, previewRequestId);
      }
      return api.getArchiveFilamentRequirements(source.id, effectivePlateId, previewRequestId);
    },
    enabled: !needsPlatePicker,
    staleTime: 60_000,
  });

  // Filament slot list for the active plate. Falls back to one synthetic slot
  // for STL/STEP and any "no metadata available" case so the modal still
  // works (single dropdown, mono-color slice). In ``sliceAllPlates`` mode
  // we keep the same slot list (the backend already returns every project
  // slot via ``extract_project_filaments_from_3mf``'s fallback path when
  // slice_info doesn't carry per-plate filaments) but override every
  // slot's ``used_in_plate`` flag to ``true`` so the dropdown labels
  // drop the "— not used by this plate" suffix and the dropdowns become
  // selectable. Across the whole project, every defined slot IS used by
  // at least one plate, so this is correct in slice-all mode.
  const filamentSlots = useMemo<PlateFilament[]>(() => {
    const reqs = filamentReqsQuery.data?.filaments ?? [];
    const base: PlateFilament[] =
      reqs.length > 0
        ? (reqs as PlateFilament[])
        : [{ slot_id: 1, type: '', color: '', used_grams: 0, used_meters: 0 }];
    if (sliceAllPlates) {
      return base.map((slot) => ({ ...slot, used_in_plate: true }));
    }
    return base;
  }, [sliceAllPlates, filamentReqsQuery.data]);

  const presetsQuery = useQuery({
    queryKey: ['slicerPresets'],
    queryFn: () => api.getSlicerPresets(),
    staleTime: 60_000,
    // Don't fetch presets while the plate picker is on screen — saves a
    // round-trip if the user cancels out of the plate step.
    enabled: !platesQuery.isLoading && !needsPlatePicker,
  });

  // Manual refresh — bypasses the backend's 5-minute cloud cache and 1-hour
  // bundled cache for one call so users who deleted a preset in Bambu
  // Studio / Bambu Handy see the change immediately (#1581). The cache write
  // inside _fetch_cloud_presets / _fetch_bundled_presets refills with the
  // fresh result so subsequent normal callers still get cached responses.
  const [isRefreshing, setIsRefreshing] = useState(false);
  const handleRefreshPresets = async () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    try {
      const fresh = await api.getSlicerPresets({ refresh: true });
      queryClient.setQueryData(['slicerPresets'], fresh);
    } catch {
      // Fall through to invalidate so React Query retries via its normal
      // path on the next render — surfacing the failure through the existing
      // presetsQuery.isError banner instead of duplicating error UI here.
      queryClient.invalidateQueries({ queryKey: ['slicerPresets'] });
    } finally {
      setIsRefreshing(false);
    }
  };

  // Imported Printer Preset Bundles (.bbscfg). Empty list when no sidecar
  // configured / no bundles imported yet; the bundle picker hides itself
  // in that case so users without bundles see the original modal layout.
  const bundlesQuery = useQuery({
    queryKey: ['slicerBundles'],
    queryFn: api.listSlicerBundles,
    staleTime: 60_000,
    enabled: !platesQuery.isLoading && !needsPlatePicker,
    // Bundle listing is a hard 503 when the sidecar is offline; don't
    // retry tight loops in that case.
    retry: false,
  });
  // Canonical Bambu printer-model registry — drives the @BBL <code> name
  // fallback in slicerPrinterMatch when no slicer bundle covers a cloud /
  // standard preset (#1325 follow-up). Long staleTime: the registry only
  // changes across backend releases.
  const printerModelsQuery = useQuery({
    queryKey: ['slicerPrinterModels'],
    queryFn: api.getSlicerPrinterModels,
    staleTime: Infinity,
  });
  const selectedBundle: SlicerBundle | null = useMemo(() => {
    if (!selectedBundleId || !bundlesQuery.data) return null;
    return bundlesQuery.data.find((b) => b.id === selectedBundleId) ?? null;
  }, [selectedBundleId, bundlesQuery.data]);
  const isBundleMode = selectedBundle != null;

  // Selected-printer context for the process / filament filter (#1325).
  const selectedPrinterName = useMemo<string | null>(() => {
    if (!presetsQuery.data || !printerPreset) return null;
    return findPreset(presetsQuery.data, printerPreset, 'printer')?.name ?? null;
  }, [presetsQuery.data, printerPreset]);
  // Compatibility ground truth: the user's uploaded Slicer Bundles plus the
  // backend Bambu printer-model registry (#1325 + follow-up). The bundle
  // path handles imported / custom presets; the registry-driven @BBL name
  // fallback inside slicerPrinterMatch picks up cloud / standard presets
  // for users who haven't uploaded bundles yet.
  const compatIndex = useMemo<PrinterCompatibilityIndex>(
    () => buildCompatibilityIndex(bundlesQuery.data ?? [], printerModelsQuery.data ?? {}),
    [bundlesQuery.data, printerModelsQuery.data],
  );

  // Printer / process preset names the source 3MF was prepared with. The
  // plates query resolves before the presets query (the latter is gated on
  // it), so these are known by the time the pre-pick effects run.
  const embeddedPrinter = platesQuery.data?.embedded_printer ?? null;
  const embeddedProcess = platesQuery.data?.embedded_process ?? null;

  // Printer pre-pick: defaults to the printer the 3MF was prepared for when
  // that preset is available, else the first listed printer. Runs once when
  // presets first arrive; later re-renders preserve any manual choice.
  useEffect(() => {
    const data = presetsQuery.data;
    if (!data) return;
    if (printerPreset == null) {
      setPrinterPreset(
        findPresetByName(data, 'printer', embeddedPrinter) ?? pickDefault(data, 'printer'),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetsQuery.data, embeddedPrinter]);

  // Process pre-pick / re-pick (#1325): defaults to a process compatible with
  // the selected printer, and re-defaults when a printer change leaves the
  // current process incompatible. A compatible or unknown manual pick is kept.
  useEffect(() => {
    const data = presetsQuery.data;
    if (!data) return;
    setProcessPreset((current) => {
      if (current) {
        const p = findPreset(data, current, 'process');
        if (p && presetCompatibility(p, 'process', selectedPrinterName, compatIndex) !== 'mismatch') {
          return current;
        }
      }
      return pickProcessDefault(data, selectedPrinterName, compatIndex, embeddedProcess);
    });
  }, [presetsQuery.data, selectedPrinterName, compatIndex, embeddedProcess]);

  // Filament pre-pick: re-runs when the active filament-slot count changes
  // (plate selection, single-plate metadata arriving) or the selected printer
  // changes. Each slot scores every available filament preset against the
  // slot's required (type, colour); an existing pick (incl. a user override)
  // is kept as long as it's still compatible with the selected printer, while
  // null slots and printer-incompatible picks are re-picked (#1325).
  useEffect(() => {
    const data = presetsQuery.data;
    if (!data) return;
    setFilamentPresets((current) => {
      return filamentSlots.map((slot, i) => {
        const cur = current[i] ?? null;
        if (cur) {
          const p = findPreset(data, cur, 'filament');
          if (p && presetCompatibility(p, 'filament', selectedPrinterName, compatIndex) !== 'mismatch') {
            return cur;
          }
        }
        return pickFilamentForSlot(
          data,
          { type: slot.type, color: slot.color },
          selectedPrinterName,
          compatIndex,
        );
      });
    });
  }, [presetsQuery.data, filamentSlots, selectedPrinterName, compatIndex]);

  // Bundle-mode auto-pick: when the user picks a bundle (or the slot count
  // changes after the picker is open), default the process to the bundle's
  // first listed process and every filament slot to the bundle's first
  // listed filament. Plain string match — bundles store delta files keyed
  // by user preset name, no scoring needed since the user picks per-slot
  // afterwards if the default is wrong.
  useEffect(() => {
    if (!selectedBundle) {
      // Reset bundle picks when bundle is cleared so re-selection
      // re-defaults rather than carrying stale values.
      setBundleProcessName(null);
      setBundleFilamentNames([]);
      return;
    }
    setBundleProcessName((current) => {
      // Preserve a manual pick if it still exists in the bundle; otherwise
      // re-default. Same shape as the preset auto-pick effect above.
      if (current && selectedBundle.process.includes(current)) return current;
      return selectedBundle.process[0] ?? null;
    });
    setBundleFilamentNames((current) => {
      if (current.length === filamentSlots.length && current.every((n) => n != null)) {
        return current;
      }
      const fallback = selectedBundle.filament[0] ?? null;
      return filamentSlots.map((_, i) => current[i] ?? fallback);
    });
  }, [selectedBundle, filamentSlots]);

  const enqueueMutation = useMutation({
    mutationFn: async (plate: number | null) => {
      const body = buildSliceBody(plate);
      if (source.kind === 'libraryFile') {
        return api.sliceLibraryFile(source.id, body);
      }
      return api.sliceArchive(source.id, body);
    },
    onSuccess: (enqueue) => {
      trackJob(enqueue.job_id, source.kind, source.filename);
      onClose();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(msg);
    },
  });

  // Body builder shared by the single-plate and slice-all paths. ``plate``
  // is the 1-indexed plate number to slice, or ``null`` for STL / single-
  // plate 3MF sources where the field is omitted entirely.
  function buildSliceBody(plate: number | null): SliceRequest {
    if (isBundleMode) {
      if (
        !selectedBundle ||
        !bundleProcessName ||
        bundleFilamentNames.length === 0 ||
        bundleFilamentNames.some((n) => n == null)
      ) {
        throw new Error(t('slice.bundleAllRequired'));
      }
      const bundleSpec: SliceBundleSpec = {
        bundle_id: selectedBundle.id,
        printer_name: selectedBundle.printer[0] ?? selectedBundle.printer_preset_name,
        process_name: bundleProcessName,
        filament_names: bundleFilamentNames as string[],
      };
      return {
        bundle: bundleSpec,
        ...(plate != null ? { plate } : {}),
        // Bed-type override (#1337) also flows through the bundle path —
        // the sidecar forwards `bedType` as --curr_bed_type to the CLI.
        ...(bedType != null ? { bed_type: bedType } : {}),
      };
    }
    if (
      !printerPreset ||
      !processPreset ||
      filamentPresets.length === 0 ||
      filamentPresets.some((r) => r == null)
    ) {
      throw new Error(t('slice.allPresetsRequired'));
    }
    return {
      printer_preset: printerPreset,
      process_preset: processPreset,
      filament_preset: filamentPresets[0] as PresetRef,
      filament_presets: filamentPresets as PresetRef[],
      ...(plate != null ? { plate } : {}),
      ...(bedType != null ? { bed_type: bedType } : {}),
    };
  }


  // Slice button stays disabled until the preview slice / embedded-metadata
  // read has succeeded (filamentReqsQuery.isSuccess) and every filament slot
  // has a picked profile.
  const isReady = isBundleMode
    ? selectedBundle != null &&
      bundleProcessName != null &&
      filamentReqsQuery.isSuccess &&
      bundleFilamentNames.length > 0 &&
      bundleFilamentNames.every((n) => n != null)
    : printerPreset != null &&
      processPreset != null &&
      filamentReqsQuery.isSuccess &&
      filamentPresets.length > 0 &&
      filamentPresets.every((r) => r != null);
  const isEnqueuing = enqueueMutation.isPending;
  const totalPlateCount = platesQuery.data?.plates?.length ?? 0;
  const canSliceAll = isMultiPlate && totalPlateCount > 1 && !needsPlatePicker;

  // Step 1: plate picker for multi-plate 3MF sources. Cancelling closes the
  // entire flow (matches the existing PlatePickerModal contract used by the
  // archive g-code-viewer entry point).
  if (needsPlatePicker && platesQuery.data) {
    return (
      <PlatePickerModal
        plates={platesQuery.data.plates}
        onSelect={(plateIndex) => setSelectedPlate(plateIndex)}
        onClose={onClose}
      />
    );
  }

  // Step 2 (or only step for single-plate / non-3MF / load-failure): preset
  // picker. While the plates query is in-flight we still render the shell
  // because the presets query is gated on it; the loader covers both.
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={() => {
        if (!isEnqueuing) onClose();
      }}
    >
      <div
        className="w-full max-w-xl max-h-[85vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary/60"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-bambu-dark-tertiary/40">
          <div className="min-w-0">
            <h3 className="text-white font-medium flex items-center gap-2">
              <Cog className="w-4 h-4" />
              {t('slice.title')}
            </h3>
            <p className="text-xs text-bambu-gray mt-1 truncate" title={source.filename}>
              {source.filename}
              {selectedPlate != null
                ? ` • ${t('archives.platePicker.plateLabel', { index: selectedPlate })}`
                : ''}
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={isEnqueuing}
            className="flex-shrink-0 text-bambu-gray hover:text-white transition-colors disabled:opacity-50"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Preset listing loader — printer/process dropdowns can't render
              without it. Plate query reuses the same spinner since it's
              also blocking. */}
          {(platesQuery.isLoading || presetsQuery.isLoading) && (
            <div className="flex items-center gap-2 text-bambu-gray text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('slice.loadingPresets')}
            </div>
          )}

          {presetsQuery.isError && (
            <div className="text-sm text-red-400" role="alert">
              {t(
                'slice.presetsLoadFailed',
                'Failed to load presets. Open Settings → Profiles to import them, or sign in to Bambu Cloud.',
              )}
            </div>
          )}

          {presetsQuery.data && (
            <>
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 space-y-2">
                  <CloudStatusBanner status={presetsQuery.data.cloud_status} cloudName="bambu" />
                  <CloudStatusBanner status={presetsQuery.data.orca_cloud_status} cloudName="orca" />
                </div>
                <button
                  type="button"
                  onClick={handleRefreshPresets}
                  disabled={isRefreshing || isEnqueuing}
                  className="flex-shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary/40 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title={t('slice.refreshPresetsTitle')}
                  aria-label={t('slice.refreshPresets')}
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
                  {t('slice.refreshPresets')}
                </button>
              </div>
              {/* CloudStatusBanner above is hidden via flex-1 wrapper when
                  status === 'ok' (returns null in that case), but the Refresh
                  button stays visible regardless so users can pick up cloud /
                  bundled changes even when sign-in is healthy. */}
              {/* Bundle picker — only renders when at least one .bbscfg has
                  been imported via Settings → Slicer Bundles. Lets the user
                  trade the cloud/local/standard tier for a single curated
                  triplet from a previously-uploaded BambuStudio bundle. */}
              {bundlesQuery.data && bundlesQuery.data.length > 0 && (
                <BundlePicker
                  bundles={bundlesQuery.data}
                  selectedId={selectedBundleId}
                  onChange={setSelectedBundleId}
                  disabled={isEnqueuing}
                />
              )}
              {/* Preset triplet — hidden when a bundle is selected so the
                  user only sees one tier at a time. The bundle's process +
                  filament dropdowns render below in their stead. */}
              {!isBundleMode && (
                <>
                  <PresetDropdown
                    label={t('slice.printer')}
                    slot="printer"
                    data={presetsQuery.data}
                    value={printerPreset}
                    onChange={setPrinterPreset}
                    disabled={isEnqueuing}
                  />
                  <PresetDropdown
                    label={t('slice.process')}
                    slot="process"
                    data={presetsQuery.data}
                    value={processPreset}
                    onChange={setProcessPreset}
                    disabled={isEnqueuing}
                    selectedPrinterName={selectedPrinterName}
                    compatIndex={compatIndex}
                  />
                </>
              )}
              {isBundleMode && selectedBundle && (
                <>
                  {/* Bundle's printer is implicit (each .bbscfg has exactly
                      one). Show it as a read-only label so the user can
                      verify the printer they're slicing for. */}
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('slice.printer')}
                    </label>
                    <div className="px-3 py-2 rounded-md bg-bambu-dark/40 border border-bambu-dark-tertiary text-white text-sm">
                      {selectedBundle.printer_preset_name}
                    </div>
                  </div>
                  <BundleStringDropdown
                    label={t('slice.process')}
                    options={selectedBundle.process}
                    value={bundleProcessName}
                    onChange={setBundleProcessName}
                    disabled={isEnqueuing}
                  />
                </>
              )}
              {/* Bed-type override (#1337). Always visible, always enabled.
                  In non-bundle mode the backend patches curr_bed_type on the
                  resolved process JSON before forwarding to the sidecar; in
                  bundle mode the same value rides through as a sidecar form
                  field so the bundle's materialised process JSON gets the
                  override applied there too. */}
              <BedTypeDropdown
                value={bedType}
                onChange={setBedType}
                disabled={isEnqueuing}
              />
              {/* Filament reqs may need a server-side preview-slice for
                  unsliced project files (single-pass, then cached). Show a
                  scoped spinner so the user sees the printer/process
                  dropdowns instead of an opaque "Loading presets…" wait. */}
              {filamentReqsQuery.isLoading ? (
                <FilamentAnalysisSpinner
                  requestId={previewRequestId}
                  sourceName={source.filename}
                />
              ) : isBundleMode && selectedBundle ? (
                filamentSlots.map((slot, idx) => {
                  const isUsed = slot.used_in_plate !== false;
                  const baseLabel =
                    filamentSlots.length > 1
                      ? t('slice.filamentSlot', {
                          index: idx + 1,
                          type: slot.type,
                        })
                      : t('slice.filament');
                  const label = isUsed
                    ? baseLabel
                    : `${baseLabel} ${t('slice.notUsedByPlate')}`;
                  return (
                    <BundleStringDropdown
                      key={`bundle-filament-${idx}`}
                      label={label}
                      options={selectedBundle.filament}
                      value={bundleFilamentNames[idx] ?? null}
                      onChange={(name) =>
                        setBundleFilamentNames((current) => {
                          const next = current.length === filamentSlots.length
                            ? [...current]
                            : filamentSlots.map((_, i) => current[i] ?? null);
                          next[idx] = name;
                          return next;
                        })
                      }
                      disabled={isEnqueuing || !isUsed}
                      swatchColor={filamentSlots.length > 1 ? slot.color : undefined}
                    />
                  );
                })
              ) : (
                filamentSlots.map((slot, idx) => {
                  // Slots flagged by the backend as not used by the
                  // picked plate are auto-picked from project metadata
                  // and disabled — the slicer CLI still needs a
                  // profile per project slot, but the user shouldn't
                  // have to think about slots their plate doesn't
                  // paint with. used_in_plate defaults to true when
                  // missing (sliced 3MFs and the no-flag legacy path).
                  const isUsed = slot.used_in_plate !== false;
                  const baseLabel =
                    filamentSlots.length > 1
                      ? t('slice.filamentSlot', {
                          index: idx + 1,
                          type: slot.type,
                        })
                      : t('slice.filament');
                  const label = isUsed
                    ? baseLabel
                    : `${baseLabel} ${t('slice.notUsedByPlate')}`;
                  return (
                    <PresetDropdown
                      key={`filament-${idx}`}
                      label={label}
                      slot="filament"
                      data={presetsQuery.data}
                      value={filamentPresets[idx] ?? null}
                      onChange={(ref) =>
                        setFilamentPresets((current) => {
                          const next = current.length === filamentSlots.length
                            ? [...current]
                            : filamentSlots.map((_, i) => current[i] ?? null);
                          next[idx] = ref;
                          return next;
                        })
                      }
                      disabled={isEnqueuing || !isUsed}
                      swatchColor={filamentSlots.length > 1 ? slot.color : undefined}
                      selectedPrinterName={selectedPrinterName}
                      compatIndex={compatIndex}
                    />
                  );
                })
              )}
            </>
          )}

          {errorMessage && (
            <div className="text-sm text-red-400 bg-red-900/20 border border-red-900/40 rounded p-2" role="alert">
              {errorMessage}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 flex justify-end gap-2 px-4 py-3 border-t border-bambu-dark-tertiary/40">
          <button
            type="button"
            onClick={onClose}
            disabled={isEnqueuing}
            className="px-3 py-1.5 text-sm rounded-md border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray transition-colors disabled:opacity-50"
          >
            {t('common.cancel')}
          </button>
          {canSliceAll && (
            <label
              className="flex items-center gap-2 mr-auto text-sm text-bambu-gray cursor-pointer select-none"
              title={t('slice.actionAllTitle', { count: totalPlateCount })}
            >
              <input
                type="checkbox"
                checked={sliceAllPlates}
                onChange={(e) => setSliceAllPlates(e.target.checked)}
                disabled={isEnqueuing}
                className="cursor-pointer"
              />
              {t('slice.allPlatesToggle', { count: totalPlateCount })}
            </label>
          )}
          <button
            type="button"
            onClick={() => {
              setErrorMessage(null);
              // ``plate=0`` is the sidecar's "all plates" sentinel — passes
              // ``--slice 0`` to the BS CLI which produces a single 3MF
              // with one ``Metadata/plate_N.gcode`` entry per plate.
              const platePayload = sliceAllPlates ? 0 : selectedPlate;
              enqueueMutation.mutate(platePayload);
            }}
            disabled={!isReady || isEnqueuing}
            className="px-3 py-1.5 text-sm rounded-md bg-bambu-green hover:bg-bambu-green/90 text-bambu-dark font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {isEnqueuing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('slice.enqueuing')}
              </>
            ) : sliceAllPlates ? (
              t('slice.actionAll', { count: totalPlateCount })
            ) : (
              t('slice.action')
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function CloudStatusBanner({
  status,
  cloudName = 'bambu',
}: {
  status: SlicerCloudStatus;
  cloudName?: 'bambu' | 'orca';
}) {
  const { t } = useTranslation();
  // `ok` is the happy path. `not_authenticated` is silenced too: a user who
  // hasn't signed in (or has explicitly logged out — #1712) doesn't need a
  // permanent nag at the top of the modal; sign-in lives on the Profiles
  // page if they want it. Only `expired` and `unreachable` surface — those
  // are real breakage states a previously-signed-in user needs to see.
  if (status === 'ok' || status === 'not_authenticated') return null;

  // Same status vocabulary for both Bambu and Orca Cloud — only the
  // user-facing text varies. The fallbacks below name each cloud explicitly
  // so the banner makes sense without translation when i18n hasn't been
  // updated for a new locale.
  const messages =
    cloudName === 'orca'
      ? {
          expired: {
            key: 'slice.orcaCloud.expired',
            fallback: 'Orca Cloud session expired — sign in again to refresh your Orca presets.',
          },
          unreachable: {
            key: 'slice.orcaCloud.unreachable',
            fallback: 'Orca Cloud is unreachable right now. Other presets still work.',
          },
        }
      : {
          expired: {
            key: 'slice.cloud.expired',
            fallback: 'Bambu Cloud session expired — sign in again to refresh your cloud presets.',
          },
          unreachable: {
            key: 'slice.cloud.unreachable',
            fallback: 'Bambu Cloud is unreachable right now. Local and standard presets still work.',
          },
        };

  const tones: Record<'expired' | 'unreachable', { tone: string; icon: typeof Cloud }> = {
    expired: {
      tone: 'border-amber-700/40 bg-amber-900/20 text-amber-200',
      icon: CloudOff,
    },
    unreachable: {
      tone: 'border-bambu-dark-tertiary/40 bg-bambu-dark text-bambu-gray',
      icon: CloudOff,
    },
  };
  const { tone, icon: Icon } = tones[status];
  const { key, fallback } = messages[status];
  return (
    <div className={`flex items-start gap-2 text-xs rounded-md border p-2 ${tone}`} role="status">
      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <span>{t(key, fallback)}</span>
    </div>
  );
}

// Build-plate options offered in the SliceModal (#1337). Values are the
// canonical strings the slicer's StaticPrintConfig validator accepts as
// `curr_bed_type` — BambuStudio is the default sidecar, so this matches its
// enum; OrcaSlicer accepts the same set with a Supertack alias that users
// can target via the same dropdown if they re-import their presets.
const BED_TYPE_OPTIONS: { value: string; labelKey: string; fallback: string }[] = [
  { value: 'Cool Plate', labelKey: 'slice.bedType.coolPlate', fallback: 'Cool Plate' },
  {
    value: 'Cool Plate (SuperTack)',
    labelKey: 'slice.bedType.coolPlateSuperTack',
    fallback: 'Cool Plate SuperTack',
  },
  { value: 'Engineering Plate', labelKey: 'slice.bedType.engineering', fallback: 'Engineering Plate' },
  { value: 'High Temp Plate', labelKey: 'slice.bedType.highTemp', fallback: 'High Temp Plate' },
  { value: 'Textured PEI Plate', labelKey: 'slice.bedType.texturedPEI', fallback: 'Textured PEI Plate' },
  { value: 'Smooth PEI Plate', labelKey: 'slice.bedType.smoothPEI', fallback: 'Smooth PEI Plate' },
];

function BedTypeDropdown({
  value,
  onChange,
  disabled,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="block text-xs text-bambu-gray mb-1">
        {t('slice.bedType.label')}
      </span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        disabled={disabled}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">{t('slice.bedType.auto')}</option>
        {BED_TYPE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {t(opt.labelKey, opt.fallback)}
          </option>
        ))}
      </select>
    </label>
  );
}

interface PresetDropdownProps {
  label: string;
  slot: Slot;
  data: UnifiedPresetsResponse;
  value: PresetRef | null;
  onChange: (ref: PresetRef | null) => void;
  disabled?: boolean;
  // Optional colour swatch shown next to the label — used for multi-color
  // filament slots so the user can see at a glance which slot they're
  // configuring against the source 3MF's per-slot colour.
  swatchColor?: string;
  // Selected printer context (#1325). When provided for a process / filament
  // slot, presets that resolve to a different printer (per the uploaded
  // Slicer Bundles in compatIndex) move into a trailing "Other printers"
  // group instead of the main tier list.
  selectedPrinterName?: string | null;
  compatIndex?: PrinterCompatibilityIndex;
}

function PresetDropdown({
  label,
  slot,
  data,
  value,
  onChange,
  disabled,
  swatchColor,
  selectedPrinterName,
  compatIndex,
}: PresetDropdownProps) {
  const { t } = useTranslation();

  // Tier sections (imported → cloud → standard), plus — for a process /
  // filament slot with a selected printer — a trailing group of presets that
  // resolve to a different printer (#1325). Compatibility-unknown presets
  // stay in their tier, so a custom / untagged preset is never hidden, and
  // empty sections collapse out.
  const { sections, otherEntries } = useMemo(() => {
    const tiers: { key: keyof UnifiedPresetsResponse; label: string; fallback: string }[] = [
      { key: 'local', label: 'slice.tier.local', fallback: 'Imported' },
      { key: 'orca_cloud', label: 'slice.tier.orcaCloud', fallback: 'Orca Cloud' },
      { key: 'cloud', label: 'slice.tier.cloud', fallback: 'Bambu Cloud' },
      { key: 'standard', label: 'slice.tier.standard', fallback: 'Standard' },
    ];
    const filterByPrinter = slot !== 'printer';
    const compatSections: { tierLabel: string; entries: UnifiedPreset[] }[] = [];
    const other: UnifiedPreset[] = [];
    for (const { key, label: lk, fallback } of tiers) {
      const entries = (data[key] as UnifiedPresetsBySlot)[slot];
      if (!filterByPrinter) {
        if (entries.length > 0) compatSections.push({ tierLabel: t(lk, fallback), entries });
        continue;
      }
      const compatible: UnifiedPreset[] = [];
      for (const p of entries) {
        if (
          presetCompatibility(
            p,
            // filterByPrinter is true here, so slot is never 'printer'.
            slot as 'process' | 'filament',
            selectedPrinterName ?? null,
            compatIndex ?? EMPTY_COMPATIBILITY_INDEX,
          ) === 'mismatch'
        ) {
          other.push(p);
        } else {
          compatible.push(p);
        }
      }
      if (compatible.length > 0) {
        compatSections.push({ tierLabel: t(lk, fallback), entries: compatible });
      }
    }
    return { sections: compatSections, otherEntries: other };
  }, [data, slot, t, selectedPrinterName, compatIndex]);

  const totalEntries =
    sections.reduce((sum, s) => sum + s.entries.length, 0) + otherEntries.length;

  return (
    <label className="block">
      <span className="flex items-center gap-2 text-xs text-bambu-gray mb-1">
        {swatchColor && (
          <span
            className="inline-block w-3 h-3 rounded-full border border-bambu-dark-tertiary"
            style={{ backgroundColor: swatchColor || 'transparent' }}
            aria-hidden
          />
        )}
        <span>{label}</span>
      </span>
      <select
        value={toRefValue(value)}
        onChange={(e) => onChange(fromRefValue(e.target.value))}
        disabled={disabled || totalEntries === 0}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">
          {totalEntries === 0
            ? t('slice.noPresetsForSlot')
            : t('slice.selectPreset')}
        </option>
        {sections.map((section) => (
          <optgroup key={section.tierLabel} label={section.tierLabel}>
            {section.entries.map((p) => (
              <option key={`${p.source}:${p.id}`} value={`${p.source}:${p.id}`}>
                {p.name}
              </option>
            ))}
          </optgroup>
        ))}
        {otherEntries.length > 0 && (
          <optgroup label={t('slice.otherPrinters')}>
            {otherEntries.map((p) => (
              <option key={`${p.source}:${p.id}`} value={`${p.source}:${p.id}`}>
                {p.name}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
}

// Top-of-modal bundle picker. The "None" option leaves the user on the
// cloud/local/standard tier path; selecting a bundle id flips the modal
// into bundle dispatch mode (see SliceModal state above).
interface BundlePickerProps {
  bundles: SlicerBundle[];
  selectedId: string | null;
  onChange: (id: string | null) => void;
  disabled?: boolean;
}

function BundlePicker({ bundles, selectedId, onChange, disabled }: BundlePickerProps) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="block text-sm text-bambu-gray mb-1 inline-flex items-center gap-1.5">
        <Package className="w-3.5 h-3.5" />
        {t('slice.bundle')}
      </span>
      <select
        value={selectedId ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        disabled={disabled}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">
          {t('slice.bundleNone')}
        </option>
        {bundles.map((b) => (
          <option key={b.id} value={b.id}>
            {b.printer_preset_name}
          </option>
        ))}
      </select>
    </label>
  );
}

// Plain-string dropdown used for bundle-mode process / filament selectors.
// Bundles store presets as a flat list of names within their printer-tied
// directory, so a `<select>` of strings is enough — no source tier, no
// optgroups. Same swatch / disabled affordances as the cloud/local/standard
// PresetDropdown above so the visual rhythm of the form stays consistent.
interface BundleStringDropdownProps {
  label: string;
  options: string[];
  value: string | null;
  onChange: (next: string | null) => void;
  disabled?: boolean;
  swatchColor?: string;
}

function BundleStringDropdown({
  label,
  options,
  value,
  onChange,
  disabled,
  swatchColor,
}: BundleStringDropdownProps) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="block text-sm text-bambu-gray mb-1 inline-flex items-center gap-1.5">
        {swatchColor && (
          <span
            className="inline-block w-3 h-3 rounded-sm border border-black/20"
            style={{ backgroundColor: swatchColor || 'transparent' }}
            aria-hidden
          />
        )}
        <span>{label}</span>
      </span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        disabled={disabled || options.length === 0}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">
          {options.length === 0
            ? t('slice.noPresetsForSlot')
            : t('slice.selectPreset')}
        </option>
        {options.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
