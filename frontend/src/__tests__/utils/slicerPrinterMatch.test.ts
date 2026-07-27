import { describe, it, expect } from 'vitest';
import {
  buildCompatibilityIndex,
  matchesPrinterModelSuffix,
  presetCompatibility,
  EMPTY_COMPATIBILITY_INDEX,
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

describe('buildCompatibilityIndex', () => {
  it('inverts the printer-model registry into short-code → display fragment', () => {
    const index = buildCompatibilityIndex(PRINTER_MODELS);
    expect(index.bambuModelByShortCode.X1C).toBe('X1 Carbon');
    expect(index.bambuModelByShortCode.P2S).toBe('P2S');
    expect(index.bambuModelByShortCode['A1 Mini']).toBe('A1 Mini');
    expect(index.bambuModelByShortCode['H2D Pro']).toBe('H2D Pro');
  });

  it('tolerates an empty printer-model registry (model fetch hasn\'t resolved yet)', () => {
    const index = buildCompatibilityIndex();
    expect(index.bambuModelByShortCode).toEqual({});
  });
});

describe('presetCompatibility', () => {
  const index = buildCompatibilityIndex(PRINTER_MODELS);

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

  it('is unknown when no printer is selected', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', null, index),
    ).toBe('unknown');
  });

  it('compatible_printers wins over @BBL even when the name suggests a different printer', () => {
    // Authoritative slicer declaration: this @BBL P2S preset has been
    // manually reassigned to X1C. The compatible_printers list must win.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S', compatible_printers: [X1C] },
        'process',
        X1C,
        index,
      ),
    ).toBe('match');
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL P2S', compatible_printers: [X1C] },
        'process',
        P2S,
        index,
      ),
    ).toBe('mismatch');
  });
});

// ─── #1325 follow-up: @BBL name fallback ──────────────────────────────────

describe('presetCompatibility — @BBL name fallback', () => {
  const idx = buildCompatibilityIndex(PRINTER_MODELS);

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
    // Models the original hardcoded list missed, now resolved via the
    // backend registry.
    ['Bambu PLA Basic @BBL P2S', P2S, 'match'],
    ['Bambu PLA Basic @BBL P2S', X1C, 'mismatch'],
    ['0.20mm Standard @BBL X2D', 'Bambu Lab X2D 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL H2C', 'Bambu Lab H2C 0.4 nozzle', 'match'],
    ['0.20mm Standard @BBL H2S', 'Bambu Lab H2S 0.4 nozzle', 'match'],
  ])('classifies %s against %s as %s', (presetName, printerName, expected) => {
    expect(presetCompatibility({ name: presetName }, 'process', printerName, idx)).toBe(expected);
  });

  it('handles a trailing nozzle-size suffix on the @BBL tag', () => {
    // An explicit "0.4 nozzle" suffix matches the 0.4 printer (Bambu's
    // convention is to omit it for 0.4, but some cloud presets write it).
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C 0.4 nozzle' },
        'process',
        X1C,
        idx,
      ),
    ).toBe('match');
    // #1325 follow-up #2 (IndividualGhost1905, 2026-05-23): a different
    // nozzle size IS a mismatch — a 0.6-nozzle process is unusable on a
    // 0.4-nozzle printer. The dedicated "nozzle filtering" describe
    // block below covers the full matrix; this case stays here as the
    // counterpart to the matching-suffix case above.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C 0.6 nozzle' },
        'process',
        X1C,
        idx,
      ),
    ).toBe('mismatch');
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
    // EMPTY_COMPATIBILITY_INDEX = no models — first paint of the SliceModal
    // before the /slicer/printer-models fetch resolves. Short codes that
    // match their printer-name fragment directly (P2S, H2D, etc.) still
    // work; codes that differ in form (X1C vs "X1 Carbon") gracefully
    // fall through to 'mismatch'.
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

