/**
 * PrintModal in cross-model mode (#671).
 *
 * Selecting several sliced files puts the modal in model-based assignment with
 * no single target model. That combination used to fall through every gate the
 * override UI depends on, leaving the user with *less* control than the
 * ordinary "Any X1C" flow — no AMS mapping (correct, there is no printer yet)
 * and no filament override either (wrong).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintModal } from '../../components/PrintModal';

const CANDIDATES = [
  { id: 11, filename: 'x1c.gcode.3mf', sliced_for_model: 'X1C' },
  { id: 12, filename: 'h2d.gcode.3mf', sliced_for_model: 'H2D' },
];

/** Loaded filaments differ per model — the union is what the user may pick.
 *  The dropdown only ever offers the slot's own material (overriding PLA with
 *  PETG is not a colour choice), so the spool that proves the union works has
 *  to be a PLA the X1C does not have. */
const BY_MODEL: Record<string, Array<Record<string, unknown>>> = {
  X1C: [{ type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null }],
  H2D: [
    { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null },
    { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Matte', extruder_id: null },
  ],
};

function mockBackend() {
  server.use(
    http.get('/api/v1/printers/', () =>
      HttpResponse.json([
        { id: 1, name: 'X1C-1', model: 'X1C', ip_address: '10.0.0.1', is_active: true, enabled: true },
        { id: 2, name: 'H2D-1', model: 'H2D', ip_address: '10.0.0.2', is_active: true, enabled: true },
      ]),
    ),
    http.get('/api/v1/printers/available-filaments', ({ request }) => {
      const model = new URL(request.url).searchParams.get('model') ?? '';
      return HttpResponse.json(BY_MODEL[model] ?? []);
    }),
    http.get('/api/v1/library/files/:id', ({ params }) =>
      HttpResponse.json({
        id: Number(params.id),
        filename: 'x1c.gcode.3mf',
        file_type: 'gcode.3mf',
        sliced_for_model: 'X1C',
      }),
    ),
    http.get('/api/v1/library/files/:id/plates', ({ params }) =>
      HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: [], is_multi_plate: false }),
    ),
    http.get('/api/v1/library/files/:id/filament-requirements', () =>
      HttpResponse.json({
        filaments: [{ slot_id: 1, type: 'PLA', color: '#FFFFFF', used_grams: 15, used_meters: 5 }],
      }),
    ),
  );
}

function renderCrossModel() {
  render(
    <PrintModal
      mode="create"
      libraryFileId={CANDIDATES[0].id}
      variantFiles={CANDIDATES}
      archiveName="bracket"
      onClose={() => {}}
    />,
  );
}

