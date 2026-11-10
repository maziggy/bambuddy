/**
 * PrintModal with several sliced files marked in the File Manager (#3462).
 *
 * The selection used to always become printer alternatives (#671): one job, the
 * first free printer of a matching model prints its file. Two files for the same
 * model could therefore not be queued at all ("variants must target different
 * printers"). The dialog now asks what the selection is: alternatives, or one
 * queue item per file in list order, with the ordinary printer picker and
 * options applying to every item.
 */

import type { ComponentProps } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintModal } from '../../components/PrintModal';
import type { VariantCandidate } from '../../components/PrintModal/VariantCandidates';
import { DEFAULT_PRINT_OPTIONS, DEFAULT_SCHEDULE_OPTIONS } from '../../components/PrintModal/types';

const PART_A: VariantCandidate = { id: 11, filename: 'part-a.gcode.3mf', sliced_for_model: 'A1' };
const PART_B: VariantCandidate = { id: 12, filename: 'part-b.gcode.3mf', sliced_for_model: 'A1' };
const PART_C: VariantCandidate = { id: 13, filename: 'part-c.gcode.3mf', sliced_for_model: 'P1S' };
// No sliced-for model, so the printer picker lists every printer.
const PART_D: VariantCandidate = { id: 14, filename: 'part-d.gcode.3mf', sliced_for_model: null };
const BY_ID: Record<number, VariantCandidate> = { 11: PART_A, 12: PART_B, 13: PART_C, 14: PART_D };

type Body = Record<string, unknown>;

let posted: Body[];
/** Answers each POST /queue/ by its position; the default accepts it. */
let respond: (body: Body, index: number) => Response | Promise<Response>;

function mockBackend(printers: Array<Record<string, unknown>> = [
  { id: 1, name: 'A1 Test', model: 'A1', ip_address: '192.0.2.1', is_active: true, enabled: true },
  { id: 2, name: 'P1S Test', model: 'P1S', ip_address: '192.0.2.2', is_active: true, enabled: true },
]) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] }),
    ),
    http.get('/api/v1/printers/available-filaments', () =>
      HttpResponse.json([
        { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null },
      ]),
    ),
    http.get('/api/v1/library/files/:id', ({ params }) => {
      const file = BY_ID[Number(params.id)];
      return HttpResponse.json({
        id: file.id,
        filename: file.filename,
        file_type: 'gcode.3mf',
        sliced_for_model: file.sliced_for_model,
      });
    }),
    http.get('/api/v1/library/files/:id/plates', ({ params }) =>
      HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: [], is_multi_plate: false }),
    ),
    http.get('/api/v1/library/files/:id/filament-requirements', () =>
      HttpResponse.json({
        filaments: [{ slot_id: 1, type: 'PLA', color: '#FFFFFF', used_grams: 15, used_meters: 5 }],
      }),
    ),
    http.post('/api/v1/queue/', async ({ request }) => {
      const body = (await request.json()) as Body;
      posted.push(body);
      return respond(body, posted.length - 1);
    }),
  );
}

function renderModal(
  files: VariantCandidate[],
  props: Pick<
    ComponentProps<typeof PrintModal>,
    'separateJobsAllowed' | 'onClose' | 'onSuccess' | 'resumeSeparateJobs' | 'onSeparateJobsProgress'
  > = {},
) {
  render(
    <PrintModal
      mode="create"
      libraryFileId={files[0].id}
      variantFiles={files}
      archiveName="parts"
      {...props}
      onClose={props.onClose ?? (() => {})}
    />,
  );
}

const printButton = () => screen.getByRole('button', { name: /^Print$/i });

/** Picks a printer card and waits until its status is in and Print is enabled. */
async function pickPrinterAndWait(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByText(name));
  await waitFor(() => expect(printButton()).toBeEnabled());
}

const fileIds = (bodies: Body[]) => bodies.map((b) => b.library_file_id);

