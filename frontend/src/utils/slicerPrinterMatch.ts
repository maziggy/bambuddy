// Printer-compatibility matching for the SliceModal's process / filament
// dropdowns (#1325).
//
// Compatibility is resolved in this order, stopping on the first non-unknown
// answer:
//
//   1. Imported (local-tier) presets carry the slicer's own
//      `compatible_printers` list — an exact list of printer-preset names.
//   2. The `@<printer>` naming convention, in both shapes the slicer
//      writes: `@BBL <model>` on shipped cloud / standard presets, and
//      `@Bambu Lab <model> <size> nozzle` on presets a user saved for a
//      specific printer (#2628). The token → printer-fragment table is
//      derived from the backend's canonical PRINTER_MODEL_MAP (fetched via
//      /slicer/printer-models), not duplicated here.
//
// The result drives grouping, not hard hiding: a preset no rule covers
// stays in the main list, and only a preset that resolves to a *different*
// printer is pushed into an "Other printers" group.

export type PrinterCompatibility = 'match' | 'mismatch' | 'unknown';

// Lookup tables consumed by `presetCompatibility`. `bambuModelByShortCode`
// is the @BBL token → printer-preset fragment map derived from the backend's
// PRINTER_MODEL_MAP — e.g. `X1C` → `X1 Carbon`. An empty map means the @BBL
// fallback still works when token and printer-name fragment match directly
// (raw-token comparison), and gracefully degrades otherwise.
export interface PrinterCompatibilityIndex {
  bambuModelByShortCode: Record<string, string>;
}

/** An empty index — used when the model map hasn't loaded yet. */
export const EMPTY_COMPATIBILITY_INDEX: PrinterCompatibilityIndex = {
  bambuModelByShortCode: {},
};

// Bambu cloud started shipping terse model codes in `@BBL <code>` suffixes
// mid-2026 — the most visible one is "A1 Mini" → "A1M" (#1649, reported by
// @technopaw). User-authored profiles still use the long display name, so
// both shapes have to match the same printer. The table is uppercase-normalised
// for case-insensitive lookups; add a row when a future rename is spotted via
// `/api/v1/cloud/settings`. Keep narrow on purpose — wide-net aliasing
// (e.g. "X1" ⇄ "X1C") would silently group truly distinct printers.
const PRINTER_MODEL_SUFFIX_ALIASES: Record<string, readonly string[]> = {
  'A1 MINI': ['A1M'],
};

/**
 * True when ``presetSuffix`` (the token extracted from a "@BBL <code>" or
 * preset-name suffix) refers to the same printer as ``printerModel``
 * (the display name selected in the picker). Case-insensitive; consults
 * the alias table for short codes Bambu introduced after the long forms
 * shipped (#1649).
 */
