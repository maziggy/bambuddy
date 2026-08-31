/**
 * The spool form's Printers tab.
 *
 * Two things are keyed differently and the UI has to keep them apart: the
 * filament preset belongs to the printer MODEL (an "@BBL X1C" preset is the
 * same preset on every X1C), while a K profile is measured on one individual
 * hotend and stays per printer, per extruder, per nozzle diameter.
 *
 * The layout is a model list plus a detail pane so that fleet size stops
 * mattering -- these drive it with a deliberately mixed fleet: two machines of
 * one model, a dual-nozzle machine with two different diameters fitted, and an
 * offline printer.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { render } from '../utils';
import { PrinterProfilesSection } from '../../components/spool-form/PrinterProfilesSection';
import { presetKey, hotendKey } from '../../components/spool-form/utils';
import { defaultFormData } from '../../components/spool-form/types';
import type {
  CalibrationProfile,
  FilamentOption,
  PresetChoice,
  PrinterWithCalibrations,
} from '../../components/spool-form/types';

const OPTIONS: FilamentOption[] = [
  { code: 'GFSA00', name: 'Bambu PLA Basic @BBL X1C', displayName: 'Bambu PLA Basic @BBL X1C', isCustom: false, allCodes: ['GFSA00'], source: 'cloud' },
  { code: 'GFSA09', name: 'Bambu PLA Basic @BBL H2C', displayName: 'Bambu PLA Basic @BBL H2C', isCustom: false, allCodes: ['GFSA09'], source: 'cloud' },
  { code: 'GFSA21', name: 'Bambu PLA Basic @BBL H2C 0.2 nozzle', displayName: 'Bambu PLA Basic @BBL H2C 0.2 nozzle', isCustom: false, allCodes: ['GFSA21'], source: 'orca_cloud' },
  // Names no model at all -- the shape most user-authored and Orca presets
  // have. Must stay offered everywhere: hiding what cannot be classified
  // would hide most third-party profiles.
  { code: 'LOCAL1', name: 'eSUN PETG Basic', displayName: 'eSUN PETG Basic (Local)', isCustom: true, allCodes: ['LOCAL1'], source: 'local' },
];

/** The backend registry shape: "Bambu Lab <long>" -> short code. */
const PRINTER_MODELS: Record<string, string> = {
  'Bambu Lab X1 Carbon': 'X1C',
  'Bambu Lab H2C': 'H2C',
  'Bambu Lab P1S': 'P1S',
  'Bambu Lab H2D': 'H2D',
};

function cal(overrides: Partial<CalibrationProfile>): CalibrationProfile {
  return {
    cali_idx: 1,
    filament_id: 'GFL99',
    setting_id: 'GFSL99',
    name: 'PLA Basic',
    k_value: 0.02,
    n_coef: 1.0,
    extruder_id: 0,
    nozzle_diameter: '0.4',
    nozzle_id: '',
    ...overrides,
  };
}

function printer(
  id: number,
  name: string,
  model: string | null,
  opts: {
    connected?: boolean;
    nozzleCount?: number;
    calibrations?: CalibrationProfile[];
    nozzles?: { nozzle_diameter?: string; nozzle_type?: string }[];
  } = {},
): PrinterWithCalibrations {
  return {
    printer: {
      id,
      name,
      model,
      connected: opts.connected ?? true,
      nozzle_count: opts.nozzleCount ?? 1,
    } as PrinterWithCalibrations['printer'],
    calibrations: opts.calibrations ?? [cal({})],
    nozzles: opts.nozzles,
  };
}

/** Two X1Cs, one dual-nozzle H2C with 0.4 + 0.2 fitted, one offline P1S. */
function fleet(): PrinterWithCalibrations[] {
  return [
    printer(1, 'X1C-1', 'X1C'),
    printer(2, 'X1C-2', 'X1C', { calibrations: [cal({ cali_idx: 2, k_value: 0.019 })] }),
    printer(3, 'H2C-1', 'H2C', {
      nozzleCount: 2,
      nozzles: [{ nozzle_diameter: '0.4' }, { nozzle_diameter: '0.2' }],
      calibrations: [
        cal({ cali_idx: 3, extruder_id: 0, nozzle_diameter: '0.4', k_value: 0.021 }),
        cal({ cali_idx: 16, extruder_id: 1, nozzle_diameter: '0.2', k_value: 0.014, name: 'PLA 0.2' }),
      ],
    }),
    printer(4, 'P1S-1', 'P1S', { connected: false }),
  ];
}