// #1325 follow-up #2 (IndividualGhost1905, 2026-05-23): the @BBL name
// fallback must also filter by nozzle diameter. Bambu ships per-nozzle
// variants of process / filament presets — 0.2 / 0.4 / 0.6 / 0.8 —
// and a 0.6-nozzle process is unusable on a 0.4-nozzle printer. Bambu's
// naming convention: 0.4 is the default and DROPS the suffix; 0.2 / 0.6
// / 0.8 carry an explicit "<size> nozzle" segment. So an empty suffix
// means 0.4, not "any nozzle".
describe('presetCompatibility — nozzle filtering on @BBL name fallback', () => {
  const X1C_04 = 'Bambu Lab X1 Carbon 0.4 nozzle';
  const X1C_06 = 'Bambu Lab X1 Carbon 0.6 nozzle';
  const X1C_08 = 'Bambu Lab X1 Carbon 0.8 nozzle';
  const index = buildCompatibilityIndex(PRINTER_MODELS);

  it('treats a no-suffix process as 0.4 (Bambu default) and matches a 0.4 printer', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', X1C_04, index),
    ).toBe('match');
  });

  it('flags a 0.6-nozzle process as mismatch against a 0.4 printer', () => {
    expect(
      presetCompatibility({ name: '0.30mm @BBL X1C 0.6 nozzle' }, 'process', X1C_04, index),
    ).toBe('mismatch');
  });

  it('flags an 0.8-nozzle process as mismatch against a 0.4 printer', () => {
    expect(
      presetCompatibility({ name: '0.40mm Strength @BBL X1C 0.8 nozzle' }, 'process', X1C_04, index),
    ).toBe('mismatch');
  });

  it('matches a 0.6-nozzle process against a 0.6 printer', () => {
    expect(
      presetCompatibility({ name: '0.30mm @BBL X1C 0.6 nozzle' }, 'process', X1C_06, index),
    ).toBe('match');
  });

  it('flags a no-suffix process (=0.4) as mismatch against a 0.6 printer', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', X1C_06, index),
    ).toBe('mismatch');
  });

  it('applies the same rule to filament presets', () => {
    // Bambu's bundled filament presets follow the same per-nozzle naming.
    expect(
      presetCompatibility(
        { name: 'Bambu PLA Basic @BBL X1C 0.6 nozzle' },
        'filament',
        X1C_04,
        index,
      ),
    ).toBe('mismatch');
    expect(
      presetCompatibility({ name: 'Bambu PLA Basic @BBL X1C' }, 'filament', X1C_04, index),
    ).toBe('match');
  });

  it('keeps a 0.4 process matching a 0.4 printer when the preset DOES carry an explicit "0.4 nozzle" suffix', () => {
    // Some cloud presets write the 0.4 suffix explicitly even though
    // Bambu's bundled convention omits it. Both forms must compare equal.
    expect(
      presetCompatibility(
        { name: '0.20mm Standard @BBL X1C 0.4 nozzle' },
        'process',
        X1C_04,
        index,
      ),
    ).toBe('match');
  });

  it('still flags a wrong-MODEL process even when the nozzle matches', () => {
    // The model filter must continue to dominate over the nozzle filter:
    // a 0.4 A1 process isn't usable on an X1C 0.4 just because both are 0.4.
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL A1' }, 'process', X1C_04, index),
    ).toBe('mismatch');
  });

  it('falls back to model-only when the selected printer name has no parseable nozzle', () => {
    // Defensive degrade for non-Bambu / hand-typed names that happen to
    // match the model. Real Bambu printer presets always carry a nozzle,
    // so this path is rare; the assertion pins the intentional behaviour.
    const noNozzle = 'Bambu Lab X1 Carbon'; // no "0.4 nozzle" suffix
    expect(
      presetCompatibility({ name: '0.30mm @BBL X1C 0.6 nozzle' }, 'process', noNozzle, index),
    ).toBe('match');
  });

  it('flags 0.4 process on 0.8 printer (sanity check across the third common size)', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL X1C' }, 'process', X1C_08, index),
    ).toBe('mismatch');
  });
});