export function matchesPrinterModelSuffix(presetSuffix: string, printerModel: string): boolean {
  const p = presetSuffix.toUpperCase();
  const m = printerModel.toUpperCase();
  if (p === m) return true;
  const aliasesOfM = PRINTER_MODEL_SUFFIX_ALIASES[m];
  if (aliasesOfM && aliasesOfM.includes(p)) return true;
  const aliasesOfP = PRINTER_MODEL_SUFFIX_ALIASES[p];
  if (aliasesOfP && aliasesOfP.includes(m)) return true;
  return false;
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
 * Build the compatibility index from the backend printer-model registry.
 */
export function buildCompatibilityIndex(
  printerModels: Record<string, string> = {},
): PrinterCompatibilityIndex {
  return {
    bambuModelByShortCode: buildShortCodeMap(printerModels),
  };
}

function normalizeModelFragment(s: string): string {
  return s.replace(/\s+/g, '').toLowerCase();
}

// Bambu Studio's naming convention for bundled presets: the 0.4 nozzle is
// the default and its variants drop the nozzle suffix; 0.2 / 0.6 / 0.8
// carry an explicit "<size> nozzle" segment. So a process with no suffix
// is implicitly a 0.4 process — required to compare correctly against a
// 0.4 printer preset, which DOES carry the suffix.
const DEFAULT_NOZZLE = '0.4';

// Strip a trailing "<size> nozzle" segment, returning the nozzle string
// (e.g. "0.6") or null when absent. Used by both BBL-token and printer-
// preset extractors so the suffix is parsed identically on both sides.
function takeNozzleSuffix(s: string): { stripped: string; nozzle: string | null } {
  const m = s.match(/^(.*?)\s+([\d.]+)\s*nozzle\s*$/i);
  if (!m) return { stripped: s.trim(), nozzle: null };
  return { stripped: m[1].trim(), nozzle: m[2] };
}

// Pull the model token and nozzle out of a "@BBL <token> [<size> nozzle]"
// suffix. The token may contain a space (e.g. "A1 mini"), so we strip a
// trailing nozzle segment rather than splitting on the first whitespace.
function extractBblToken(presetName: string): { token: string; nozzle: string | null } | null {
  const marker = '@BBL ';
  const idx = presetName.indexOf(marker);
  if (idx < 0) return null;
  const rest = presetName.slice(idx + marker.length).trim();
  const { stripped, nozzle } = takeNozzleSuffix(rest);
  return stripped ? { token: stripped, nozzle } : null;
}

// Pull the model fragment and nozzle out of a "Bambu Lab <model> [<size>
// nozzle]" printer preset name. Returns null for non-Bambu printer
// presets — there is no reliable name-based match against those.
function extractPrinterPresetModel(printerPresetName: string): { model: string; nozzle: string | null } | null {
  const m = printerPresetName.match(/^Bambu Lab\s+(.+)$/i);
  if (!m) return null;
  const { stripped, nozzle } = takeNozzleSuffix(m[1]);
  return stripped ? { model: stripped, nozzle } : null;
}

// Trailing parenthetical the slicer appends to user-saved presets —
// "… @Bambu Lab H2D 0.4 nozzle (Custom)". Dropped before the nozzle suffix
// is parsed, or the tag would resolve to a nonsense model token and the
// preset would be branded a mismatch against its OWN printer.
function stripTrailingParenthetical(s: string): string {
  return s.replace(/\s*\([^)]*\)\s*$/, '').trim();
}

// Nozzle sizes Bambu ships run 0.2 – 0.8. The range guard keeps a tag that
// merely looks numeric ("PLA @2026") from being read as a nozzle and branded
// incompatible with every printer.
const MIN_NOZZLE_MM = 0.1;
const MAX_NOZZLE_MM = 2.0;

// Compare two nozzle strings numerically, so "0.20" and "0.2" are the same
// size. Unparseable values never match — a size we can't read is not evidence.
function sameNozzle(a: string, b: string): boolean {
  const x = Number.parseFloat(a);
  const y = Number.parseFloat(b);
  if (Number.isNaN(x) || Number.isNaN(y)) return false;
  return x === y;
}

// Pull the model token and nozzle out of a preset name's printer tag.
// Three shapes exist in the wild (#2628):
//
//   "0.20mm Standard @BBL X1C"                    — short code, the form
//      Bambu ships its own cloud / standard presets under.
//   "SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle"     — the full printer-preset
//      name, the form the slicer writes when a user saves their own preset
//      for a printer. Handling only the short form left these classified
//      'unknown', so an H2D-scoped filament was offered (and auto-picked)
//      for an A1 slice, which the CLI then rejected.
//   "Overture PLA Matte @0.2"                     — nozzle only, no model.
//      Returned with a null token: the size can rule a printer OUT, but
//      says nothing about which models the profile belongs to.
//
// The first two shapes are also parsed in ConfigureAmsSlotModal (#1623).
function extractPrinterTag(presetName: string): { token: string | null; nozzle: string | null } | null {
  const cleaned = stripTrailingParenthetical(presetName);
  const bbl = extractBblToken(cleaned);
  if (bbl) return bbl;
  // The printer tag is a suffix by convention, so read from the LAST '@' —
  // a stray earlier one ("My @work PLA @Bambu Lab H2D 0.4 nozzle") must not
  // swallow it. Anything that doesn't parse as a Bambu printer preset name
  // falls through to 'unknown', never to a guessed mismatch.
  const at = cleaned.lastIndexOf('@');
  if (at < 0) return null;
  const suffix = cleaned.slice(at + 1).trim();
  const longForm = extractPrinterPresetModel(suffix);
  if (longForm) return { token: longForm.model, nozzle: longForm.nozzle };
  const nozzleOnly = suffix.match(/^([\d.]+)\s*(?:mm)?\s*(?:nozzle)?$/i);
  if (nozzleOnly) {
    const size = Number.parseFloat(nozzleOnly[1]);
    if (!Number.isNaN(size) && size >= MIN_NOZZLE_MM && size <= MAX_NOZZLE_MM) {
      return { token: null, nozzle: nozzleOnly[1] };
    }
  }
  return null;
}