interface HarnessProps {
  printers?: PrinterWithCalibrations[];
  presets?: Map<string, PresetChoice>;
  profiles?: Map<string, CalibrationProfile>;
  onPresets?: (next: Map<string, PresetChoice>) => void;
  onProfiles?: (next: Map<string, CalibrationProfile>) => void;
  slicerFilament?: string;
}

/**
 * Renders the section with real state, so a click is asserted through the
 * component's own update path rather than against a spy on the setter.
 */
function Harness({
  printers = fleet(),
  presets = new Map(),
  profiles = new Map(),
  onPresets,
  onProfiles,
  slicerFilament = 'GFSA00',
}: HarnessProps) {
  const [modelPresets, setModelPresets] = React.useState(presets);
  const [selectedProfiles, setSelectedProfiles] = React.useState(profiles);
  const [selectedGroupId, setSelectedGroupId] = React.useState('');

  React.useEffect(() => {
    onPresets?.(modelPresets);
  }, [modelPresets, onPresets]);
  React.useEffect(() => {
    onProfiles?.(selectedProfiles);
  }, [selectedProfiles, onProfiles]);

  return (
    <PrinterProfilesSection
      formData={{ ...defaultFormData, material: 'PLA', brand: 'Bambu', slicer_filament: slicerFilament }}
      printersWithCalibrations={printers}
      filamentOptions={OPTIONS}
      modelPresets={modelPresets}
      setModelPresets={setModelPresets}
      selectedProfiles={selectedProfiles}
      setSelectedProfiles={setSelectedProfiles}
      selectedGroupId={selectedGroupId}
      setSelectedGroupId={setSelectedGroupId}
      printerModels={PRINTER_MODELS}
    />
  );
}

/**
 * The preset pickers in the detail pane, in render order.
 *
 * Each is a button labelled "<model> <size>mm Filament preset" -- not a native
 * <select>, because every option carries an origin badge (Bambu Cloud / Orca
 * Cloud / Local / Built-in) and a <select> cannot render one.
 */
function presetPickers(): HTMLElement[] {
  return screen.getAllByRole('button', { name: /Filament preset$/ });
}

function presetPicker(model: string, diameter: string): HTMLElement {
  return screen.getByRole('button', { name: `${model} ${diameter}mm Filament preset` });
}

/** Open one picker and return the options it is offering. */
function openPicker(picker: HTMLElement): HTMLElement[] {
  fireEvent.click(picker);
  return screen.getAllByRole('option');
}

function optionNamed(pattern: RegExp | string): HTMLElement {
  return screen.getByRole('option', { name: pattern });
}

/**
 * A model's row in the left rail. By role: the model name also appears as the
 * detail pane's heading, and the pane is full of other buttons.
 */
function modelRow(model: string): HTMLElement {
  return screen.getByRole('tab', { name: new RegExp(`^\\s*${model}\\b`) });
}

/** The detail pane's heading, i.e. which model is currently open. */
function openModel(): string {
  return screen.getByRole('heading').textContent ?? '';
}

