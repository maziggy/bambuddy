import { describe, it, expect } from 'vitest';
import {
  buildCompatibilityIndex,
  presetCompatibility,
  EMPTY_COMPATIBILITY_INDEX,
  type CompatibilityBundle,
} from '../../utils/slicerPrinterMatch';

const X1C = 'Bambu Lab X1 Carbon 0.4 nozzle';
const P2S = 'Bambu Lab P2S 0.4 nozzle';

// Mirror of backend/app/utils/printer_models.py PRINTER_MODEL_MAP, fetched
// from /slicer/printer-models at runtime (#1325 follow-up). Listed in tests
// to exercise the @BBL name fallback against the same registry the real
// app sees.
const PRINTER_MODELS: Record<string, string> = {
  'Bambu Lab X1 Carbon': 'X1C',
  'Bambu Lab X1': 'X1',
  'Bambu Lab X1E': 'X1E',
  'Bambu Lab P1S': 'P1S',
  'Bambu Lab P1P': 'P1P',
  'Bambu Lab P2S': 'P2S',
  'Bambu Lab A1': 'A1',
  'Bambu Lab A1 Mini': 'A1 Mini',
  'Bambu Lab A1 mini': 'A1 Mini',
  'Bambu Lab H2D': 'H2D',
  'Bambu Lab H2D Pro': 'H2D Pro',
  'Bambu Lab H2C': 'H2C',
  'Bambu Lab H2S': 'H2S',
  'Bambu Lab X2D': 'X2D',
};

// Two uploaded bundles, one per printer — the ground truth all matching
// is derived from. Note P2S: a model the old hard-coded list never knew
// about, now covered purely because its bundle was uploaded (#1325).
const BUNDLES: CompatibilityBundle[] = [
  {
    printer_preset_name: X1C,
    process: ['0.20mm Standard @BBL X1C', '0.20mm Strength @BBL X1C'],
    filament: ['Bambu PLA Basic @BBL X1C'],
  },
  {
    printer_preset_name: P2S,
    process: ['0.20mm Standard @BBL P2S', '0.16mm Standard @BBL P2S'],
    filament: ['Bambu PLA Basic @BBL P2S'],
  },
];

describe('buildCompatibilityIndex', () => {
  it('maps each preset name to the printers whose bundles ship it', () => {
    const index = buildCompatibilityIndex(BUNDLES, PRINTER_MODELS);
    expect([...(index.process.get('0.20mm Standard @BBL X1C') ?? [])]).toEqual([X1C]);
    expect([...(index.process.get('0.16mm Standard @BBL P2S') ?? [])]).toEqual([P2S]);
    expect([...(index.filament.get('Bambu PLA Basic @BBL P2S') ?? [])]).toEqual([P2S]);
  });

  it('unions printers when several bundles ship the same preset name', () => {
    const shared = '0.20mm Standard';
    const index = buildCompatibilityIndex(
      [
        { printer_preset_name: X1C, process: [shared], filament: [] },
        { printer_preset_name: P2S, process: [shared], filament: [] },
      ],
      PRINTER_MODELS,
    );
    expect(index.process.get(shared)).toEqual(new Set([X1C, P2S]));
  });

  it("strips BambuStudio's '# ' user-clone prefix so names compare equal", () => {
    const index = buildCompatibilityIndex(
      [{ printer_preset_name: X1C, process: ['# 0.20mm Custom'], filament: [] }],
      PRINTER_MODELS,
    );
    expect(index.process.has('0.20mm Custom')).toBe(true);
  });

  it('skips bundles with no printer name', () => {
    const index = buildCompatibilityIndex(
      [{ printer_preset_name: '', process: ['Orphan Process'], filament: [] }],
      PRINTER_MODELS,
    );
    expect(index.process.size).toBe(0);
  });

  it('inverts the printer-model registry into short-code → display fragment', () => {
    const index = buildCompatibilityIndex([], PRINTER_MODELS);
    expect(index.bambuModelByShortCode.X1C).toBe('X1 Carbon');
    expect(index.bambuModelByShortCode.P2S).toBe('P2S');
    expect(index.bambuModelByShortCode['A1 Mini']).toBe('A1 Mini');
    expect(index.bambuModelByShortCode['H2D Pro']).toBe('H2D Pro');
  });

  it('tolerates an empty printer-model registry (model fetch hasn\'t resolved yet)', () => {
    const index = buildCompatibilityIndex(BUNDLES);
    expect(index.bambuModelByShortCode).toEqual({});
    // Bundle matching still works on its own.
    expect([...(index.process.get('0.20mm Standard @BBL X1C') ?? [])]).toEqual([X1C]);
  });
});