const ALTERNATIVES_LIST_HELP = 'One job, one queue slot. The first matching printer to free up runs its file.';
const ALTERNATIVES_HINT = 'One job. The first matching printer to free up prints its file.';
const SEPARATE_HINT = /One queue item per file/;

describe('PrintModal with several selected files (#3462)', () => {
  beforeEach(() => {
    posted = [];
    respond = () => HttpResponse.json({ id: 1, status: 'pending' });
    mockBackend();
  });

  it('keeps a version group as alternatives, without the toggle', async () => {
    renderModal([PART_A, PART_C]);
    expect(await screen.findByText('part-c.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('Printer alternatives')).toBeInTheDocument();
    expect(screen.getByText(ALTERNATIVES_LIST_HELP)).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  });

  it('explains each mode once, under the toggle', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await screen.findByRole('radio', { name: 'Alternatives' });
    expect(screen.getAllByText(ALTERNATIVES_HINT)).toHaveLength(1);
    expect(screen.queryByText(ALTERNATIVES_LIST_HELP)).not.toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Separate jobs' }));
    expect(screen.getAllByText(SEPARATE_HINT)).toHaveLength(1);
    expect(screen.queryByText(ALTERNATIVES_HINT)).not.toBeInTheDocument();
  });

  it('starts on separate jobs for two files of one model and shows the printer picker', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    expect(await screen.findByRole('radio', { name: 'Separate jobs' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: 'Alternatives' })).toBeDisabled();
    expect(screen.getByText('Queue order')).toBeInTheDocument();
    // The reason Alternatives is blocked is the one explanation shown.
    expect(screen.getAllByText(/More than one file is sliced for A1\./)).toHaveLength(1);
    expect(screen.queryByText(SEPARATE_HINT)).not.toBeInTheDocument();

    // A selected printer would bring up the first file's tray mapping; it is
    // not shown, because each file is mapped against its own 3MF at dispatch.
    await pickPrinterAndWait(user, 'A1 Test');
    expect(screen.queryByText(/Filament Mapping/)).not.toBeInTheDocument();

    // Same for the override panel of "Any A1".
    await user.click(screen.getByRole('button', { name: /Any A1/ }));
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.queryByText(/Filament Override/)).not.toBeInTheDocument();
  });

  it('starts on alternatives for files of different models, without a printer picker', async () => {
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    expect(await screen.findByRole('radio', { name: 'Alternatives' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Printer alternatives')).toBeInTheDocument();
    expect(screen.queryByText('A1 Test')).not.toBeInTheDocument();
    expect(screen.queryByText('Specific Printer')).not.toBeInTheDocument();
  });

  it('queues one item per file in list order on the chosen printer', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(2));
    expect(fileIds(posted)).toEqual([11, 12]);
    for (const body of posted) {
      expect(body.printer_id).toBe(1);
      expect(body).not.toHaveProperty('variants');
      // Each file's own 3MF decides its trays at dispatch.
      expect(body.ams_mapping).toBeUndefined();
      expect(body.filament_overrides).toBeUndefined();
      // Nothing that belongs to the first file alone, nor a batch tying the
      // files together: the server prices and batches each file on its own.
      expect(body).not.toHaveProperty('estimated_cost');
      expect(body).not.toHaveProperty('skip_filament_check');
      expect(body).not.toHaveProperty('nozzle_rack_choice');
      expect(body).not.toHaveProperty('batch_id');
    }
  });

  it('counts the items of every listed file in the quantity hint', async () => {
    // Each file is posted with the quantity, so 2 files x 2 make 4 items.
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    await screen.findByRole('radio', { name: 'Separate jobs' });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '2' } });

    expect(await screen.findByText('Creates 4 queue items')).toBeInTheDocument();
  });

  it('warns per printer model about the files sliced for another model', async () => {
    // Every selected printer gets every file, so each printer's model is
    // checked against all of them. A warning, not a block, as for one file.
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await pickPrinterAndWait(user, 'A1 Test');
    expect(screen.getByText('Not sliced for A1: part-c.gcode.3mf')).toBeInTheDocument();
    expect(screen.queryByText(/Not sliced for P1S/)).not.toBeInTheDocument();

    await user.click(screen.getByText('P1S Test'));
    expect(await screen.findByText('Not sliced for P1S: part-a.gcode.3mf')).toBeInTheDocument();
    expect(screen.getByText('Not sliced for A1: part-c.gcode.3mf')).toBeInTheDocument();
    expect(printButton()).toBeEnabled();
  });

  describe('while the printer status is still loading', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/printers/:id/status', async () => {
          await delay('infinite');
          return HttpResponse.json({});
        }),
      );
    });

    it('lets separate jobs be queued, which send no AMS mapping to wait for', async () => {
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], { separateJobsAllowed: true });

      await user.click(await screen.findByText('A1 Test'));

      await waitFor(() => expect(printButton()).toBeEnabled());
      expect(screen.queryByText(/Waiting for AMS status/)).not.toBeInTheDocument();
    });

    it('still holds a single file until the AMS status is in (#2589)', async () => {
      const user = userEvent.setup();
      renderModal([PART_A]);

      await user.click(await screen.findByText('A1 Test'));

      expect(await screen.findByText('Waiting for AMS status from A1 Test…')).toBeInTheDocument();
      expect(printButton()).toBeDisabled();
    });
  });

  it('keeps the mode while the files are being queued', async () => {
    // A switch mid-run could leave the rest of a partly queued run as
    // alternatives, with the toggle already gone.
    respond = () => new Promise<Response>(() => {});
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(screen.getByRole('radio', { name: 'Alternatives' })).toBeDisabled();
    expect(screen.getByRole('radio', { name: 'Separate jobs' })).toBeDisabled();
  });

  it('treats a file queued on one of two printers as partly queued', async () => {
    // part-a goes onto the A1 and is refused by the P1S: one item is in the
    // queue, although no file went through on every printer.
    respond = (_body, index) =>
      index === 0
        ? HttpResponse.json({ id: 1, status: 'pending' })
        : HttpResponse.json({ detail: 'refused' }, { status: 400 });
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await user.click(await screen.findByText('A1 Test'));
    await pickPrinterAndWait(user, 'P1S Test');
    await user.click(printButton());

    const message = await screen.findByText(/Not queued: part-a\.gcode\.3mf \(refused\)/);
    expect(posted).toHaveLength(2);
    expect(message).toHaveTextContent('Queued before it: part-a.gcode.3mf.');
    expect(message).not.toHaveTextContent('None');
    await waitFor(() =>
      expect(screen.queryByRole('radiogroup', { name: 'What the selected files become' })).not.toBeInTheDocument(),
    );

    // A second Print sends part-a only to the printer that refused it: the A1
    // already has it, and queuing it there again would print it twice.
    respond = () => HttpResponse.json({ id: 2, status: 'pending' });
    await user.click(printButton());
    await waitFor(() => expect(posted).toHaveLength(5));
    expect(posted.slice(2).map((b) => [b.library_file_id, b.printer_id])).toEqual([
      [11, 2],
      [13, 1],
      [13, 2],
    ]);
  });

  it('follows the reordered list', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(screen.getAllByRole('button', { name: 'Move up' })[1]);
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(2));
    expect(fileIds(posted)).toEqual([12, 11]);
  });

  describe('with a multi-plate file first', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/library/files/:id/plates', ({ params }) =>
          Number(params.id) === 11
            ? HttpResponse.json({
                file_id: 11,
                filename: 'part-a.gcode.3mf',
                is_multi_plate: true,
                plates: [1, 2].map((index) => ({
                  index,
                  name: `Plate ${index}`,
                  objects: ['part'],
                  filaments: [{ slot_id: 1, type: 'PLA', color: '#FFFFFF' }],
                  has_thumbnail: false,
                  thumbnail_url: null,
                  bed_type: null,
                  print_time_seconds: 600,
                })),
              })
            : HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: [], is_multi_plate: false }),
        ),
      );
    });

    it('queues the plate chosen in the list', async () => {
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], { separateJobsAllowed: true });

      await user.selectOptions(await screen.findByLabelText('Plate for part-a.gcode.3mf'), '2');
      await pickPrinterAndWait(user, 'A1 Test');
      // The first file's plate summary belongs to a single-file print.
      expect(screen.queryByTestId('selected-plates-summary')).not.toBeInTheDocument();
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(2));
      expect(posted[0]).toMatchObject({ library_file_id: 11, plate_id: 2 });
      expect(posted[1]).toMatchObject({ library_file_id: 12, plate_id: null });
    });

    it('keeps the global quantity and sends it with every item', async () => {
      // The first file's per-plate steppers would own the number (#342) and
      // hide this field, but nothing would send them: the same trap as #3101.
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], { separateJobsAllowed: true });

      await screen.findByLabelText('Plate for part-a.gcode.3mf');
      await pickPrinterAndWait(user, 'A1 Test');
      fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '3' } });
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(2));
      expect(posted.map((b) => b.quantity)).toEqual([3, 3]);
    });
  });

  it('blocks "any A1" while a listed file cannot run on an A1', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await user.click(screen.getByRole('button', { name: /Any Model/ }));
    await user.selectOptions(screen.getByDisplayValue('Select a model...'), 'A1');

    expect(await screen.findByText('Cannot be dispatched to A1 printers: part-c.gcode.3mf')).toBeInTheDocument();
    expect(printButton()).toBeDisabled();
  });

  it('switching to separate jobs and back queues the alternatives job unchanged', async () => {
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(screen.getByRole('radio', { name: 'Alternatives' }));

    // The printer chosen for separate jobs is gone with its picker.
    expect(screen.queryByText('A1 Test')).not.toBeInTheDocument();
    await waitFor(() => expect(printButton()).toBeEnabled());
    expect(screen.queryByText(/Filament Mapping/)).not.toBeInTheDocument();
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(posted).toHaveLength(1);
    expect(posted[0].variants).toHaveLength(2);
    expect(posted[0]).not.toHaveProperty('printer_id');
    expect(posted[0]).not.toHaveProperty('target_model');
  });

  it('does not carry a location picked for separate jobs into the alternatives', async () => {
    // The alternatives job always sends target_location, and no control in
    // that mode shows it, so a leftover would narrow it out of sight.
    server.use(
      http.get('/api/v1/printers/', () =>
        HttpResponse.json([
          { id: 1, name: 'A1 Test', model: 'A1', location: 'Shop', ip_address: '192.0.2.1', is_active: true, enabled: true },
          { id: 2, name: 'P1S Test', model: 'P1S', ip_address: '192.0.2.2', is_active: true, enabled: true },
        ]),
      ),
    );
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await user.click(screen.getByRole('button', { name: /Any Model/ }));
    await user.selectOptions(screen.getByDisplayValue('Select a model...'), 'A1');
    await user.selectOptions(await screen.findByDisplayValue('Any location'), 'Shop');
    await user.click(screen.getByRole('radio', { name: 'Alternatives' }));

    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].variants).toHaveLength(2);
    expect(posted[0].target_location).toBeNull();
  });

  it('stops at a refused file, drops what was queued, and continues on resubmit', async () => {
    const onClose = vi.fn();
    respond = (_body, index) =>
      index === 1
        ? HttpResponse.json({ detail: 'refused' }, { status: 400 })
        : HttpResponse.json({ id: index + 1, status: 'pending' });
    const user = userEvent.setup();
    renderModal([PART_A, PART_B], { separateJobsAllowed: true, onClose });

    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(printButton());

    expect(await screen.findByText(/Not queued: part-b\.gcode\.3mf \(refused\)/)).toBeInTheDocument();
    expect(fileIds(posted)).toEqual([11, 12]);
    await waitFor(() => expect(screen.queryByText('part-a.gcode.3mf')).not.toBeInTheDocument());
    expect(screen.getByText('part-b.gcode.3mf')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // What is left is the rest of these separate jobs, not a new choice: with
    // one file left, "Alternatives" would post a job with a single candidate.
    expect(screen.queryByRole('radiogroup', { name: 'What the selected files become' })).not.toBeInTheDocument();
    // With the toggle gone, the list carries the explanation, once.
    expect(screen.getAllByText(SEPARATE_HINT)).toHaveLength(1);

    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(3));
    expect(fileIds(posted.slice(2))).toEqual([12]);
    expect(posted[2]).not.toHaveProperty('variants');
    expect(posted[2].printer_id).toBe(1);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('checks the model of the files still listed after the first one was queued', async () => {
    // The dialog's own file (the first one) is queued and gone; "Any P1S" for
    // the P1S file that is left must not be judged by the A1 file's model.
    respond = (_body, index) =>
      index === 0
        ? HttpResponse.json({ id: 1, status: 'pending' })
        : HttpResponse.json({ detail: 'refused' }, { status: 400 });
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await pickPrinterAndWait(user, 'P1S Test');
    await user.click(printButton());
    await screen.findByText(/Not queued: part-c\.gcode\.3mf/);
    await waitFor(() => expect(screen.queryByText('part-a.gcode.3mf')).not.toBeInTheDocument());

    respond = () => HttpResponse.json({ id: 9, status: 'pending' });
    await user.click(screen.getByRole('button', { name: /Any P1S/ }));
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(3));
    expect(posted[2]).toMatchObject({ library_file_id: 13, target_model: 'P1S', printer_id: null });
  });

  it('keeps Alternatives blocked for two files of one model, also on a click', async () => {
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    const alternatives = await screen.findByRole('radio', { name: 'Alternatives' });
    fireEvent.click(alternatives);

    expect(screen.getByRole('radio', { name: 'Separate jobs' })).toHaveAttribute('aria-checked', 'true');
    expect(alternatives).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('Queue order')).toBeInTheDocument();
    expect(screen.queryByText('Printer alternatives')).not.toBeInTheDocument();
  });

  it('queues the rest of an ASAP run after the items already inserted (#3471 review)', async () => {
    // part-a goes in at 1, part-b is refused. The second Print must put
    // part-b at 2, behind part-a, not ahead of it at 1.
    respond = (_body, index) =>
      index === 1
        ? HttpResponse.json({ detail: 'refused' }, { status: 400 })
        : HttpResponse.json({ id: index + 1, status: 'pending' });
    // The queue as the server then has it: part-a (item 1) on top.
    server.use(http.get('/api/v1/queue/', () => HttpResponse.json([{ id: 1, position: 1, batch_id: null }])));
    const user = userEvent.setup();
    renderModal([PART_A, PART_B], { separateJobsAllowed: true });

    await pickPrinterAndWait(user, 'A1 Test');
    await user.click(screen.getByRole('button', { name: /^ASAP$/i }));
    await user.click(printButton());
    await screen.findByText(/Not queued: part-b\.gcode\.3mf/);
    expect(posted[0]).toMatchObject({ library_file_id: 11, insert_at_top: true, insert_position: 1 });

    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());
    await waitFor(() => expect(posted).toHaveLength(3));
    expect(posted[2]).toMatchObject({ library_file_id: 12, insert_at_top: true, insert_position: 2 });
  });

  it('closes without a "0 items queued" message when nothing is left to send (#3471 review)', async () => {
    // Everything goes through except part-c on the P1S. With the P1S
    // deselected, part-c on the A1 is already queued: nothing to post.
    respond = (_body, index) =>
      index === 3
        ? HttpResponse.json({ detail: 'refused' }, { status: 400 })
        : HttpResponse.json({ id: index + 1, status: 'pending' });
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderModal([PART_A, PART_C], { separateJobsAllowed: true, onClose, onSuccess });

    await user.click(await screen.findByRole('radio', { name: 'Separate jobs' }));
    await user.click(await screen.findByText('A1 Test'));
    await pickPrinterAndWait(user, 'P1S Test');
    await user.click(printButton());
    await screen.findByText(/Not queued: part-c\.gcode\.3mf/);
    expect(posted).toHaveLength(4);

    await user.click(screen.getByText('P1S Test'));
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onSuccess).toHaveBeenCalled();
    expect(posted).toHaveLength(4);
    expect(screen.queryByText(/0 items queued/)).not.toBeInTheDocument();
  });

  describe('while a file\'s plate list is still loading (#3471 review)', () => {
    let releasePlates: () => void;
    beforeEach(() => {
      const platesLoaded = new Promise<void>((resolve) => {
        releasePlates = resolve;
      });
      server.use(
        http.get('/api/v1/library/files/:id/plates', async ({ params }) => {
          if (Number(params.id) !== 12) {
            return HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: [], is_multi_plate: false });
          }
          await platesLoaded;
          return HttpResponse.json({
            file_id: 12,
            filename: 'part-b.gcode.3mf',
            is_multi_plate: true,
            plates: [1, 2].map((index) => ({
              index,
              name: `Plate ${index}`,
              objects: ['part'],
              filaments: [{ slot_id: 1, type: 'PLA', color: '#FFFFFF' }],
              has_thumbnail: false,
              thumbnail_url: null,
              bed_type: null,
              print_time_seconds: 600,
            })),
          });
        }),
      );
    });

    it('holds Print for separate jobs until the plate is chosen', async () => {
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], { separateJobsAllowed: true });

      await user.click(await screen.findByText('A1 Test'));
      // Long enough for the printer status; only the plate list is missing.
      await delay(300);
      expect(printButton()).toBeDisabled();

      releasePlates();
      await screen.findByLabelText('Plate for part-b.gcode.3mf');
      await waitFor(() => expect(printButton()).toBeEnabled());
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(2));
      expect(posted[1]).toMatchObject({ library_file_id: 12, plate_id: 1 });
    });

    it('holds Print for alternatives the same way', async () => {
      const user = userEvent.setup();
      renderModal([PART_C, PART_B]);

      await screen.findByText('part-b.gcode.3mf');
      await delay(300);
      expect(printButton()).toBeDisabled();

      releasePlates();
      await screen.findByLabelText('Plate for part-b.gcode.3mf');
      await waitFor(() => expect(printButton()).toBeEnabled());
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0].variants).toEqual(
        expect.arrayContaining([expect.objectContaining({ library_file_id: 12, plate_id: 1 })]),
      );
    });
  });

  describe('carried over from an earlier dialog (#3471 review)', () => {
    it('reports what a refused run got into the queue', async () => {
      respond = (_body, index) =>
        index === 1
          ? HttpResponse.json({ detail: 'refused' }, { status: 400 })
          : HttpResponse.json({ id: index + 1, status: 'pending' });
      const onSeparateJobsProgress = vi.fn();
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], { separateJobsAllowed: true, onSeparateJobsProgress });

      await pickPrinterAndWait(user, 'A1 Test');
      await user.click(screen.getByRole('button', { name: /^ASAP$/i }));
      await user.click(printButton());
      await screen.findByText(/Not queued: part-b\.gcode\.3mf/);

      expect(onSeparateJobsProgress).toHaveBeenLastCalledWith({
        queuedPairs: ['11:1'],
        asapInserted: 1,
        queuedItemIds: [1],
        queuedBatchIds: [],
        queuedFileIds: [11],
        remainingFileIds: [12],
        // What the rest is queued with when the dialog is opened again.
        settings: expect.objectContaining({
          assignmentMode: 'printer',
          printerIds: [1],
          targetModel: null,
          quantity: 1,
          scheduleOptions: expect.objectContaining({ scheduleType: 'asap' }),
        }),
      });
    });

    it('skips the pairs and continues the ASAP positions of the earlier dialog', async () => {
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], {
        separateJobsAllowed: true,
        resumeSeparateJobs: { queuedPairs: ['11:1'], asapInserted: 1 },
      });

      // The rest of a partly queued run: no new choice of mode.
      await screen.findByText('part-b.gcode.3mf');
      expect(screen.queryByRole('radiogroup', { name: 'What the selected files become' })).not.toBeInTheDocument();

      await pickPrinterAndWait(user, 'A1 Test');
      await user.click(screen.getByRole('button', { name: /^ASAP$/i }));
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0]).toMatchObject({ library_file_id: 12, printer_id: 1, insert_position: 2 });
    });

    it("finishes the run with the earlier dialog's printers, plates and options", async () => {
      server.use(
        http.get('/api/v1/library/files/:id/plates', ({ params }) =>
          HttpResponse.json({
            file_id: Number(params.id),
            filename: 'x',
            is_multi_plate: true,
            plates: [
              { index: 1, name: 'One', objects: [], has_thumbnail: false, filaments: [] },
              { index: 3, name: 'Three', objects: [], has_thumbnail: false, filaments: [] },
            ],
          }),
        ),
      );
      const user = userEvent.setup();
      renderModal([PART_D], {
        separateJobsAllowed: true,
        resumeSeparateJobs: {
          queuedPairs: ['14:1'],
          asapInserted: 0,
          settings: {
            plateByFile: { 14: 3 },
            assignmentMode: 'printer',
            printerIds: [1, 2],
            targetModel: null,
            targetLocation: null,
            quantity: 1,
            printOptions: {
              ...DEFAULT_PRINT_OPTIONS,
              timelapse: true,
            },
            scheduleOptions: { ...DEFAULT_SCHEDULE_OPTIONS, scheduleType: 'queue', requireManualStart: true },
            costCenterId: null,
          },
        },
      });

      // Nothing picked by hand: the run's printers, plate and options are back.
      await screen.findByLabelText('Plate for part-d.gcode.3mf');
      await waitFor(() => expect(printButton()).toBeEnabled());
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0]).toMatchObject({
        library_file_id: 14,
        printer_id: 2,
        plate_id: 3,
        timelapse: true,
        manual_start: true,
      });
    });

    it('puts the rest behind where the run ends in the queue now', async () => {
      // The run's item 5 moved to position 3 since the refusal.
      server.use(
        http.get('/api/v1/queue/', () =>
          HttpResponse.json([
            { id: 9, position: 1, batch_id: null },
            { id: 8, position: 2, batch_id: null },
            { id: 5, position: 3, batch_id: null },
            { id: 7, position: 4, batch_id: null },
          ]),
        ),
      );
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], {
        separateJobsAllowed: true,
        resumeSeparateJobs: { queuedPairs: ['11:1'], asapInserted: 1, queuedItemIds: [5] },
      });

      await screen.findByText('part-b.gcode.3mf');
      await pickPrinterAndWait(user, 'A1 Test');
      await user.click(screen.getByRole('button', { name: /^ASAP$/i }));
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0]).toMatchObject({ library_file_id: 12, insert_position: 4 });
    });

    it('falls back to the positions taken when the queue cannot be read', async () => {
      server.use(http.get('/api/v1/queue/', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
      const user = userEvent.setup();
      renderModal([PART_A, PART_B], {
        separateJobsAllowed: true,
        resumeSeparateJobs: { queuedPairs: ['11:1'], asapInserted: 1, queuedItemIds: [5] },
      });

      await screen.findByText('part-b.gcode.3mf');
      await pickPrinterAndWait(user, 'A1 Test');
      await user.click(screen.getByRole('button', { name: /^ASAP$/i }));
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0]).toMatchObject({ library_file_id: 12, insert_position: 2 });
    });

    it('continues a single file that one printer already took', async () => {
      const user = userEvent.setup();
      renderModal([PART_D], {
        separateJobsAllowed: true,
        resumeSeparateJobs: { queuedPairs: ['14:1'], asapInserted: 0 },
      });

      await user.click(await screen.findByText('A1 Test'));
      await pickPrinterAndWait(user, 'P1S Test');
      await user.click(printButton());

      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted.map((b) => [b.library_file_id, b.printer_id])).toEqual([[14, 2]]);
    });
  });
});
