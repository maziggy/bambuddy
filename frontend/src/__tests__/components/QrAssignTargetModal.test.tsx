/**
 * Tests for the scan-to-location dialog (#1574): picking a target, and what a
 * scan does. jsdom has no camera, so scans come either from the photo fallback
 * or from a stubbed live camera, with the platform decoder stubbed to read a
 * given payload.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, fireEvent, frames, render, stubLiveCamera, waitFor } from '../utils';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { QrAssignTargetModal } from '../../components/QrAssignTargetModal';

function noop() {}

/** A1 holds PLA, so a spool of another material makes the dialog ask. */
function slotA1HoldsPla() {
  server.use(
    http.get('/api/v1/printers/1/status', () =>
      HttpResponse.json({
        ams: [
          {
            id: 0,
            is_ams_ht: false,
            tray: [
              { id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: 'FF0000FF', remain: 50, state: 11, exists: true },
              { id: 1 },
              { id: 2 },
              { id: 3 },
            ],
          },
        ],
        vt_tray: [],
      }),
    ),
  );
}

function inventorySpool(id: number, extra: Record<string, unknown> = {}) {
  return { id, material: 'PLA', subtype: 'Basic', brand: 'Polymaker', archived_at: null, slicer_filament_name: null, ...extra };
}

/**
 * jsdom has no camera, so a scan goes through the photo fallback: the platform
 * decoder is stubbed to read `rawValue` off whatever photo is picked.
 */
function stubPhotoDecoder(rawValue: string) {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
  vi.stubGlobal(
    'BarcodeDetector',
    class {
      detect = vi.fn().mockResolvedValue([{ rawValue }]);
    },
  );
}

function pickPhoto() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['qr'], 'qr.jpg', { type: 'image/jpeg' })] } });
}

async function openScanStepForSlotA1() {
  fireEvent.click(await screen.findByRole('button', { name: /^A1\b/ }));
  fireEvent.click(screen.getByRole('button', { name: /set target & scan/i }));
  await screen.findByRole('button', { name: /take photo/i });
}