describe('matchesPrinterModelSuffix (#1649)', () => {
  it('matches the canonical short code against itself', () => {
    expect(matchesPrinterModelSuffix('X1C', 'X1C')).toBe(true);
  });

  it('is case-insensitive on both sides', () => {
    expect(matchesPrinterModelSuffix('x1c', 'X1C')).toBe(true);
    expect(matchesPrinterModelSuffix('a1 mini', 'A1 Mini')).toBe(true);
  });

  it('matches Bambu cloud rename A1M against the long form A1 Mini', () => {
    expect(matchesPrinterModelSuffix('A1M', 'A1 Mini')).toBe(true);
  });

  it('matches the long form A1 Mini against the short Bambu cloud code A1M', () => {
    expect(matchesPrinterModelSuffix('A1 Mini', 'A1M')).toBe(true);
  });

  it('does NOT match A1M against A1 (different printer, must not collapse)', () => {
    expect(matchesPrinterModelSuffix('A1M', 'A1')).toBe(false);
  });

  it('does NOT match A1 against A1 Mini', () => {
    expect(matchesPrinterModelSuffix('A1', 'A1 Mini')).toBe(false);
  });

  it('does NOT match unrelated models', () => {
    expect(matchesPrinterModelSuffix('X1C', 'P1S')).toBe(false);
  });
});

describe('presetCompatibility with Bambu cloud A1M rename (#1649)', () => {
  const A1_MINI = 'Bambu Lab A1 mini 0.4 nozzle';
  const A1 = 'Bambu Lab A1 0.4 nozzle';
  const idx = buildCompatibilityIndex(PRINTER_MODELS);

  it('matches a cloud preset using the new @BBL A1M suffix against an A1 Mini printer', () => {
    // The slicer-mirrored case: technopaw's report — A1 Mini cloud presets
    // newly ship as "Bambu PLA Basic @BBL A1M ..." and used to be filtered
    // out by the model check that compared "A1M" vs "A1 mini" verbatim.
    expect(
      presetCompatibility({ name: 'Bambu PLA Basic @BBL A1M' }, 'filament', A1_MINI, idx),
    ).toBe('match');
  });

  it('matches a 0.4-nozzle process with @BBL A1M against an A1 Mini printer', () => {
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL A1M' }, 'process', A1_MINI, idx),
    ).toBe('match');
  });

  it('does NOT match @BBL A1M against an A1 (non-mini) printer', () => {
    // The alias must not collapse two physically different printers — A1
    // and A1 Mini ship distinct profile sets.
    expect(
      presetCompatibility({ name: '0.20mm Standard @BBL A1M' }, 'process', A1, idx),
    ).toBe('mismatch');
  });
});

describe('presetCompatibility — long-form @Bambu Lab printer tag (#2628)', () => {
  const A1 = 'Bambu Lab A1 0.4 nozzle';
  const A1_MINI = 'Bambu Lab A1 mini 0.4 nozzle';
  const H2D = 'Bambu Lab H2D 0.4 nozzle';
  const idx = buildCompatibilityIndex(PRINTER_MODELS);

  it('flags a user-saved H2D filament preset as a mismatch on an A1', () => {
    // michaelklos's report: this exact preset sat in an unused filament slot
    // of a multi-plate 3MF. Classified 'unknown' it was treated as usable,
    // auto-picked, and the CLI rejected the slice with "filament preset
    // (slot 1) is not compatible with printer Bambu Lab A1 0.4 nozzle".
    expect(
      presetCompatibility({ name: 'SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle' }, 'filament', A1, idx),
    ).toBe('mismatch');
  });

  it('matches the same preset against its own printer', () => {
    expect(
      presetCompatibility({ name: 'SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle' }, 'filament', H2D, idx),
    ).toBe('match');
  });

  it('resolves a long-form model whose display name differs from its short code', () => {
    const name = 'My PETG @Bambu Lab X1 Carbon 0.4 nozzle';
    expect(presetCompatibility({ name }, 'filament', X1C, idx)).toBe('match');
    expect(presetCompatibility({ name }, 'filament', A1, idx)).toBe('mismatch');
  });

  it('applies the nozzle filter to the long form too', () => {
    expect(
      presetCompatibility({ name: 'My PETG @Bambu Lab A1 0.6 nozzle' }, 'filament', A1, idx),
    ).toBe('mismatch');
  });

  it('ignores a trailing "(Custom)" suffix rather than mangling the model token', () => {
    // The slicer appends this to user-saved presets. Parsed naively the tag
    // becomes "H2D 0.4 nozzle (Custom)" — which would brand the preset a
    // mismatch against its own printer and hide it from the dropdown.
    const name = 'Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)';
    expect(presetCompatibility({ name }, 'filament', H2D, idx)).toBe('match');
    expect(presetCompatibility({ name }, 'filament', A1, idx)).toBe('mismatch');
  });

  it('keeps the A1M alias working through the long form', () => {
    expect(
      presetCompatibility({ name: 'My PLA @Bambu Lab A1 mini 0.4 nozzle' }, 'filament', A1_MINI, idx),
    ).toBe('match');
    expect(
      presetCompatibility({ name: 'My PLA @Bambu Lab A1 mini 0.4 nozzle' }, 'filament', A1, idx),
    ).toBe('mismatch');
  });

  it('stays unknown for an @-tag that names no recognisable printer', () => {
    // "@Voron 0.4 nozzle" is not a Bambu printer preset — the matcher must
    // not guess, so the preset keeps its place in the main dropdown list.
    expect(
      presetCompatibility({ name: 'My PLA @Voron 0.4 nozzle' }, 'filament', A1, idx),
    ).toBe('unknown');
  });

  it('reads the tag from the last @ so a stray earlier one cannot swallow it', () => {
    expect(
      presetCompatibility({ name: 'My @work PLA @Bambu Lab H2D 0.4 nozzle' }, 'filament', A1, idx),
    ).toBe('mismatch');
    expect(
      presetCompatibility({ name: 'My @work PLA @Bambu Lab H2D 0.4 nozzle' }, 'filament', H2D, idx),
    ).toBe('match');
  });
});

