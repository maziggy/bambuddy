// Printer-compatibility matching for the SliceModal's process / filament
// dropdowns (#1325).
//
// Compatibility is resolved in this order, stopping on the first non-unknown
// answer:
//
//   1. Imported (local-tier) presets carry the slicer's own
//      `compatible_printers` list — an exact list of printer-preset names.
//   2. Uploaded Slicer Bundles (.bbscfg). A bundle is scoped to one printer
//      and lists the process / filament presets shipped with it, so a preset
//      a bundle covers is compatible with exactly that bundle's printer. A
//      newly released Bambu model is covered the moment its bundle is
//      uploaded — no code change required.
//   3. BambuStudio's own `@BBL <model>` naming convention on shipped cloud
//      / standard presets. This used to be the only signal, was removed in
//      the first cut of #1325 in favour of (2) — which works for the author
//      and anyone who uploaded their bundles, but silently no-ops for users
//      who hadn't (the reporter's case). Restored as a fallback below the
//      bundle path so the table is only consulted when bundles can't decide.
//      The token → printer-fragment table is derived from the backend's
//      canonical PRINTER_MODEL_MAP (fetched via /slicer/printer-models),
//      not duplicated here.
//
// The result drives grouping, not hard hiding: a preset no rule covers
// stays in the main list, and only a preset that resolves to a *different*
// printer is pushed into an "Other printers" group.

export type PrinterCompatibility = 'match' | 'mismatch' | 'unknown';

// Minimal shape of a Slicer Bundle needed for matching (see SlicerBundle in
// api/client.ts). `printer_preset_name` scopes the bundle to one printer;
// `process` / `filament` are the preset names that bundle ships.
export interface CompatibilityBundle {
  printer_preset_name: string;
  process: string[];
  filament: string[];
}

// Lookup tables consumed by `presetCompatibility`. `process` / `filament` are
// preset-name → set-of-compatible-printer-names built from uploaded bundles.
// `bambuModelByShortCode` is the @BBL token → printer-preset fragment map
// derived from the backend's PRINTER_MODEL_MAP — e.g. `X1C` → `X1 Carbon`.
// All three are empty by default; an empty `bambuModelByShortCode` means the
// @BBL fallback still works when token and printer-name fragment match
// directly (raw-token comparison), and gracefully degrades otherwise.
export interface PrinterCompatibilityIndex {
  process: Map<string, Set<string>>;
  filament: Map<string, Set<string>>;
  bambuModelByShortCode: Record<string, string>;
}

/** An empty index — used when no bundles / models are loaded yet. */
export const EMPTY_COMPATIBILITY_INDEX: PrinterCompatibilityIndex = {
  process: new Map(),
  filament: new Map(),
  bambuModelByShortCode: {},
};

// Bundle preset names occasionally carry BambuStudio's "# " user-clone
// prefix; strip it so a bundle entry and a tier-listed preset compare equal.
function normalizePresetName(name: string): string {
  return name.replace(/^#\s*/, '').trim();
}

/**
 * Invert the backend's PRINTER_MODEL_MAP into the shape the @BBL fallback
 * needs: short code → printer-preset fragment (the part of "Bambu Lab X1
 * Carbon" the user sees in a printer preset name, minus the "Bambu Lab "
 * brand prefix).
 *
 * Backend ships e.g. `{"Bambu Lab X1 Carbon": "X1C", "Bambu Lab A1 mini":
 * "A1 Mini", "Bambu Lab A1 Mini": "A1 Mini"}` — multiple long forms can map
 * to the same short. We pick the first long-form encountered for each short
 * code; case normalisation happens at match time so "A1 mini" vs "A1 Mini"
 * never matters.
 */
function buildShortCodeMap(
  printerModels: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [longName, shortCode] of Object.entries(printerModels)) {
    if (shortCode in out) continue;
    out[shortCode] = longName.replace(/^Bambu Lab\s+/, '');
  }
  return out;
}

/**
 * Build the compatibility index from the user's uploaded Slicer Bundles and
 * the backend printer-model registry. Each bundle contributes its printer
 * to every process / filament name it ships; a name shipped by several
 * bundles accumulates every printer.
 */