describe('PrintModal cross-model mode', () => {
  beforeEach(() => mockBackend());

  it('replaces the printer picker with the candidate list', async () => {
    renderCrossModel();
    expect(await screen.findByText('x1c.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('h2d.gcode.3mf')).toBeInTheDocument();
    // Choosing these files already answered "which printer".
    expect(screen.queryByText('Select Printer')).not.toBeInTheDocument();
  });

  it('offers filament overrides drawn from every candidate model', async () => {
    renderCrossModel();

    expect(await screen.findByText('Filament Override')).toBeInTheDocument();

    // PLA Matte is loaded only on the H2D. It has to be offered anyway: the job
    // can land there, and choosing it simply narrows which candidates match.
    // The control is a listbox since it gained a swatch per choice (#3159), so
    // its rows exist only while it is open.
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    fireEvent.click(trigger);
    await waitFor(() => {
      const options = within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .map((o) => o.textContent ?? '');
      expect(options.some((o) => o.includes('PLA Matte'))).toBe(true);
      expect(options.some((o) => o.includes('PLA Basic'))).toBe(true);
    });
  });

  it('shows a queued job its alternatives instead of a printer picker', async () => {
    // Before this, editing a cross-model item showed "Any H2D" with a live
    // Target Model dropdown and a Specific Printer toggle. Saving that left a
    // row with variants AND a printer_id, and the fixed-printer branch of the
    // scheduler wins — dispatching a row whose library_file_id is still null.
    render(
      <PrintModal
        mode="edit-queue-item"
        libraryFileId={CANDIDATES[0].id}
        archiveName="bracket"
        queueItem={
          {
            id: 9,
            printer_id: null,
            target_model: 'H2D',
            status: 'pending',
            variants: [
              { library_file_id: 12, filename: 'h2d.gcode.3mf', target_model: 'H2D', position: 0 },
              { library_file_id: 11, filename: 'x1c.gcode.3mf', target_model: 'X1C', position: 1 },
            ],
          } as never
        }
        onClose={() => {}}
      />,
    );

    expect(await screen.findByText('h2d.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('x1c.gcode.3mf')).toBeInTheDocument();
    expect(screen.queryByText('Target Model')).not.toBeInTheDocument();
    // Read-only: reordering after queueing would need a variant-level API.
    expect(screen.queryByLabelText('Move down')).not.toBeInTheDocument();
  });

  it('shows no AMS slot mapping, because no printer has been chosen yet', async () => {
    renderCrossModel();
    await screen.findByText('x1c.gcode.3mf');
    // The scheduler derives the mapping against whichever printer it picks —
    // collecting tray numbers here would only be thrown away.
    expect(screen.queryByText('Filament Mapping')).not.toBeInTheDocument();
  });
});

/**
 * Cross-model on a multi-plate file (#3101).
 *
 * The two features met badly. A multi-plate file moves quantity onto the
 * per-plate steppers and hides the global field (#342), but the cross-model
 * submit posts the global one — which in that configuration nothing can
 * change. The reporter asked for 19 runs across an X1C and a P2S, watched the
 * modal say "19 runs in total", and got exactly one print.
 */
describe('PrintModal cross-model mode on a multi-plate file', () => {
  const PLATES = [1, 2, 3].map((i) => ({
    index: i,
    name: `Plate ${i}`,
    objects: ['bracket'],
    filaments: [{ slot_id: 1, type: 'PETG', color: '#FFFFFF' }],
    has_thumbnail: false,
    thumbnail_url: null,
    bed_type: null,
    print_time_seconds: 3600,
  }));

  let posted: Record<string, unknown> | null;
  let reqUrls: string[];

  beforeEach(() => {
    posted = null;
    reqUrls = [];
    mockBackend();
    server.use(
      http.get('/api/v1/library/files/:id/plates', ({ params }) =>
        HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: PLATES, is_multi_plate: true }),
      ),
      http.get('/api/v1/library/files/:id/filament-requirements', ({ request }) => {
        reqUrls.push(request.url);
        return HttpResponse.json({
          filaments: [{ slot_id: 1, type: 'PETG', color: '#FFFFFF', used_grams: 15, used_meters: 5 }],
        });
      }),
      http.post('/api/v1/queue/', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, status: 'pending', variants: [] });
      }),
    );
  });

  it('offers the global Quantity field, not the per-plate steppers', async () => {
    renderCrossModel();
    await screen.findByText('x1c.gcode.3mf');

    // The plate selector is gone: its choice never reached the request, and the
    // candidate list below is where a cross-model job picks its plates.
    expect(screen.queryByRole('button', { name: /Select All/i })).toBeNull();
    expect(await screen.findByLabelText('Quantity')).toBeInTheDocument();
  });

  it('queues the number of copies the user asked for', async () => {
    const user = userEvent.setup();
    renderCrossModel();
    await screen.findByText('x1c.gcode.3mf');

    fireEvent.change(await screen.findByLabelText('Quantity'), { target: { value: '19' } });
    await user.click(screen.getByRole('button', { name: /^Print$/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted!.quantity).toBe(19);
    // One job with both candidates, nineteen times over — not nineteen jobs
    // each pinned to a model, and not one job.
    expect((posted!.variants as Array<{ library_file_id: number }>).map((v) => v.library_file_id))
      .toEqual([11, 12]);
  });

  it('reads filament requirements for the plate the primary candidate will run', async () => {
    const user = userEvent.setup();
    renderCrossModel();
    await screen.findByText('x1c.gcode.3mf');
    await waitFor(() => expect(reqUrls.some((u) => u.includes('plate_id=1'))).toBe(true));

    await user.selectOptions(
      screen.getByLabelText('Plate for x1c.gcode.3mf'),
      '3',
    );

    // Without this the override panel would describe plate 1 while the job ran
    // plate 3 — the plate selector that used to key it is no longer on screen.
    await waitFor(() => expect(reqUrls.some((u) => u.includes('plate_id=3'))).toBe(true));
  });
});