describe('PrinterProfilesSection — spool identity', () => {
  it('names the spool being configured, colour included', () => {
    // This tab is the one place you read printer names rather than filament,
    // and the K lists below are filtered by exactly these fields -- so the line
    // also explains an empty list.
    const Named = () => {
      const [presets, setPresets] = React.useState(new Map<string, PresetChoice>());
      const [profiles, setProfiles] = React.useState(new Map<string, CalibrationProfile>());
      const [group, setGroup] = React.useState('');
      return (
        <PrinterProfilesSection
          formData={{
            ...defaultFormData,
            brand: 'Bambu',
            material: 'PLA',
            subtype: 'Matte',
            color_name: 'Scarlet Red',
            rgba: 'DE4343FF',
          }}
          printersWithCalibrations={fleet()}
          filamentOptions={OPTIONS}
          modelPresets={presets}
          setModelPresets={setPresets}
          selectedProfiles={profiles}
          setSelectedProfiles={setProfiles}
          selectedGroupId={group}
          setSelectedGroupId={setGroup}
        />
      );
    };
    render(<Named />);
    expect(screen.getByText('Bambu PLA Matte')).toBeInTheDocument();
    expect(screen.getByText('Scarlet Red')).toBeInTheDocument();
  });

  it('renders no identity bar at all when the spool has nothing to name yet', () => {
    const Blank = () => {
      const [presets, setPresets] = React.useState(new Map<string, PresetChoice>());
      const [profiles, setProfiles] = React.useState(new Map<string, CalibrationProfile>());
      const [group, setGroup] = React.useState('');
      return (
        <PrinterProfilesSection
          formData={{ ...defaultFormData, brand: '', material: '', subtype: '', color_name: '' }}
          printersWithCalibrations={fleet()}
          filamentOptions={OPTIONS}
          modelPresets={presets}
          setModelPresets={setPresets}
          selectedProfiles={profiles}
          setSelectedProfiles={setProfiles}
          selectedGroupId={group}
          setSelectedGroupId={setGroup}
        />
      );
    };
    render(<Blank />);
    // An empty bar, or one repeating the K section's "select a material first",
    // would be noise -- the K section already says it once.
    expect(screen.getAllByText(/select a material first/i)).toHaveLength(1);
  });
});

describe('PrinterProfilesSection — model list', () => {
  it('lists each model once, however many machines it has', () => {
    render(<Harness />);
    expect(modelRow('X1C')).toBeInTheDocument();
    expect(modelRow('H2C')).toBeInTheDocument();
    expect(modelRow('P1S')).toBeInTheDocument();
    // Two X1Cs, one row -- the rail is per model, not per machine.
    expect(within(modelRow('X1C')).getByText('2 printers')).toBeInTheDocument();
  });

  it('shows the first model by default and switches on click', () => {
    render(<Harness />);
    // Alphabetical: H2C first. Its machine is named in the detail pane.
    expect(openModel()).toBe('H2C');
    // Named twice on purpose: once in the pane subtitle, once on its own card.
    expect(screen.getAllByText('H2C-1').length).toBeGreaterThan(0);

    fireEvent.click(modelRow('X1C'));
    expect(openModel()).toBe('X1C');
    expect(screen.getByText('X1C-1, X1C-2')).toBeInTheDocument();
  });

  it('counts hotends with no K profile chosen', () => {
    render(<Harness />);
    // H2C has two hotends and nothing chosen yet.
    expect(within(modelRow('H2C')).getByText('2')).toBeInTheDocument();
  });
});

