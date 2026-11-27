/**
 * The Slice dialog's nozzle flow (#3136).
 *
 * Sliced with profiles, the slicer cuts for a Standard nozzle unless told
 * otherwise, and a P2S with a High Flow nozzle then warned that the file did
 * not match it. The dialog offers Standard / High Flow on models sold with
 * both, takes its default from the first online printer of the model that
 * reports its nozzle flow at the profile's size, and sends the flow.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import { SliceModal } from '../../components/SliceModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import {
  api,
  type LoadedSpoolPrinter,
  type UnifiedPresetsResponse,
} from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSlicerPresets: vi.fn(),
    sliceLibraryFile: vi.fn(),
    sliceArchive: vi.fn(),
    getSliceJob: vi.fn(),
    getLibraryFilePlates: vi.fn(),
    getArchivePlates: vi.fn(),
    getLibraryFileFilamentRequirements: vi.fn(),
    getArchiveFilamentRequirements: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    listSlicerPipelines: vi.fn(),
    createSlicerPipeline: vi.fn(),
    getSlicerPrinterModels: vi.fn(),
    getSlicerPresetValues: vi.fn(),
    getSlicerLoadedSpools: vi.fn(),
  },
}));

const mockApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function presets(printer: string, model: string): UnifiedPresetsResponse {
  const empty = { printer: [], process: [], filament: [] };
  return {
    orca_cloud: empty,
    cloud: empty,
    local: empty,
    standard: {
      printer: [{ id: printer, name: printer, source: 'standard' }],
      process: [{ id: `0.20mm Standard @BBL ${model}`, name: `0.20mm Standard @BBL ${model}`, source: 'standard' }],
      filament: [
        { id: `Bambu PLA Basic @BBL ${model}`, name: `Bambu PLA Basic @BBL ${model}`, source: 'standard', filament_type: 'PLA' },
      ],
    },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
  };
}

function printer(name: string, model: string, nozzles: LoadedSpoolPrinter['nozzles']): LoadedSpoolPrinter {
  return { id: name.length, name, model, ams: [], external: [], external_holders: 1, nozzles };
}

function setup(opts: { printer: string; model: string; online: LoadedSpoolPrinter[] }) {
  mockApi.getSlicerPresets.mockResolvedValue(presets(opts.printer, opts.model));
  mockApi.getSlicerLoadedSpools.mockResolvedValue({ printers: opts.online });
  render(
    <SliceJobTrackerProvider>
      <SliceModal source={{ kind: 'libraryFile', id: 100, filename: 'Cube.stl' }} onClose={vi.fn()} />
    </SliceJobTrackerProvider>,
  );
}

async function slice(): Promise<Record<string, unknown>> {
  await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: /^Slice$/ }));
  await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
  return mockApi.sliceLibraryFile.mock.calls[0][1] as Record<string, unknown>;
}

const P2S_08 = 'Bambu Lab P2S 0.8 nozzle';

describe('SliceModal — nozzle flow (#3136)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
    mockApi.getSlicerPrinterModels.mockResolvedValue({
      'Bambu Lab P2S': 'P2S',
      'Bambu Lab H2D': 'H2D',
      'Bambu Lab A1': 'A1',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue({ file_id: 100, filename: 'Cube.stl', plates: [], is_multi_plate: false });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plate_id: 1,
      filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 1, used_meters: 1 }],
    });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.sliceLibraryFile.mockResolvedValue({ job_id: 1, status: 'pending', status_url: '/api/v1/slice-jobs/1' });
  });

  it('slices for the High Flow nozzle the P2S has fitted', async () => {
    setup({
      printer: P2S_08,
      model: 'P2S',
      online: [printer('Workshop P2S', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: 'High Flow' }])],
    });
    const flow = (await screen.findByLabelText('Nozzle flow')) as HTMLSelectElement;
    await waitFor(() => expect(flow.value).toBe('High Flow'));
    expect(screen.getByText('As fitted on Workshop P2S')).toBeInTheDocument();
    expect((await slice()).nozzle_volume_type).toEqual(['High Flow']);
  });

  it('follows the first printer when two disagree', async () => {
    setup({
      printer: P2S_08,
      model: 'P2S',
      online: [
        printer('Attic', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: 'Standard' }]),
        printer('Workshop', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: 'High Flow' }]),
      ],
    });
    const flow = (await screen.findByLabelText('Nozzle flow')) as HTMLSelectElement;
    await waitFor(() => expect(screen.getByText('As fitted on Attic')).toBeInTheDocument());
    expect(flow.value).toBe('Standard');
  });

  it('skips a printer with a nozzle of another size', async () => {
    setup({
      printer: P2S_08,
      model: 'P2S',
      online: [
        printer('Attic', 'P2S', [{ extruder_id: 0, diameter: '0.4', flow: 'Standard' }]),
        printer('Workshop', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: 'High Flow' }]),
      ],
    });
    await waitFor(() => expect(screen.getByText('As fitted on Workshop')).toBeInTheDocument());
  });

  it('sends the flow the user picks', async () => {
    setup({
      printer: P2S_08,
      model: 'P2S',
      online: [printer('Workshop', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: 'High Flow' }])],
    });
    const flow = (await screen.findByLabelText('Nozzle flow')) as HTMLSelectElement;
    await waitFor(() => expect(flow.value).toBe('High Flow'));
    fireEvent.change(flow, { target: { value: 'Standard' } });
    expect(screen.queryByText('As fitted on Workshop')).toBeNull();
    expect((await slice()).nozzle_volume_type).toEqual(['Standard']);
  });

  it('leaves the flow to the backend when no printer says', async () => {
    // X1 / P1 report the nozzle material, not its flow.
    setup({
      printer: P2S_08,
      model: 'P2S',
      online: [printer('Workshop', 'P2S', [{ extruder_id: 0, diameter: '0.8', flow: null }])],
    });
    const flow = (await screen.findByLabelText('Nozzle flow')) as HTMLSelectElement;
    expect(flow.value).toBe('Standard');
    expect(screen.getByText(/No online printer of this model reports its nozzle flow/)).toBeInTheDocument();
    expect(await slice()).not.toHaveProperty('nozzle_volume_type');
  });

  it('gives each nozzle of a dual-nozzle printer its own flow, left first', async () => {
    setup({
      printer: 'Bambu Lab H2D 0.4 nozzle',
      model: 'H2D',
      online: [
        printer('Farm H2D', 'H2D', [
          { extruder_id: 0, diameter: '0.4', flow: 'Standard' }, // right
          { extruder_id: 1, diameter: '0.4', flow: 'High Flow' }, // left
        ]),
      ],
    });
    const box = await screen.findByTestId('slice-nozzle-flow');
    const left = within(box).getByLabelText('Nozzle flow: Left') as HTMLSelectElement;
    const right = within(box).getByLabelText('Nozzle flow: Right') as HTMLSelectElement;
    await waitFor(() => expect(left.value).toBe('High Flow'));
    expect(right.value).toBe('Standard');
    expect((await slice()).nozzle_volume_type).toEqual(['High Flow', 'Standard']);
  });

  it('offers nothing on a model sold with one flow only', async () => {
    setup({ printer: 'Bambu Lab A1 0.4 nozzle', model: 'A1', online: [] });
    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());
    expect(screen.queryByTestId('slice-nozzle-flow')).toBeNull();
    expect(await slice()).not.toHaveProperty('nozzle_volume_type');
  });
});