describe('QrAssignTargetModal', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X1C' }])),
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({
          ams: [{ id: 0, is_ams_ht: false, tray: [{ id: 0 }, { id: 1 }, { id: 2 }, { id: 3 }] }],
          vt_tray: [],
        }),
      ),
      http.get('/api/v1/inventory/locations', () =>
        HttpResponse.json([
          { id: 7, name: 'Shelf A', identifier: null, spool_count: 2, created_at: '', updated_at: '' },
        ]),
      ),
    );
  });

  it('gates the scan button until an AMS slot is chosen, then opens the photo step', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    // Slots render from the printer status (formatSlotLabel(0,0,..) => "A1"),
    // each with what is in it after the label.
    const slotA1 = await screen.findByRole('button', { name: /^A1\b/ });

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    fireEvent.click(slotA1);
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);

    // Photo step: header switches, the Take-photo action and target chip show.
    expect(await screen.findByText('Scan a spool QR')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /take photo/i })).toBeInTheDocument();
    expect(screen.getByText(/Target: X1C · A1/)).toBeInTheDocument();
  });

  it('storage tab: scan button enables only after a structured location is picked', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    fireEvent.click(screen.getByRole('button', { name: /storage/i }));

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    // Options come from the Locations catalog, not free text.
    const select = await screen.findByRole('combobox');
    expect(await screen.findByRole('option', { name: 'Shelf A' })).toBeInTheDocument();

    fireEvent.change(select, { target: { value: '7' } });
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);
    expect(await screen.findByText(/Target: Shelf A/)).toBeInTheDocument();
  });

  it('storage tab: creating a location picks it immediately, without waiting for a refetch', async () => {
    server.use(
      http.post('/api/v1/inventory/locations', () =>
        HttpResponse.json({ id: 9, name: 'Bin 7', identifier: null, spool_count: 0, created_at: '', updated_at: '' }),
      ),
    );
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    fireEvent.click(screen.getByRole('button', { name: /storage/i }));
    await screen.findByRole('option', { name: 'Shelf A' });

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Shelf A, Drawer 1/i), { target: { value: 'Bin 7' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    // The created location is selected straight away and resolves by id.
    await waitFor(() => expect(startBtn).toBeEnabled());
    fireEvent.click(startBtn);
    expect(await screen.findByText(/Target: Bin 7/)).toBeInTheDocument();
  });

  it("slot tiles say what is in each slot by the printer card's rules, never Empty for a present spool", async () => {
    server.use(
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({
          ams: [
            {
              id: 0,
              is_ams_ht: false,
              tray: [
                // loaded, with what is left on it
                { id: 0, tray_type: 'PLA', tray_color: 'FF0000FF', remain: 80, state: 11, exists: true },
                // the firmware says no spool is there
                { id: 1, tray_type: '', tray_color: null, remain: -1, state: 11, exists: false },
                // a spool without RFID: present, no filament data — "?", not Empty
                { id: 2, tray_type: '', tray_color: null, remain: -1, state: 11, exists: true },
                // no presence data: state 9/10 reads empty...
                { id: 3, tray_type: '', tray_color: null, remain: -1, state: 10, exists: null },
              ],
            },
          ],
          vt_tray: [
            // ...anything else reads unknown, on an external slot too
            { id: 254, tray_type: '', tray_color: null, remain: -1, state: 3, exists: null },
            { id: 255, tray_type: '', tray_color: null, remain: -1, state: 11, exists: true },
          ],
        }),
      ),
    );
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    expect(await screen.findByRole('button', { name: 'A1 PLA 80%' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A2 Empty' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A3 ?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A4 Empty' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ext-L ?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ext-R ?' })).toBeInTheDocument();
  });

  describe('assigning a scanned spool to a slot', () => {
    let unassigned: string[];

    beforeEach(() => {
      unassigned = [];
      server.use(
        http.delete('/api/v1/inventory/assignments/:printerId/:amsId/:trayId', ({ request }) => {
          unassigned.push(request.url);
          return HttpResponse.json({ status: 'deleted' });
        }),
        http.delete('/api/v1/spoolman/inventory/slot-assignments/:spoolId', ({ request }) => {
          unassigned.push(request.url);
          return HttpResponse.json({ id: 0 });
        }),
        // The built-in inventory looks a scanned spool up before an AMS assign.
        http.get('/api/v1/inventory/spools/:id', ({ params }) => HttpResponse.json(inventorySpool(Number(params.id)))),
      );
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it('built-in: one request moves the spool, with no unassign before or after (#1574)', async () => {
      let body: unknown = null;
      server.use(
        http.post('/api/v1/inventory/assignments', async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({ id: 1, spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0 });
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(body).toEqual({ spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0, move: true });
      expect(unassigned).toEqual([]);
    });

    it('Spoolman: one request moves the spool, with no unassign before it (#1574)', async () => {
      let body: unknown = null;
      server.use(
        http.post('/api/v1/spoolman/inventory/slot-assignments', async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({ id: 42 });
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode />);

      await openScanStepForSlotA1();
      pickPhoto();

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(body).toEqual({ spoolman_spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0, move: true });
      expect(unassigned).toEqual([]);
    });

    it("built-in: a Spoolman label is turned down, not read as one of the inventory's spools", async () => {
      let requested = false;
      server.use(
        http.post('/api/v1/inventory/assignments', () => {
          requested = true;
          return HttpResponse.json({});
        }),
      );
      stubPhotoDecoder('web+spoolman:s-5');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      expect(await screen.findByText(/That is a Spoolman label/)).toBeInTheDocument();
      expect(requested).toBe(false);
      expect(onClose).not.toHaveBeenCalled();
    });

    it("Spoolman: Spoolman's own label assigns that Spoolman spool", async () => {
      let body: unknown = null;
      server.use(
        http.post('/api/v1/spoolman/inventory/slot-assignments', async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({ id: 5 });
        }),
      );
      stubPhotoDecoder('https://spoolman.local/spool/show/5');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode />);

      await openScanStepForSlotA1();
      pickPhoto();

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(body).toEqual({ spoolman_spool_id: 5, printer_id: 1, ams_id: 0, tray_id: 0, move: true });
    });

    it('a failed move keeps the scan step open', async () => {
      let posts = 0;
      server.use(
        http.post('/api/v1/inventory/assignments', () => {
          posts += 1;
          return HttpResponse.json({ detail: 'Printer not found' }, { status: 404 });
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      await waitFor(() => expect(posts).toBe(1));
      // The take-photo button is disabled while the request runs, so it being
      // enabled again means the failed request has settled.
      await waitFor(() => expect(screen.getByRole('button', { name: /take photo/i })).toBeEnabled());
      expect(onClose).not.toHaveBeenCalled();
      expect(screen.getByText('Scan a spool QR')).toBeInTheDocument();
      // Why, in the dialog: a toast would sit under its backdrop, and the kiosk shows none.
      const why = screen.getByText('Could not assign to X1C · A1: Printer not found');
      expect(screen.getByTestId('toast-viewport')).not.toContainElement(why);
    });

    it('built-in: closing the dialog while the spool is looked up assigns nothing (#1606 review)', async () => {
      let lookedUp = false;
      let answer: () => void = () => {};
      let posts = 0;
      server.use(
        http.get('/api/v1/inventory/spools/:id', async () => {
          lookedUp = true;
          await new Promise<void>((resolve) => { answer = resolve; });
          return HttpResponse.json(inventorySpool(42));
        }),
        http.post('/api/v1/inventory/assignments', () => {
          posts += 1;
          return HttpResponse.json({ id: 1, spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0 });
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      // Both pages that open the dialog unmount it when it closes.
      const { unmount } = render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();
      await waitFor(() => expect(lookedUp).toBe(true));
      unmount();
      answer();
      await new Promise((r) => setTimeout(r, 100));

      expect(posts).toBe(0);
    });

    it('a refused assign is not sent again while its label stays in front of the camera (#1606 review)', async () => {
      let posts = 0;
      server.use(
        http.post('/api/v1/spoolman/inventory/slot-assignments', () => {
          posts += 1;
          return HttpResponse.json({ detail: 'Cannot assign an archived spool' }, { status: 400 });
        }),
      );
      stubLiveCamera('https://bambuddy.local/inventory?spool=42');
      render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode />);

      await openScanStepForSlotA1();
      await waitFor(() => expect(posts).toBe(1), { timeout: 3000 });
      await frames(4);

      expect(posts).toBe(1);
      // Asked for, it goes again.
      fireEvent.click(await screen.findByRole('button', { name: /retry/i }));
      await waitFor(() => expect(posts).toBe(2), { timeout: 3000 });
    });

    it('built-in: a spool of another material asks first, and Assign Anyway moves it', async () => {
      slotA1HoldsPla();
      let body: unknown = null;
      server.use(
        http.get('/api/v1/inventory/spools/:id', () => HttpResponse.json(inventorySpool(42, { material: 'PETG', subtype: 'HF' }))),
        http.post('/api/v1/inventory/assignments', async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({ id: 1, spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0 });
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      expect(await screen.findByText('Material mismatch')).toBeInTheDocument();
      expect(screen.getByText(/"PETG" does not match the tray material "PLA"/)).toBeInTheDocument();
      expect(body).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Assign Anyway' }));

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(body).toEqual({ spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0, move: true });
    });

    it('built-in: Cancel on the material question sticks while the label stays in view (#1606 review)', async () => {
      slotA1HoldsPla();
      let posts = 0;
      server.use(
        http.get('/api/v1/inventory/spools/:id', () => HttpResponse.json(inventorySpool(42, { material: 'PETG' }))),
        http.post('/api/v1/inventory/assignments', () => {
          posts += 1;
          return HttpResponse.json({});
        }),
      );
      stubLiveCamera('https://bambuddy.local/inventory?spool=42');
      render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      await screen.findByText('Material mismatch', {}, { timeout: 3000 });
      // The question's own Cancel comes after the dialog's in the page.
      const cancels = screen.getAllByRole('button', { name: 'Cancel' });
      fireEvent.click(cancels[cancels.length - 1]);
      await frames(4);

      expect(screen.queryByText('Material mismatch')).not.toBeInTheDocument();
      expect(posts).toBe(0);
    });

    it('built-in: with filament warnings off, a spool of another material is assigned without asking', async () => {
      slotA1HoldsPla();
      server.use(
        http.get('/api/v1/settings/', () => HttpResponse.json({ disable_filament_warnings: true })),
        http.get('/api/v1/inventory/spools/:id', () => HttpResponse.json(inventorySpool(42, { material: 'PETG' }))),
        http.post('/api/v1/inventory/assignments', () =>
          HttpResponse.json({ id: 1, spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0 }),
        ),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      const onClose = vi.fn();
      render(<QrAssignTargetModal isOpen onClose={onClose} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(screen.queryByText('Material mismatch')).not.toBeInTheDocument();
    });

    it('built-in: a slot that will configure when the spool goes in says so (#1680)', async () => {
      server.use(
        http.post('/api/v1/inventory/assignments', () =>
          HttpResponse.json({ id: 1, spool_id: 42, printer_id: 1, ams_id: 0, tray_id: 0, pending_config: true }),
        ),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      expect(await screen.findByText('Assigned. Slot will configure when you insert the spool.')).toBeInTheDocument();
    });

    it.each([
      ['archived', () => HttpResponse.json(inventorySpool(42, { archived_at: '2026-01-01T00:00:00Z' })), /Spool #42 is archived/],
      ['missing', () => HttpResponse.json({ detail: 'Spool not found' }, { status: 404 }), /There is no spool #42/],
    ])('built-in: a %s spool says so, and nothing is assigned', async (_kind, reply, message) => {
      let posts = 0;
      server.use(
        http.get('/api/v1/inventory/spools/:id', reply),
        http.post('/api/v1/inventory/assignments', () => {
          posts += 1;
          return HttpResponse.json({});
        }),
      );
      stubPhotoDecoder('https://bambuddy.local/inventory?spool=42');
      render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

      await openScanStepForSlotA1();
      pickPhoto();

      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(posts).toBe(0);
    });
  });
});