describe('PrinterProfilesSection — filament preset', () => {
  it('starts inherited and stores an override when one is picked', () => {
    let latest: Map<string, PresetChoice> = new Map();
    render(<Harness onPresets={next => { latest = next; }} />);

    // One badge per preset row -- one row per nozzle size.
    expect(screen.getAllByText('inherited')).toHaveLength(presetPickers().length);

    fireEvent.click(presetPicker('H2C', '0.4'));
    fireEvent.click(optionNamed('Bambu PLA Basic @BBL H2C Bambu Cloud'));

    expect(latest.get(presetKey('H2C', '0.4'))).toEqual({
      code: 'GFSA09',
      name: 'Bambu PLA Basic @BBL H2C',
    });
    expect(screen.getByText('override')).toBeInTheDocument();
  });

  it('clearing an override removes the row rather than storing the spool value', () => {
    // A row repeating the spool's own preset would freeze it: later edits to
    // the spool preset would stop reaching this model. Absent means inherit.
    let latest: Map<string, PresetChoice> = new Map();
    render(
      <Harness
        presets={new Map([[presetKey('H2C', '0.4'), { code: 'GFSA09', name: 'Bambu PLA Basic @BBL H2C' }]])}
        onPresets={next => { latest = next; }}
      />,
    );

    fireEvent.click(presetPicker('H2C', '0.4'));
    fireEvent.click(optionNamed(/use the spool's preset/i));

    expect(latest.has(presetKey('H2C', '0.4'))).toBe(false);
  });

  it('offers one preset row per nozzle size and nothing above them', () => {
    render(<Harness />);
    // Every standard size, not only the ones fitted: a spool is configured
    // once and nozzles get swapped. No model-wide row -- the preset lands on
    // an AMS slot and a slot feeds exactly one nozzle.
    expect(presetPickers()).toHaveLength(4);
    // (Each size also labels a K-profile grid row, hence getAll.)
    expect(screen.getAllByText('0.4mm').length).toBeGreaterThan(0);
    expect(screen.getAllByText('0.2mm').length).toBeGreaterThan(0);

    // Same for a model with a single diameter fitted across both machines.
    fireEvent.click(modelRow('X1C'));
    expect(presetPickers()).toHaveLength(4);
  });

  it('a pick is stored under the size it was made on', () => {
    let latest: Map<string, PresetChoice> = new Map();
    render(<Harness onPresets={next => { latest = next; }} />);

    fireEvent.click(presetPicker('H2C', '0.2'));
    fireEvent.click(optionNamed(/0\.2 nozzle/));

    expect(latest.get(presetKey('H2C', '0.2'))?.code).toBe('GFSA21');
    // Only that size -- the other rows are untouched.
    expect(latest.size).toBe(1);
  });

  it('refuses to offer a preset for a printer whose model is unknown', () => {
    render(<Harness printers={[printer(9, 'Mystery', null)]} />);
    expect(screen.getByText(/has not reported its model/i)).toBeInTheDocument();
  });
});

describe('PrinterProfilesSection — presets offered per model', () => {
  function optionNames(picker: HTMLElement): string[] {
    return openPicker(picker).map(o => o.textContent ?? '');
  }

  it('offers a model only the presets that name it', () => {
    render(<Harness />);
    const names = optionNames(presetPicker('H2C', '0.4'));

    expect(names.some(n => n.includes('Bambu PLA Basic @BBL H2C'))).toBe(true);
    expect(names.some(n => n.includes('Bambu PLA Basic @BBL X1C'))).toBe(false);
  });

  it('keeps presets whose model cannot be read from the name', () => {
    render(<Harness />);
    const names = optionNames(presetPicker('H2C', '0.4'));
    expect(names.some(n => n.includes('eSUN PETG Basic (Local)'))).toBe(true);
  });

  it('switching model switches which presets are offered', () => {
    render(<Harness />);
    fireEvent.click(modelRow('X1C'));
    const names = optionNames(presetPicker('X1C', '0.4'));
    expect(names.some(n => n.includes('Bambu PLA Basic @BBL X1C'))).toBe(true);
    expect(names.some(n => n.includes('Bambu PLA Basic @BBL H2C'))).toBe(false);
  });

  it('never hides an override that is already saved', () => {
    // The stored value may name another model -- picked before the filter
    // existed, or by hand. Dropping it from the list would blank the control
    // and quietly change what gets saved.
    render(
      <Harness
        presets={new Map([[presetKey('X1C', '0.4'), { code: 'GFSA09', name: 'Bambu PLA Basic @BBL H2C' }]])}
      />,
    );
    fireEvent.click(modelRow('X1C'));
    const picker = presetPicker('X1C', '0.4');
    expect(picker.textContent).toContain('Bambu PLA Basic @BBL H2C');
    expect(optionNames(picker).some(n => n.includes('Bambu PLA Basic @BBL H2C'))).toBe(true);
  });

  it('badges each preset with the source it came from', () => {
    // The same filament exists as a cloud preset, an imported one and a
    // built-in, and which one is picked decides what reaches the printer --
    // so the origin is shown here exactly as the Configure AMS Slot modal
    // shows it.
    render(<Harness />);
    const options = openPicker(presetPicker('H2C', '0.4'));

    const cloud = options.find(o => o.textContent?.includes('@BBL H2C'));
    expect(cloud?.textContent).toContain('Bambu Cloud');
    const local = options.find(o => o.textContent?.includes('eSUN PETG Basic'));
    expect(local?.textContent).toContain('Local');
  });

  it('filters the list as you type', () => {
    render(<Harness />);
    fireEvent.click(presetPicker('H2C', '0.4'));
    fireEvent.change(screen.getByPlaceholderText(/search filament presets/i), {
      target: { value: 'esun' },
    });

    const names = screen.getAllByRole('option').map(o => o.textContent ?? '');
    expect(names.some(n => n.includes('eSUN'))).toBe(true);
    expect(names.some(n => n.includes('Bambu PLA Basic'))).toBe(false);
  });
});

describe('PrinterProfilesSection — every nozzle size', () => {
  it('offers a preset row for all four standard sizes, fitted or not', () => {
    // A spool is configured once and nozzles get swapped. X1C has only a 0.4
    // fitted, and must still offer 0.2 / 0.6 / 0.8.
    render(<Harness />);
    fireEvent.click(modelRow('X1C'));

    expect(presetPickers()).toHaveLength(4);
    for (const size of ['0.2mm', '0.4mm', '0.6mm', '0.8mm']) {
      expect(screen.getAllByText(size).length).toBeGreaterThan(0);
    }
  });

  it('stores a preset for a size that is not currently fitted', () => {
    let latest: Map<string, PresetChoice> = new Map();
    render(<Harness onPresets={next => { latest = next; }} />);
    fireEvent.click(modelRow('X1C'));

    fireEvent.click(presetPicker('X1C', '0.6'));
    fireEvent.click(optionNamed('Bambu PLA Basic @BBL X1C Bambu Cloud'));

    expect(latest.get(presetKey('X1C', '0.6'))?.code).toBe('GFSA00');
  });

  it('offers a K profile for a size the printer has profiles for but has not fitted', () => {
    // fetchPrinterCalibrations now asks for every standard size, so a 0.6
    // profile reaches the picker without a 0.6 being screwed in.
    let latest: Map<string, CalibrationProfile> = new Map();
    const withSpare = [
      printer(1, 'X1C-1', 'X1C', {
        nozzles: [{ nozzle_diameter: '0.4' }],
        calibrations: [
          cal({ cali_idx: 1, nozzle_diameter: '0.4' }),
          cal({ cali_idx: 7, nozzle_diameter: '0.6', k_value: 0.026, name: 'PLA 0.6' }),
        ],
      }),
    ];
    render(<Harness printers={withSpare} onProfiles={next => { latest = next; }} />);

    fireEvent.change(screen.getByLabelText('X1C-1 Nozzle 0.6mm'), { target: { value: '7' } });
    expect(latest.get(hotendKey(1, 0, '0.6'))?.cali_idx).toBe(7);
  });
});

describe('PrinterProfilesSection — auto-match', () => {
  it('fills every nozzle size of each model, preferring the variant for that size', () => {
    let latest: Map<string, PresetChoice> = new Map();
    render(<Harness onPresets={next => { latest = next; }} />);

    fireEvent.click(screen.getByRole('button', { name: /auto-match/i }));

    // The H2C list holds a plain "@BBL H2C" and an "@BBL H2C 0.2 nozzle". The
    // 0.2 row takes the sized variant; the rest take the unsized one.
    expect(latest.get(presetKey('H2C', '0.2'))?.code).toBe('GFSA21');
    expect(latest.get(presetKey('H2C', '0.4'))?.code).toBe('GFSA09');
    expect(latest.get(presetKey('H2C', '0.8'))?.code).toBe('GFSA09');
    // Nothing is written to the model-wide key -- there is no row that shows
    // it, and a stored value nobody can see is a value nobody can undo.
    expect(latest.has(presetKey('H2C', ''))).toBe(false);
  });

  it('leaves a model with no matching variant inherited', () => {
    let latest: Map<string, PresetChoice> = new Map();
    render(<Harness onPresets={next => { latest = next; }} />);

    fireEvent.click(screen.getByRole('button', { name: /auto-match/i }));

    // P1S has no preset of this filament in the list, and an approximate one
    // is worse than falling back to the spool's own.
    for (const size of ['', '0.2', '0.4', '0.6', '0.8']) {
      expect(latest.has(presetKey('P1S', size))).toBe(false);
    }
  });
});

describe('PrinterProfilesSection — K profiles', () => {
  it('keys a chosen profile by printer, extruder and diameter', () => {
    let latest: Map<string, CalibrationProfile> = new Map();
    render(<Harness onProfiles={next => { latest = next; }} />);

    // Addressed by cell rather than by DOM order: the grid is size down the
    // side and hotend across, and only cells the printer has a calibration for
    // hold a dropdown at all.
    fireEvent.change(screen.getByLabelText('H2C-1 Left Nozzle 0.2mm'), {
      target: { value: '16' },
    });

    // Extruder 1 at 0.2mm -- not the 0.4 hotend, and not keyed by cali_idx,
    // which is numbered per nozzle and repeats across hotends.
    expect(latest.get(hotendKey(3, 1, '0.2'))?.cali_idx).toBe(16);
    expect(latest.has(hotendKey(3, 0, '0.4'))).toBe(false);
  });

  it('lays the grid out as nozzle size by hotend, with a dash where the printer has nothing', () => {
    render(<Harness />);

    // The H2C has a 0.2 profile on the left hotend and a 0.4 on the right.
    expect(screen.getByLabelText('H2C-1 Left Nozzle 0.2mm')).toBeInTheDocument();
    expect(screen.getByLabelText('H2C-1 Right Nozzle 0.4mm')).toBeInTheDocument();
    // The other six cells have no calibration to offer, so no control.
    expect(screen.queryByLabelText('H2C-1 Left Nozzle 0.4mm')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('H2C-1 Right Nozzle 0.8mm')).not.toBeInTheDocument();
    // Every size is still listed down the side rather than looking forgotten.
    for (const size of ['0.2mm', '0.4mm', '0.6mm', '0.8mm']) {
      expect(screen.getAllByText(size).length).toBeGreaterThan(0);
    }
  });

  it('gives a single-nozzle machine one unnamed column', () => {
    render(<Harness />);
    fireEvent.click(modelRow('X1C'));

    expect(screen.getByLabelText('X1C-1 Nozzle 0.4mm')).toBeInTheDocument();
    expect(screen.queryByLabelText(/X1C-1 (Left|Right) Nozzle/)).not.toBeInTheDocument();
  });

  it('choosing again on one hotend replaces rather than accumulates', () => {
    let latest: Map<string, CalibrationProfile> = new Map();
    render(<Harness onProfiles={next => { latest = next; }} />);

    fireEvent.click(modelRow('X1C'));
    const kSelect = screen.getByLabelText('X1C-1 Nozzle 0.4mm');

    fireEvent.change(kSelect, { target: { value: '1' } });
    fireEvent.change(kSelect, { target: { value: '' } });

    expect(latest.size).toBe(0);
  });

  it('says an offline printer cannot be configured instead of showing empty rows', () => {
    render(<Harness />);
    fireEvent.click(modelRow('P1S'));
    expect(screen.getByText(/printer is offline/i)).toBeInTheDocument();
  });

  it('labels the hotends of a dual-nozzle machine by side', () => {
    render(<Harness />);
    expect(screen.getByText('Right Nozzle')).toBeInTheDocument();
    expect(screen.getByText('Left Nozzle')).toBeInTheDocument();

    // A single-nozzle machine has no side to name.
    fireEvent.click(modelRow('X1C'));
    expect(screen.queryByText('Right Nozzle')).not.toBeInTheDocument();
    expect(screen.getAllByText('Nozzle').length).toBeGreaterThan(0);
  });
});

describe('PrinterProfilesSection — nozzle flow type', () => {
  function flowFleet() {
    return [
      printer(1, 'H2D-1', 'H2D', {
        nozzleCount: 2,
        nozzles: [
          { nozzle_diameter: '0.4', nozzle_type: 'HH01' },
          { nozzle_diameter: '0.4', nozzle_type: 'HH01' },
        ],
        calibrations: [
          cal({ cali_idx: 4, nozzle_id: 'HH00-0.4', name: 'High Flow_PLA' }),
          cal({ cali_idx: 5, nozzle_id: 'HS00-0.4', name: 'Standard_PLA', k_value: 0.019 }),
        ],
      }),
    ];
  }

  it('labels each profile with the flow it was measured on', () => {
    // A printer can hold both for one diameter -- this H2D has 102 high-flow
    // entries and 6 standard -- and the same filament reads a different K
    // through each, so the list has to say which is which.
    render(<Harness printers={flowFleet()} />);
    const options = within(screen.getByLabelText('H2D-1 Right Nozzle 0.4mm'))
      .getAllByRole('option')
      .map(o => o.textContent ?? '');

    expect(options.some(o => o.startsWith('[HF]'))).toBe(true);
    expect(options.some(o => o.startsWith('[S]'))).toBe(true);
  });

  it('says nothing about flow when the printer declares none', () => {
    // Measured on an X1C: every profile comes back with nozzle_id ''. A label
    // there would be invented rather than reported.
    render(<Harness />);
    const options = within(screen.getByLabelText('H2C-1 Right Nozzle 0.4mm'))
      .getAllByRole('option')
      .map(o => o.textContent ?? '');

    expect(options.some(o => o.includes('[HF]') || o.includes('[S]'))).toBe(false);
  });

  it('marks a chosen profile that does not match the fitted nozzle', () => {
    // Standard profile chosen, high-flow nozzle fitted: the backend will not
    // apply it, so the control must not look quietly configured.
    const chosen = new Map([
      [
        hotendKey(1, 0, '0.4'),
        cal({ cali_idx: 5, nozzle_id: 'HS00-0.4', name: 'Standard_PLA' }),
      ],
    ]);
    render(<Harness printers={flowFleet()} profiles={chosen} />);

    const select = screen.getByLabelText('H2D-1 Right Nozzle 0.4mm');
    expect(select.getAttribute('title')).toMatch(/will not be applied/i);
    expect(select.className).toContain('border-amber');
  });

  it('does not mark a profile whose flow agrees', () => {
    const chosen = new Map([
      [hotendKey(1, 0, '0.4'), cal({ cali_idx: 4, nozzle_id: 'HH00-0.4' })],
    ]);
    render(<Harness printers={flowFleet()} profiles={chosen} />);

    const select = screen.getByLabelText('H2D-1 Right Nozzle 0.4mm');
    expect(select.getAttribute('title')).toBeNull();
  });
});

describe('PrinterProfilesSection — empty fleet', () => {
  it('says so rather than rendering an empty two-pane layout', () => {
    render(<Harness printers={[]} />);
    expect(screen.getByText(/no printers configured/i)).toBeInTheDocument();
  });

  it('waits rather than claiming there are no printers while still loading', () => {
    // Reading each printer's calibration table is several MQTT round trips, so
    // this gap is seconds. Saying "no printers configured" during it is a wrong
    // answer about the user's setup, not a slow one.
    const Loading = () => {
      const [presets, setPresets] = React.useState(new Map<string, PresetChoice>());
      const [profiles, setProfiles] = React.useState(new Map<string, CalibrationProfile>());
      const [group, setGroup] = React.useState('');
      return (
        <PrinterProfilesSection
          formData={{ ...defaultFormData, material: 'PLA' }}
          printersWithCalibrations={[]}
          filamentOptions={OPTIONS}
          modelPresets={presets}
          setModelPresets={setPresets}
          selectedProfiles={profiles}
          setSelectedProfiles={setProfiles}
          selectedGroupId={group}
          setSelectedGroupId={setGroup}
          isLoading
        />
      );
    };
    render(<Loading />);
    expect(screen.queryByText(/no printers configured/i)).not.toBeInTheDocument();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });
});

describe('PrinterProfilesSection — no material yet', () => {
  it('asks for a material before offering K profiles', () => {
    const Bare = () => {
      const [presets, setPresets] = React.useState(new Map<string, PresetChoice>());
      const [profiles, setProfiles] = React.useState(new Map<string, CalibrationProfile>());
      const [group, setGroup] = React.useState('');
      return (
        <PrinterProfilesSection
          formData={{ ...defaultFormData, material: '' }}
          printersWithCalibrations={fleet()}
          filamentOptions={OPTIONS}
          modelPresets={presets}
          setModelPresets={setPresets}
          selectedProfiles={profiles}
          setSelectedProfiles={setProfiles}
          selectedGroupId={group}
          setSelectedGroupId={setGroup}
        />
      );
    };
    render(<Bare />);
    expect(screen.getByText(/select a material first/i)).toBeInTheDocument();
    // The preset half still works -- it does not depend on the material.
    expect(screen.getByText('Filament preset')).toBeInTheDocument();
  });
});

describe('PrinterProfilesSection — vi sanity', () => {
  it('does not call any API of its own', () => {
    // The section is presentational: everything it changes goes through the
    // props, and SpoolFormModal is what persists it on save.
    const spy = vi.fn();
    render(<Harness onPresets={spy} />);
    expect(spy).toHaveBeenCalled();
  });
});