describe('presetCompatibility', () => {
  const index = buildCompatibilityIndex(BUNDLES, PRINTER_MODELS);
  // Bundle-free index used by the #1325 follow-up fallback tests: any match
  // here must come from the @BBL name parse alone.
  const namesOnlyIndex = buildCompatibilityIndex([], PRINTER_MODELS);

  it('uses compatible_printers exactly when present (imported / local tier)', () => {
    const preset = { name: 'My Process', compatible_printers: [X1C] };
    expect(presetCompatibility(preset, 'process', X1C, EMPTY_COMPATIBILITY_INDEX)).toBe('match');
    expect(presetCompatibility(preset, 'process', P2S, EMPTY_COMPATIBILITY_INDEX)).toBe('mismatch');
  });

  it('is unknown when compatible_printers is set but no printer is selected', () => {
    expect(
      presetCompatibility({ name: 'P', compatible_printers: [X1C] }, 'process', null, index),
    ).toBe('unknown');
  });

  it('matches a preset shipped by the selected printer\'s bundle', () => {
    expect(presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', X1C, index)).toBe(
      'match',
    );
    expect(
      presetCompatibility({ name: 'Bambu PLA Basic @BBL P2S' }, 'filament', P2S, index),
    ).toBe('match');
  });

  it('flags a preset whose bundle is for a different printer (the #1325 bug)', () => {
    // X1C selected, but this process only ships in the P2S bundle.
    expect(presetCompatibility({ name: '0.16mm Standard @BBL P2S' }, 'process', X1C, index)).toBe(
      'mismatch',
    );
  });

  it('falls back to @BBL name parsing when no bundle covers the preset (#1325 follow-up)', () => {
    // No A1 bundle uploaded, but the preset's @BBL A1 tag is enough to
    // resolve it: A1 ≠ X1C so it belongs in "Other printers".
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL A1' }, 'process', X1C, index),
    ).toBe('mismatch');
  });

  it('falls back to @BBL name parsing when no bundles are imported at all', () => {
    // Brand-new user, zero bundles, every preset would have been "unknown"
    // under the bundle-only design — now resolves via the name suffix.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C' },
        'process',
        X1C,
        namesOnlyIndex,
      ),
    ).toBe('match');
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S' },
        'process',
        X1C,
        namesOnlyIndex,
      ),
    ).toBe('mismatch');
  });

  it('is unknown when no printer is selected', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', null, index),
    ).toBe('unknown');
  });

  it("matches across the '# ' user-clone prefix", () => {
    const index2 = buildCompatibilityIndex(
      [{ printer_preset_name: X1C, process: ['# 0.20mm Custom'], filament: [] }],
      PRINTER_MODELS,
    );
    expect(presetCompatibility({ name: '0.20mm Custom' }, 'process', X1C, index2)).toBe('match');
  });

  it('compatible_printers wins over @BBL even when the name suggests a different printer', () => {
    // Authoritative slicer declaration: this @BBL P2S preset has been
    // manually reassigned to X1C. The compatible_printers list must win.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S', compatible_printers: [X1C] },
        'process',
        X1C,
        namesOnlyIndex,
      ),
    ).toBe('match');
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S', compatible_printers: [X1C] },
        'process',
        P2S,
        namesOnlyIndex,
      ),
    ).toBe('mismatch');
  });

  it('bundle index wins over @BBL when they disagree', () => {
    // Hypothetical bundle that ships a P2S-tagged preset as compatible
    // with the X1C printer too — bundle-as-ground-truth overrules the
    // name-suffix inference.
    const reassigned = buildCompatibilityIndex(
      [{ printer_preset_name: X1C, process: ['0.20mm Standard @BBL P2S'], filament: [] }],
      PRINTER_MODELS,
    );
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL P2S' }, 'process', X1C, reassigned),
    ).toBe('match');
  });
});

// ─── #1325 follow-up: @BBL name fallback ──────────────────────────────────