/**
 * Name-based fallback for presets carrying a printer tag — BambuStudio's own
 * `@BBL <model>` (#1325 follow-up), the full `@Bambu Lab <model> <size>
 * nozzle` form user-saved presets get, or a bare `@<size>` (#2628).
 * Used only after `compatible_printers` has returned `'unknown'`.
 *
 * Compares BOTH model AND nozzle. The nozzle filter is required because
 * Bambu ships per-nozzle process / filament variants (0.2 / 0.4 / 0.6 /
 * 0.8) — a 0.6-nozzle process is unusable on a 0.4-nozzle printer.
 * 0.4 is Bambu's default and its variants drop the nozzle suffix, so a
 * preset with no suffix counts as 0.4.
 */
function classifyByBambuName(
  presetName: string,
  selectedPrinterName: string,
  bambuModelByShortCode: Record<string, string>,
): PrinterCompatibility {
  const parsed = extractPrinterTag(presetName);
  if (!parsed) return 'unknown';
  const selectedParts = extractPrinterPresetModel(selectedPrinterName);
  if (!selectedParts) return 'unknown';
  if (parsed.token === null) {
    // Nozzle-only tag ("Overture PLA Matte @0.2"). The size can rule a
    // printer OUT, but a matching size proves nothing about the model, so
    // the best this can ever return is 'unknown' — never 'match'.
    if (
      selectedParts.nozzle !== null
      && parsed.nozzle !== null
      && !sameNozzle(parsed.nozzle, selectedParts.nozzle)
    ) {
      return 'mismatch';
    }
    return 'unknown';
  }
  // If the token isn't in the table (a brand-new Bambu model whose short
  // code the backend registry hasn't added yet, or the model map hasn't
  // loaded yet), fall back to comparing the raw token. That keeps the
  // matcher working when token and printer-name fragment happen to be
  // identical — e.g. "Q1" preset against "Bambu Lab Q1 0.4 nozzle" —
  // without us having to ship a code update. When they differ in form
  // (X1C vs "X1 Carbon"), the registry is what makes the match work.
  const inferredModel = bambuModelByShortCode[parsed.token] ?? parsed.token;
  // The raw inferred model and the printer-preset fragment may differ only by
  // the Bambu short-code rename (e.g. preset token "A1M" vs printer "A1 Mini").
  // ``matchesPrinterModelSuffix`` consults the alias table before declaring a
  // mismatch — see #1649.
  if (
    normalizeModelFragment(selectedParts.model) !== normalizeModelFragment(inferredModel)
    && !matchesPrinterModelSuffix(parsed.token, selectedParts.model)
  ) {
    return 'mismatch';
  }
  // Nozzle compare — only when we have a usable size from the printer
  // side. A Bambu printer preset always carries one, so this branch is
  // taken in practice; the null path is defensive degrade for hand-typed
  // or non-Bambu printer names that happened to match the model.
  if (selectedParts.nozzle !== null) {
    const presetNozzle = parsed.nozzle ?? DEFAULT_NOZZLE;
    if (!sameNozzle(presetNozzle, selectedParts.nozzle)) return 'mismatch';
  }
  return 'match';
}

/**
 * Classify a process / filament preset against the selected printer.
 *
 * - 'match'    — the preset is compatible with the selected printer.
 * - 'mismatch' — the preset resolves to a *different* printer.
 * - 'unknown'  — compatibility can't be determined (no `compatible_printers`,
 *                no recognizable `@BBL` tag, or no printer is selected);
 *                the caller must not hide it.
 */
export function presetCompatibility(
  preset: { name: string; compatible_printers?: string[] | null },
  _slot: 'process' | 'filament',
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
  // (2) BambuStudio's `@BBL <model>` name convention — covers cloud /
  // standard presets that don't carry compatible_printers.
  return classifyByBambuName(preset.name, selectedPrinterName, index.bambuModelByShortCode);
}