describe('presetCompatibility — nozzle-only @<size> tag (#2628 follow-up)', () => {
  const P2S_04 = 'Bambu Lab P2S 0.4 nozzle';
  const X1C_02 = 'Bambu Lab X1 Carbon 0.2 nozzle';
  const idx = buildCompatibilityIndex(PRINTER_MODELS);

  it('rules out a 0.2-nozzle profile on a 0.4-nozzle printer', () => {
    // The live case: "Overture PLA Matte @0.2" inherits from the X1C 0.2
    // nozzle system profile, so the slicer rejected a P2S 0.4 slice with
    // "not compatible with printer Bambu Lab P2S 0.4 nozzle". The name
    // carries no model, so this size is the only signal available.
    expect(
      presetCompatibility({ name: 'Overture PLA Matte @0.2' }, 'filament', P2S_04, idx),
    ).toBe('mismatch');
  });

  it('stays unknown when the size agrees — a size is not a model', () => {
    // The profile might belong to a different printer with the same nozzle;
    // promoting this to 'match' would claim knowledge we don't have.
    expect(
      presetCompatibility({ name: 'Overture PLA Matte @0.2' }, 'filament', X1C_02, idx),
    ).toBe('unknown');
  });

  it('accepts the "0.2 nozzle" and "0.2mm" spellings of the same tag', () => {
    for (const name of ['My PLA @0.2 nozzle', 'My PLA @0.2mm']) {
      expect(presetCompatibility({ name }, 'filament', P2S_04, idx)).toBe('mismatch');
    }
  });

  it('compares sizes numerically so 0.20 and 0.2 are one size', () => {
    expect(
      presetCompatibility({ name: 'My PLA @0.20' }, 'filament', X1C_02, idx),
    ).toBe('unknown');
  });

  it('ignores a numeric tag that cannot be a nozzle', () => {
    // "@2026" is a year, not a 2026 mm nozzle — guessing here would brand
    // the profile incompatible with every printer that exists.
    expect(presetCompatibility({ name: 'My PLA @2026' }, 'filament', P2S_04, idx)).toBe('unknown');
    expect(presetCompatibility({ name: 'My PLA @0.05' }, 'filament', P2S_04, idx)).toBe('unknown');
  });

  it('leaves a model-bearing tag on the model path', () => {
    // Regression guard: the nozzle-only branch must not swallow the forms
    // that carry a model — those still resolve to match/mismatch.
    expect(
      presetCompatibility({ name: 'Bambu PLA Basic @BBL P2S' }, 'filament', P2S_04, idx),
    ).toBe('match');
    expect(
      presetCompatibility({ name: 'My PLA @Bambu Lab X1 Carbon 0.4 nozzle' }, 'filament', P2S_04, idx),
    ).toBe('mismatch');
  });
});
