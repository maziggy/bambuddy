/**
 * The spool form's optional nozzle temperature range, prefilled from the
 * chosen slicer preset (#3769).
 *
 * The range is saved when the user typed it or a preset filled it in; one that
 * was only loaded with the spool is left out of an edit (#3768, covered by its
 * own test file).
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import { nozzleTempsPayload, readNozzleTemps } from '../../components/spool-form/utils';
import type { NozzleTemps } from '../../components/spool-form/types';
import type { InventorySpool, PresetNozzleRange } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    orcaCloudStatus: vi.fn().mockResolvedValue({ connected: false }),
    orcaCloudListProfiles: vi.fn().mockResolvedValue({ filament: [] }),
    getFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getLocations: vi.fn().mockResolvedValue([]),
    getSuppliers: vi.fn().mockResolvedValue([]),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([
      { filament_id: 'GFB01', name: 'Bambu ASA' },
      { filament_id: 'GFL99', name: 'Generic PLA' },
    ]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue(null),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    getSpools: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 7 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    resolveProfileDefault: vi.fn().mockResolvedValue(null),
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    setThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    threedfpStatus: vi.fn().mockResolvedValue({ connected: false, email: null }),
    threedfpLookup: vi.fn().mockResolvedValue({ results: [] }),
    getSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolmanInventoryFilaments: vi.fn().mockResolvedValue([]),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 77 }),
    getAssignments: vi.fn().mockResolvedValue([]),
    unassignSpool: vi.fn().mockResolvedValue({}),
    getPresetNozzleRange: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: vi.fn() }) };
});

import { api } from '../../api/client';

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

// The test setup replaces localStorage with bare mocks; back them with a map.
const store = new Map<string, string>();
const KEY = 'bambuddy-last-spool';

const PLA_RANGE: PresetNozzleRange = { nozzle_temp_min: 190, nozzle_temp_max: 240, source: 'system' };
const NO_RANGE: PresetNozzleRange = { nozzle_temp_min: null, nozzle_temp_max: null, source: null };

const spoolWith = (own: { min: number | null; max: number | null }): InventorySpool =>
  ({
    id: 7,
    material: 'ASA',
    subtype: null,
    brand: null,
    color_name: null,
    rgba: '808080FF',
    extra_colors: null,
    effect_type: null,
    label_weight: 1000,
    core_weight: 250,
    core_weight_catalog_id: null,
    weight_used: 0,
    slicer_filament: 'GFB01',
    slicer_filament_name: 'Bambu ASA',
    nozzle_temp_min: own.min,
    nozzle_temp_max: own.max,
    note: null,
    tag_uid: null,
    tray_uuid: null,
    k_profiles: [],
    suppliers: [],
  }) as unknown as InventorySpool;

const minInput = () => screen.getByLabelText('Min') as HTMLInputElement;
const maxInput = () => screen.getByLabelText('Max') as HTMLInputElement;

function renderForm(props: Partial<React.ComponentProps<typeof SpoolFormModal>> = {}) {
  return render(<SpoolFormModal isOpen onClose={vi.fn()} mode="create" currencySymbol="$" {...props} />);
}

function submit(label: string | RegExp) {
  const buttons = screen.getAllByRole('button', { name: label });
  fireEvent.click(buttons[buttons.length - 1]);
}

async function settle() {
  await waitFor(() => expect(mApi.getLocations).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 80));
}

// Picks a preset in the dialog's own preset search, on the Filament tab.
async function pickPreset(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name: /Filament/ }));
  const presetInput = await screen.findByPlaceholderText('Search filament presets...');
  fireEvent.focus(presetInput);
  fireEvent.click(await screen.findByRole('button', { name }));
}

const typeInto = (input: HTMLInputElement, value: string) => fireEvent.change(input, { target: { value } });

describe('SpoolFormModal nozzle range from the preset (#3769)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.clear();
    vi.mocked(localStorage.getItem).mockImplementation((k: string) => store.get(k) ?? null);
    vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => {
      store.set(k, v);
    });
    mApi.getPresetNozzleRange.mockResolvedValue(PLA_RANGE);
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99' }));
  });

  describe('a new spool', () => {
    it('fills the range in from its preset, marks it, and saves it', async () => {
      renderForm();

      await waitFor(() => expect(minInput().value).toBe('190'));
      expect(maxInput().value).toBe('240');
      expect(mApi.getPresetNozzleRange).toHaveBeenCalledWith('GFL99');
      expect(screen.getByText('from preset')).toBeInTheDocument();

      await settle();
      submit('Add Spool');
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      expect(mApi.createSpool.mock.calls[0][0]).toMatchObject({ nozzle_temp_min: 190, nozzle_temp_max: 240 });
    });

    it('saves the range the user typed instead, and drops the mark', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));

      typeInto(minInput(), '200');

      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
      await settle();
      submit('Add Spool');
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      expect(mApi.createSpool.mock.calls[0][0]).toMatchObject({ nozzle_temp_min: 200, nozzle_temp_max: 240 });
    });

    it('leaves a range typed before the lookup answered alone', async () => {
      let answer!: (range: PresetNozzleRange) => void;
      mApi.getPresetNozzleRange.mockReturnValue(new Promise<PresetNozzleRange>((resolve) => { answer = resolve; }));
      renderForm();
      await waitFor(() => expect(mApi.getPresetNozzleRange).toHaveBeenCalled());

      typeInto(minInput(), '210');
      typeInto(maxInput(), '250');
      answer(PLA_RANGE);
      await new Promise((r) => setTimeout(r, 30));

      expect(minInput().value).toBe('210');
      expect(maxInput().value).toBe('250');
      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
    });

    it('sends nothing for a preset that has no range', async () => {
      mApi.getPresetNozzleRange.mockResolvedValue(NO_RANGE);
      renderForm();
      await settle();

      expect(mApi.getPresetNozzleRange).toHaveBeenCalled();
      expect(minInput().value).toBe('');
      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
      submit('Add Spool');
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      const sent = mApi.createSpool.mock.calls[0][0] as Record<string, unknown>;
      expect(sent).not.toHaveProperty('nozzle_temp_min');
      expect(sent).not.toHaveProperty('nozzle_temp_max');
    });

    it('starts empty and saves without a range when the lookup fails', async () => {
      mApi.getPresetNozzleRange.mockRejectedValue(new Error('offline'));
      renderForm();
      await settle();

      expect(minInput().value).toBe('');
      submit('Add Spool');
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      expect(mApi.createSpool.mock.calls[0][0]).not.toHaveProperty('nozzle_temp_min');
    });

    it('does not look anything up without a preset', async () => {
      store.clear();
      renderForm();
      await settle();

      expect(mApi.getPresetNozzleRange).not.toHaveBeenCalled();
      expect(minInput().value).toBe('');
    });

    it('refuses a range with only one end, and saves nothing', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));
      await settle();

      typeInto(maxInput(), '');
      submit('Add Spool');

      expect(await screen.findByRole('alert')).toHaveTextContent('Enter both the minimum and the maximum');
      expect(mApi.createSpool).not.toHaveBeenCalled();
    });

    it('refuses a minimum above the maximum', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));
      await settle();

      typeInto(minInput(), '260');
      submit('Add Spool');

      expect(await screen.findByRole('alert')).toHaveTextContent('Enter whole degrees from 1 to 500');
      expect(mApi.createSpool).not.toHaveBeenCalled();
    });

    it('takes the error away again once the range is edited', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));
      await settle();
      typeInto(minInput(), '260');
      submit('Add Spool');
      await screen.findByRole('alert');

      typeInto(minInput(), '200');

      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
    it('shows the error on the Filament Info tab when saving from another tab', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));
      await settle();
      typeInto(minInput(), '260');
      fireEvent.click(screen.getByRole('button', { name: /Color & Cost/ }));
      expect(screen.queryByLabelText('Min')).not.toBeInTheDocument();

      submit('Add Spool');

      expect(await screen.findByRole('alert')).toHaveTextContent('Enter whole degrees from 1 to 500');
      expect(mApi.createSpool).not.toHaveBeenCalled();
    });

    it('is not offered in Quick Add, which has no preset', async () => {
      renderForm();
      await waitFor(() => expect(minInput().value).toBe('190'));

      fireEvent.click(screen.getByRole('switch', { name: /Quick Add/ }));

      await waitFor(() => expect(screen.queryByLabelText('Min')).not.toBeInTheDocument());
    });
  });

  describe('an existing spool', () => {
    it('shows its own range without the mark, and does not look it up', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: 230, max: 260 }) });

      await waitFor(() => expect(minInput().value).toBe('230'));
      expect(maxInput().value).toBe('260');
      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
      await settle();
      expect(mApi.getPresetNozzleRange).not.toHaveBeenCalled();
    });

    it('keeps the stored range on an edit that does not touch it', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: 230, max: 260 }) });
      await waitFor(() => expect(minInput().value).toBe('230'));
      await settle();

      submit(/save/i);

      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      const sent = mApi.updateSpool.mock.calls[0][1] as Record<string, unknown>;
      expect(sent).not.toHaveProperty('nozzle_temp_min');
      expect(sent).not.toHaveProperty('nozzle_temp_max');
    });

    it('does not fill a spool without a range from its preset just by being opened', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: null, max: null }) });
      await settle();

      expect(mApi.getPresetNozzleRange).not.toHaveBeenCalled();
      expect(minInput().value).toBe('');
      submit(/save/i);
      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      expect(mApi.updateSpool.mock.calls[0][1]).not.toHaveProperty('nozzle_temp_min');
    });

    it('saves a range the user changed', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: 230, max: 260 }) });
      await waitFor(() => expect(minInput().value).toBe('230'));
      await settle();

      typeInto(maxInput(), '270');
      submit(/save/i);

      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      expect(mApi.updateSpool.mock.calls[0][1]).toMatchObject({ nozzle_temp_min: 230, nozzle_temp_max: 270 });
    });

    it('clears the stored range when the user empties both ends', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: 230, max: 260 }) });
      await waitFor(() => expect(minInput().value).toBe('230'));
      await settle();

      typeInto(minInput(), '');
      typeInto(maxInput(), '');
      submit(/save/i);

      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      expect(mApi.updateSpool.mock.calls[0][1]).toMatchObject({ nozzle_temp_min: null, nozzle_temp_max: null });
    });

    it('does not carry the previous spool\'s preset range onto the next one opened', async () => {
      const { rerender } = renderForm({ mode: 'edit', spool: spoolWith({ min: null, max: null }) });
      await settle();
      rerender(<SpoolFormModal isOpen onClose={vi.fn()} mode="edit" spool={{ ...spoolWith({ min: 220, max: 250 }), id: 8 }} currencySymbol="$" />);

      await waitFor(() => expect(minInput().value).toBe('220'));
      expect(mApi.getPresetNozzleRange).not.toHaveBeenCalled();
    });
  });

  describe('picking a preset in the dialog', () => {
    beforeEach(() => {
      store.clear();
    });

    it('fills a new spool\'s range in', async () => {
      renderForm();
      await pickPreset(/^Generic PLA\b/);

      await waitFor(() => expect(mApi.getPresetNozzleRange).toHaveBeenCalledWith('GFL99'));
      await waitFor(() => expect(minInput().value).toBe('190'));
      expect(maxInput().value).toBe('240');
      expect(screen.getByText('from preset')).toBeInTheDocument();
    });

    it('fills in an edited spool that has no range, and saves it because the user picked the preset', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: null, max: null }) });
      await settle();
      await pickPreset(/^Generic PLA\b/);

      await waitFor(() => expect(mApi.getPresetNozzleRange).toHaveBeenCalledWith('GFL99'));
      await waitFor(() => expect(minInput().value).toBe('190'));
      submit(/save/i);
      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      expect(mApi.updateSpool.mock.calls[0][1]).toMatchObject({
        slicer_filament: 'GFL99',
        nozzle_temp_min: 190,
        nozzle_temp_max: 240,
      });
    });

    it('does not replace the range the spool already has', async () => {
      renderForm({ mode: 'edit', spool: spoolWith({ min: 230, max: 260 }) });
      await settle();
      await pickPreset(/^Generic PLA\b/);

      await waitFor(() => expect(mApi.getPresetNozzleRange).toHaveBeenCalledWith('GFL99'));
      await new Promise((r) => setTimeout(r, 30));
      expect(minInput().value).toBe('230');
      expect(maxInput().value).toBe('260');
      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
      submit(/save/i);
      await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
      expect(mApi.updateSpool.mock.calls[0][1]).not.toHaveProperty('nozzle_temp_min');
    });

    it('takes a preset\'s range back out when the preset is cleared', async () => {
      renderForm();
      await pickPreset(/^Generic PLA\b/);
      await waitFor(() => expect(minInput().value).toBe('190'));

      fireEvent.click(await screen.findByRole('button', { name: 'Remove preset' }));

      await waitFor(() => expect(minInput().value).toBe(''));
      expect(screen.queryByText('from preset')).not.toBeInTheDocument();
    });
  });

  describe('Spoolman', () => {
    it('has no range field and looks nothing up', async () => {
      renderForm({ spoolmanMode: true });
      await settle();

      expect(screen.queryByLabelText('Min')).not.toBeInTheDocument();
      expect(mApi.getPresetNozzleRange).not.toHaveBeenCalled();
    });
  });
});

describe('nozzle range helpers (#3769)', () => {
  const temps = (min: string, max: string, source: NozzleTemps['source']): NozzleTemps => ({ min, max, source });
  const create = { isCopying: false, spoolmanMode: false };

  it('reads a range, or says why it is none', () => {
    expect(readNozzleTemps(temps('190', '240', 'typed'))).toEqual({ min: 190, max: 240, error: null });
    expect(readNozzleTemps(temps(' 200 ', '200', 'typed'))).toEqual({ min: 200, max: 200, error: null });
    expect(readNozzleTemps(temps('', '', 'typed'))).toEqual({ min: null, max: null, error: null });
    expect(readNozzleTemps(temps('190', '', 'typed')).error).toBe('nozzleTempIncomplete');
    expect(readNozzleTemps(temps('', '240', 'typed')).error).toBe('nozzleTempIncomplete');
    for (const [min, max] of [['240', '190'], ['0', '240'], ['-5', '240'], ['190.5', '240'], ['190', '501'], ['abc', '240']]) {
      expect(readNozzleTemps(temps(min, max, 'typed')).error).toBe('nozzleTempInvalid');
    }
  });

  it('sends what was typed or filled in from a preset', () => {
    expect(nozzleTempsPayload(temps('190', '240', 'typed'), create)).toEqual({ nozzle_temp_min: 190, nozzle_temp_max: 240 });
    expect(nozzleTempsPayload(temps('190', '240', 'preset'), create)).toEqual({ nozzle_temp_min: 190, nozzle_temp_max: 240 });
    expect(nozzleTempsPayload(temps('', '', 'typed'), create)).toEqual({ nozzle_temp_min: null, nozzle_temp_max: null });
  });

  it('sends nothing for a range nobody entered, except a copy of the spool\'s own', () => {
    expect(nozzleTempsPayload(temps('', '', 'none'), create)).toEqual({});
    expect(nozzleTempsPayload(temps('230', '260', 'own'), create)).toEqual({});
    expect(nozzleTempsPayload(temps('230', '260', 'own'), { isCopying: true, spoolmanMode: false })).toEqual({
      nozzle_temp_min: 230,
      nozzle_temp_max: 260,
    });
  });

  it('hands a copy the stored range as it is, even one that would not pass for typed input', () => {
    expect(nozzleTempsPayload(temps('300', '100', 'own'), { isCopying: true, spoolmanMode: false })).toEqual({
      nozzle_temp_min: 300,
      nozzle_temp_max: 100,
    });
    expect(nozzleTempsPayload(temps('230', '', 'own'), { isCopying: true, spoolmanMode: false })).toEqual({
      nozzle_temp_min: 230,
      nozzle_temp_max: null,
    });
  });

  it('sends nothing in Spoolman mode', () => {
    expect(nozzleTempsPayload(temps('190', '240', 'typed'), { isCopying: false, spoolmanMode: true })).toEqual({});
  });
});