describe('presetCompatibility — @BBL name fallback (no bundles)', () => {
  // No bundles, but with the registry loaded — exactly the new-user shape.
  const idx = buildCompatibilityIndex([], PRINTER_MODELS);

  // Bambu's short codes vs the long forms in printer-preset names: the
  // entire reason the fallback needs a registry to consult.
  it.each<[string, string, 'match' | 'mismatch']>([
    // @BBL X1C → "X1 Carbon" (the case the old hardcoded list got right)
    ['0.20mm Standard @BBL X1C', X1C, 'match'],
    ['0.20mm Standard @BBL X1C', 'Bambu Lab P1S 0.4 nozzle', 'mismatch'],
    // @BBL X1 must NOT match X1 Carbon (X1 and X1C are physically different printers)
    ['0.20mm Standard @BBL X1', 'Bambu Lab X1 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL X1', X1C, 'mismatch'],
    // @BBL A1 must NOT match A1 mini (case the original hardcoded list got wrong)
    ['0.20mm Standard @BBL A1', 'Bambu Lab A1 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL A1', 'Bambu Lab A1 mini 0.4 nozzle', 'mismatch'],
    // @BBL "A1 Mini" — multi-word token
    ['0.20mm Standard @BBL A1 Mini', 'Bambu Lab A1 mini 0.4 nozzle', 'match'],
    // @BBL H2D vs H2D Pro disambiguation
    ['0.20mm Standard @BBL H2D', 'Bambu Lab H2D 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL H2D', 'Bambu Lab H2D Pro 0.4 nozzle', 'mismatch'],
    ['0.20mm Standard @BBL H2D Pro', 'Bambu Lab H2D Pro 0.4 nozzle', 'match'],
    // Models missing from the original hardcoded list (the #1325 bug),
    // now resolved via the backend registry.
    ['Bambu PLA Basic @BBL P2S', P2S, 'match'],
    ['Bambu PLA Basic @BBL P2S', X1C, 'mismatch'],
    ['0.20mm Standard @BBL X2D', 'Bambu Lab X2D 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL H2C', 'Bambu Lab H2C 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL H2S', 'Bambu Lab H2S 0.4 nozzle', 'match'],
  ])('classifies %s against %s as %s', (presetName, printerName, expected) => {
    expect(presetCompatibility({ name: presetName }, 'process', printerName, idx)).toBe(expected);
  });

  it('handles a trailing nozzle-size suffix on the @BBL tag', () => {
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C 0.4 nozzle' },
        'process',
        X1C,
        idx,
      ),
    ).toBe('match');
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C 0.6 nozzle' },
        'process',
        X1C,
        idx,
      ),
    ).toBe('match');
  });

  it('is unknown when the preset has no @BBL tag at all (custom name, no other signal)', () => {
    expect(presetCompatibility({ name: 'My Custom Process' }, 'process', X1C, idx)).toBe('unknown');
  });

  it('is unknown for a Bambu preset against a non-Bambu printer (can\'t parse the printer name)', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', 'CustomBuild 0.4', idx),
    ).toBe('unknown');
  });

  it('falls back to raw-token comparison for a model not yet in the registry', () => {
    // A future "Q1" printer with cloud presets named "@BBL Q1" should
    // match without any code change to the registry — both names resolve
    // to "Q1" directly.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL Q1' },
        'process',
        'Bambu Lab Q1 0.4 nozzle',
        idx,
      ),
    ).toBe('match');
    // And mismatch against a different printer.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL Q1' },
        'process',
        X1C,
        idx,
      ),
    ).toBe('mismatch');
  });

  it('still resolves @BBL when the registry has not loaded yet (raw-token only)', () => {
    // EMPTY_COMPATIBILITY_INDEX = no bundles, no models — first paint of
    // the SliceModal before the /slicer/printer-models fetch resolves.
    // Short codes that match their printer-name fragment directly (P2S,
    // H2D, etc.) still work; codes that differ in form (X1C vs "X1
    // Carbon") gracefully fall through to 'unknown'.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S' },
        'process',
        P2S,
        EMPTY_COMPATIBILITY_INDEX,
      ),
    ).toBe('match');
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C' },
        'process',
        X1C,
        EMPTY_COMPATIBILITY_INDEX,
      ),
    ).toBe('mismatch'); // X1C ≠ "X1 Carbon" without the registry
  });
});
