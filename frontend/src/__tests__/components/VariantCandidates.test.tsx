/**
 * Cross-model candidate list (#671).
 *
 * The list carries the one decision the user makes that the scheduler cannot:
 * which printer they would rather have when more than one is free. Order is
 * that decision, so it has to be visible and editable.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { VariantCandidates, type VariantCandidate } from '../../components/PrintModal/VariantCandidates';
import { api } from '../../api/client';

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client');
  return {
    ...actual,
    api: { ...actual.api, getLibraryFilePlates: vi.fn() },
  };
});

const CANDIDATES: VariantCandidate[] = [
  { id: 1, filename: 'bracket_h2s.gcode.3mf', sliced_for_model: 'H2S' },
  { id: 2, filename: 'bracket_h2c.gcode.3mf', sliced_for_model: 'H2C' },
];

function setup(overrides: Partial<React.ComponentProps<typeof VariantCandidates>> = {}) {
  const onReorder = vi.fn();
  const onPlateChange = vi.fn();
  render(
    <VariantCandidates
      candidates={CANDIDATES}
      onReorder={onReorder}
      plateByFile={{}}
      onPlateChange={onPlateChange}
      {...overrides}
    />,
  );
  return { onReorder, onPlateChange };
}

describe('VariantCandidates', () => {
  beforeEach(() => {
    vi.mocked(api.getLibraryFilePlates).mockResolvedValue({
      file_id: 1,
      filename: 'x',
      plates: [],
      is_multi_plate: false,
    });
  });

  it('lists every candidate with the model its file was sliced for', async () => {
    setup();
    expect(await screen.findByText('bracket_h2s.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('bracket_h2c.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('H2S')).toBeInTheDocument();
    expect(screen.getByText('H2C')).toBeInTheDocument();
  });

  it('moves a candidate down, which is how priority is expressed', async () => {
    const user = userEvent.setup();
    const { onReorder } = setup();

    const downButtons = await screen.findAllByLabelText('Move down');
    await user.click(downButtons[0]);

    expect(onReorder).toHaveBeenCalledWith([CANDIDATES[1], CANDIDATES[0]]);
  });

  it('cannot move the first candidate up or the last one down', async () => {
    setup();
    const up = await screen.findAllByLabelText('Move up');
    const down = await screen.findAllByLabelText('Move down');
    expect(up[0]).toBeDisabled();
    expect(down[down.length - 1]).toBeDisabled();
  });

  it('offers a plate picker only for the candidates that have several plates', async () => {
    vi.mocked(api.getLibraryFilePlates).mockImplementation(async (fileId: number) =>
      fileId === 2
        ? {
            file_id: 2,
            filename: 'bracket_h2c.gcode.3mf',
            is_multi_plate: true,
            plates: [
              { index: 1, name: 'Plate 1', objects: [], has_thumbnail: false, thumbnail_url: null, print_time_seconds: null, filament_used_grams: null, filaments: [] },
              { index: 2, name: 'Plate 2', objects: [], has_thumbnail: false, thumbnail_url: null, print_time_seconds: null, filament_used_grams: null, filaments: [] },
            ],
          }
        : { file_id: fileId, filename: 'x', is_multi_plate: false, plates: [] },
    );

    setup();

    // One picker, for the multi-plate file only — a single-plate candidate has
    // nothing to choose and the control would just be noise.
    await waitFor(() => expect(screen.getAllByRole('combobox')).toHaveLength(1));
    expect(screen.getByLabelText('Plate for bracket_h2c.gcode.3mf')).toBeInTheDocument();
  });

  it('reports the chosen plate against the file it belongs to', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLibraryFilePlates).mockImplementation(async (fileId: number) => ({
      file_id: fileId,
      filename: 'x',
      is_multi_plate: true,
      plates: [
        { index: 1, name: 'Plate 1', objects: [], has_thumbnail: false, thumbnail_url: null, print_time_seconds: null, filament_used_grams: null, filaments: [] },
        { index: 2, name: 'Plate 2', objects: [], has_thumbnail: false, thumbnail_url: null, print_time_seconds: null, filament_used_grams: null, filaments: [] },
      ],
    }));

    const { onPlateChange } = setup();

    const pickers = await screen.findAllByRole('combobox');
    await user.selectOptions(pickers[1], '2');

    expect(onPlateChange).toHaveBeenCalledWith(2, 2);
  });
});
