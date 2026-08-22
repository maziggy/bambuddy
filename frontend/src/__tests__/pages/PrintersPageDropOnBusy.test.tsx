/**
 * Dropping a file onto a busy or offline printer queues it (#2849).
 *
 * The card used to refuse the drop unless the printer was connected and
 * neither RUNNING nor PAUSE, showing a red "Printer busy" and silently
 * discarding the file. That gate was never needed: a dropped file always
 * becomes a queue item — dropping onto an idle printer just dispatches it
 * straight away — so a busy printer only means the item waits its turn. The
 * workaround was to upload to Archives and queue it from there by hand, which
 * is the same thing with more steps.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 1,
  name: 'X1C',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

function makeStatus(over: Record<string, unknown>) {
  return {
    connected: true,
    state: 'IDLE',
    progress: 0,
    layer_num: 0,
    total_layers: 0,
    temperatures: { nozzle: 25, bed: 25, chamber: 25 },
    remaining_time: 0,
    filename: null,
    wifi_signal: -29,
    speed_level: 2,
    vt_tray: [],
    ams: [],
    ...over,
  };
}

/** Records every library upload the page attempts. */
const uploads: string[] = [];

function renderWith(statusOver: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(makeStatus(statusOver))),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/library/files', () => {
      // Parsing multipart bodies in MSW depends on the Node.js FormData
      // implementation and is unrelated to this regression. Recording the
      // matched request proves the page attempted the upload without making
      // the test sensitive to that runtime detail.
      uploads.push('request');
      return HttpResponse.json({ id: 7, filename: 'part.gcode', metadata: {} });
    }),
  );
  return render(<PrintersPage />);
}

async function card(): Promise<HTMLElement> {
  await waitFor(() => expect(document.getElementById('printer-card-1')).not.toBeNull());
  return document.getElementById('printer-card-1') as HTMLElement;
}

const gcode = () => new File(['G28\n'], 'part.gcode', { type: 'text/plain' });

describe('PrintersPage — drop onto a busy printer (#2849)', () => {
  beforeEach(() => {
    uploads.length = 0;
    vi.clearAllMocks();
  });

  it('offers to queue instead of refusing while a print is running', async () => {
    renderWith({ state: 'RUNNING' });
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to queue')).toBeInTheDocument();
    // The old refusal copy must be gone, not merely restyled.
    expect(screen.queryByText('Printer busy')).toBeNull();
    expect(screen.queryByText('Drop to print')).toBeNull();
  });

  it('offers to queue while a print is paused', async () => {
    renderWith({ state: 'PAUSE' });
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to queue')).toBeInTheDocument();
  });

  it('offers to queue for an offline printer so it can be scheduled', async () => {
    // The queue dispatches when the printer comes back, so refusing the drop
    // helped nobody.
    renderWith({ connected: false, state: 'IDLE' });
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to queue')).toBeInTheDocument();
  });

  it('still says "print" when the printer would start it immediately', async () => {
    renderWith({ state: 'IDLE' });
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to print')).toBeInTheDocument();
    expect(screen.queryByText('Drop to queue')).toBeNull();
  });

  it('says "queue" when an idle printer is drying, which also defers the start', async () => {
    renderWith({ state: 'IDLE', ams: [{ id: 0, dry_time: 240, tray: [] }] });
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to queue')).toBeInTheDocument();
  });

  it('actually uploads the dropped file while the printer is running', async () => {
    // The regression itself: handleCardDrop returned early, so nothing was
    // uploaded and the file vanished with no feedback at all.
    renderWith({ state: 'RUNNING' });
    const el = await card();

    fireEvent.dragEnter(el, { dataTransfer: { files: [gcode()] } });
    fireEvent.drop(el, { dataTransfer: { files: [gcode()] } });

    // Asserted by count, not name: the test environment's FormData does not
    // preserve the filename through the multipart round trip. That an upload
    // happened at all is the whole regression.
    await waitFor(() => expect(uploads).toHaveLength(1));
  });

  it('keeps the Print button available while a print is running', async () => {
    // The button is the other half of "Print from Printer Card" and was hidden
    // by the same condition. Leaving it hidden while the drop zone accepted the
    // same file would have had the two routes disagree on the same card.
    renderWith({ state: 'RUNNING' });
    await card();

    expect(await screen.findByTitle('Print')).toBeInTheDocument();
  });

  it('keeps the Print button available while the printer is offline', async () => {
    renderWith({ connected: false, state: 'IDLE' });
    await card();

    expect(await screen.findByTitle('Print')).toBeInTheDocument();
  });

  it('opens the Print Modal after a drop on a printer that is mid-print', async () => {
    // Reaching the modal from a busy printer is what this change newly allows,
    // so it is worth proving the modal actually renders rather than the drop
    // ending in a dead end.
    renderWith({ state: 'RUNNING' });
    const el = await card();
    fireEvent.drop(el, { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Print Job')).toBeInTheDocument();
    expect(await screen.findByText('part.gcode')).toBeInTheDocument();
  });

  it('opens the Print Modal after a drop on an offline printer', async () => {
    // Offline is the further reach: the queue holds the job until the printer
    // reconnects, so the modal has to cope with having no live AMS data.
    renderWith({ connected: false, state: 'IDLE' });
    const el = await card();
    fireEvent.drop(el, { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Print Job')).toBeInTheDocument();
    expect(await screen.findByText('part.gcode')).toBeInTheDocument();

    // And the job is actually submittable — an offline printer must not leave
    // the user in a modal whose Print button never enables. The backend puts no
    // connectivity condition on queue creation either.
    await waitFor(() => {
      const submit = document.querySelector('button[type="submit"]') as HTMLButtonElement | null;
      expect(submit).not.toBeNull();
      expect(submit!.disabled).toBe(false);
    });
  });

  it('rejects a file that is not printable, busy or not', async () => {
    renderWith({ state: 'RUNNING' });
    const el = await card();

    const stl = new File(['solid'], 'model.stl', { type: 'model/stl' });
    fireEvent.drop(el, { dataTransfer: { files: [stl] } });

    await waitFor(() =>
      expect(screen.getByText('Only .gcode and .gcode.3mf files can be printed')).toBeInTheDocument()
    );
    expect(uploads).toEqual([]);
  });
});