export function buildCompatibilityIndex(
  bundles: readonly CompatibilityBundle[],
  printerModels: Record<string, string> = {},
): PrinterCompatibilityIndex {
  const process = new Map<string, Set<string>>();
  const filament = new Map<string, Set<string>>();
  const add = (map: Map<string, Set<string>>, name: string, printer: string) => {
    const key = normalizePresetName(name);
    if (!key) return;
    const set = map.get(key) ?? new Set<string>();
    set.add(printer);
    map.set(key, set);
  };
  for (const bundle of bundles) {
    const printer = bundle.printer_preset_name?.trim();
    if (!printer) continue;
    for (const name of bundle.process) add(process, name, printer);
    for (const name of bundle.filament) add(filament, name, printer);
  }
  return {
    process,
    filament,
    bambuModelByShortCode: buildShortCodeMap(printerModels),
  };
}

function normalizeModelFragment(s: string): string {
  return s.replace(/\s+/g, '').toLowerCase();
}

// Pull the model token out of a "@BBL <token> [0.4 nozzle]" suffix. The token
// may contain a space (e.g. "A1 mini") so we strip a trailing nozzle-size
// segment rather than splitting on the first whitespace.
function extractBblToken(presetName: string): string | null {
  const marker = '@BBL ';
  const idx = presetName.indexOf(marker);
  if (idx < 0) return null;
  const rest = presetName.slice(idx + marker.length).trim();
  const cleaned = rest.replace(/\s+[\d.]+\s*nozzle\s*$/i, '').trim();
  return cleaned || null;
}

// Pull the model fragment out of a "Bambu Lab <model> [0.4 nozzle]" printer
// preset name. Returns null for non-Bambu printer presets — there is no
// reliable name-based match against those.
function extractPrinterPresetModel(printerPresetName: string): string | null {
  const m = printerPresetName.match(/^Bambu Lab\s+(.+?)(?:\s+[\d.]+\s*nozzle)?\s*$/i);
  return m ? m[1].trim() : null;
}

/**
 * Name-based fallback for presets BambuStudio ships with a `@BBL <model>`
 * tag (#1325 follow-up). Used only after `compatible_printers` and the
 * uploaded-bundle index have already returned `'unknown'`.
 */
function classifyByBambuName(
  presetName: string,
  selectedPrinterName: string,
  bambuModelByShortCode: Record<string, string>,
): PrinterCompatibility {
  const token = extractBblToken(presetName);
  if (!token) return 'unknown';
  // If the token isn't in the table (a brand-new Bambu model whose short
  // code the backend registry hasn't added yet, or the model map hasn't
  // loaded yet), fall back to comparing the raw token. That keeps the
  // matcher working when token and printer-name fragment happen to be
  // identical — e.g. "Q1" preset against "Bambu Lab Q1 0.4 nozzle" —
  // without us having to ship a code update. When they differ in form
  // (X1C vs "X1 Carbon"), the registry is what makes the match work.
  const inferredModel = bambuModelByShortCode[token] ?? token;
  const selectedModel = extractPrinterPresetModel(selectedPrinterName);
  if (!selectedModel) return 'unknown';
  return normalizeModelFragment(selectedModel) === normalizeModelFragment(inferredModel)
    ? 'match'
    : 'mismatch';
}

/**
 * Classify a process / filament preset against the selected printer.
 *
 * - 'match'    — the preset is compatible with the selected printer.
 * - 'mismatch' — the preset resolves to a *different* printer.
 * - 'unknown'  — compatibility can't be determined (no `compatible_printers`,
 *                no uploaded bundle, no recognizable `@BBL` tag, or no
 *                printer is selected); the caller must not hide it.
 */
export function presetCompatibility(
  preset: { name: string; compatible_printers?: string[] | null },
  slot: 'process' | 'filament',
  selectedPrinterName: string | null,
  index: PrinterCompatibilityIndex,
): PrinterCompatibility {
  if (!selectedPrinterName) return 'unknown';
  // (1) Imported presets carry the slicer's own compatible_printers list —
  // authoritative when set.
  const compat = preset.compatible_printers;
  if (compat && compat.length > 0) {
    return compat.includes(selectedPrinterName) ? 'match' : 'mismatch';
  }
  // (2) Consult the uploaded Slicer Bundles.
  const printers = index[slot].get(normalizePresetName(preset.name));
  if (printers && printers.size > 0) {
    return printers.has(selectedPrinterName) ? 'match' : 'mismatch';
  }
  // (3) BambuStudio's `@BBL <model>` name convention — covers cloud /
  // standard presets for users who haven't uploaded bundles for every
  // printer their cloud catalogue includes.
  return classifyByBambuName(preset.name, selectedPrinterName, index.bambuModelByShortCode);
}
