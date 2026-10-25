/**
 * Scanning a spool's QR from the shared Assign Spool dialog (#1574 review).
 *
 * Opened from a slot, the dialog already knows the target, so a scan assigns
 * straight to it: the slot tuple the dialog was opened with is the one sent,
 * the scanned spool moves there from any other slot, the dialog closes only
 * once the assign succeeded, and Back returns to the picker.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { frames, render, stubLiveCamera } from '../utils';
import { AssignSpoolModal } from '../../components/AssignSpoolModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn(),
    getAssignments: vi.fn(),
    assignSpool: vi.fn(),
    assignSpoolmanSlot: vi.fn(),
    getSpoolmanInventorySpools: vi.fn(),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    refreshPrinterStatus: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function spool(id: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    material: 'PLA',
    subtype: 'Basic',
    brand: 'Polymaker',
    color_name: 'Red',
    rgba: 'FF0000FF',
    label_weight: 1000,
    weight_used: 0,
    tag_uid: null,
    tray_uuid: null,
    slicer_filament_name: 'PLA',
    archived_at: null,
    ...extra,
  };
}

const baseProps = {
  isOpen: true,
  printerId: 7,
  amsId: 128,
  trayId: 0,
  trayInfo: { type: 'PLA', material: 'PLA', color: 'FF0000', location: 'AMS HT A' },
};

/** jsdom has no camera, so a scan goes through the photo fallback with a stubbed decoder. */
function scan(rawValue: string) {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
  vi.stubGlobal(
    'BarcodeDetector',
    class {
      detect = vi.fn().mockResolvedValue([{ rawValue }]);
    },
  );
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['qr'], 'qr.jpg', { type: 'image/jpeg' })] } });
}

async function openScanner() {
  await userEvent.click(await screen.findByRole('button', { name: /scan a spool qr/i }));
  await screen.findByRole('button', { name: /take photo/i });
}

describe('AssignSpoolModal QR scan', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSpools.mockResolvedValue([spool(3), spool(4, { material: 'PETG', subtype: 'HF' }), spool(5, { archived_at: '2026-01-01T00:00:00Z' })]);
    mocked.getAssignments.mockResolvedValue([]);
    mocked.getSettings.mockResolvedValue({});
    mocked.refreshPrinterStatus.mockResolvedValue({ status: 'ok' });
    mocked.getSpoolmanSlotAssignments.mockResolvedValue([]);
    mocked.getSpoolmanInventorySpools.mockResolvedValue([]);
    mocked.assignSpool.mockResolvedValue({ id: 1, spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0 });
    mocked.assignSpoolmanSlot.mockResolvedValue({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('Spoolman: a refused scan is not sent again while its label stays in front of the camera (#1606 review)', async () => {
    mocked.assignSpoolmanSlot.mockRejectedValue(new Error('Spoolman server is not reachable'));
    stubLiveCamera('web+spoolman:s-12');
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} spoolmanEnabled />);
    await openScanner();

    await waitFor(() => expect(mocked.assignSpoolmanSlot).toHaveBeenCalledTimes(1), { timeout: 3000 });
    await frames(4);

    expect(mocked.assignSpoolmanSlot).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    // Why, in the scanner: a toast would sit under the dialog, and the kiosk shows none.
    const why = screen.getByText('Failed to assign spool: Spoolman server is not reachable');
    expect(screen.getByTestId('toast-viewport')).not.toContainElement(why);
  });

  it('a scan waits for the spool list rather than turning down a spool it has not loaded yet (#1606 review)', async () => {
    let listed: (spools: unknown) => void = () => {};
    mocked.getSpools.mockImplementation(() => new Promise((resolve) => { listed = resolve; }));
    stubLiveCamera('https://bambuddy.local/inventory?spool=3');
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);
    await openScanner();

    await frames(2);
    expect(screen.queryByText(/There is no spool #3/)).not.toBeInTheDocument();
    listed([spool(3)]);

    await waitFor(
      () => expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0, move: true }),
      { timeout: 3000 },
    );
  });

  it('Cancel on the material question sticks while the label stays in front of the camera (#1606 review)', async () => {
    stubLiveCamera('https://bambuddy.local/inventory?spool=4'); // PETG, for a PLA slot
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);
    await openScanner();

    await screen.findByText('Material mismatch', {}, { timeout: 3000 });
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await frames(4);

    expect(screen.queryByText('Material mismatch')).not.toBeInTheDocument();
    expect(mocked.assignSpool).not.toHaveBeenCalled();
  });

  it('opens the scanner in the dialog, for the slot it was opened from', async () => {
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);
    await openScanner();

    expect(screen.getByRole('heading', { name: 'Scan a spool QR' })).toBeInTheDocument();
    // The slot stays in view; there is no target to pick.
    expect(screen.getByText('(AMS HT A)')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
  });

  it('assigns the scanned spool to that slot, moving it, and closes only once that succeeded', async () => {
    let finish: (value: unknown) => void = () => {};
    mocked.assignSpool.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} />);
    await openScanner();

    scan('https://bambuddy.local/inventory?spool=3');

    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0, move: true }),
    );
    expect(onClose).not.toHaveBeenCalled();
    finish({ id: 1, spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0 });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('takes a spool that is in another slot — the picker would not offer it, the scan moves it', async () => {
    mocked.getAssignments.mockResolvedValue([{ id: 9, spool_id: 3, printer_id: 2, ams_id: 0, tray_id: 1 }]);
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} />);
    await openScanner();

    scan('3'); // a bare id reads as this inventory's spool too

    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0, move: true }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('stays on the scanner when the assign fails, and the next scan is sent', async () => {
    mocked.assignSpool.mockRejectedValueOnce(new Error('Printer not found'));
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} />);
    await openScanner();

    scan('https://bambuddy.local/inventory?spool=3');
    await waitFor(() => expect(mocked.assignSpool).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole('button', { name: /take photo/i })).toBeEnabled());
    expect(onClose).not.toHaveBeenCalled();

    scan('https://bambuddy.local/inventory?spool=3');
    await waitFor(() => expect(mocked.assignSpool).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('Back returns to the picker without closing the dialog', async () => {
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} />);
    await openScanner();

    await userEvent.click(screen.getByRole('button', { name: /back/i }));

    expect(await screen.findByRole('heading', { name: 'Assign Spool' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(mocked.assignSpool).not.toHaveBeenCalled();
  });

  it.each([
    ['web+spoolman:s-3', /That is a Spoolman label/],
    ['https://example.com/', /That QR code is not a spool label/],
    ['https://bambuddy.local/inventory?spool=99', /There is no spool #99/],
    ['https://bambuddy.local/inventory?spool=5', /Spool #5 is archived/],
  ])('built-in: %s is turned down with a reason, and nothing is sent', async (payload, message) => {
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} />);
    await openScanner();

    scan(payload);

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(mocked.assignSpool).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('asks before assigning a scanned spool of another material, then moves it', async () => {
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);
    await openScanner();

    scan('https://bambuddy.local/inventory?spool=4');

    expect(await screen.findByText('Material mismatch')).toBeInTheDocument();
    expect(mocked.assignSpool).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Assign Anyway' }));
    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 4, printer_id: 7, ams_id: 128, tray_id: 0, move: true }),
    );
  });

  it('Spoolman: a Spoolman label assigns that Spoolman spool to the slot, moving it', async () => {
    const onClose = vi.fn();
    render(<AssignSpoolModal {...baseProps} onClose={onClose} spoolmanEnabled />);
    await openScanner();

    scan('web+spoolman:s-12');

    await waitFor(() =>
      expect(mocked.assignSpoolmanSlot).toHaveBeenCalledWith({
        spoolman_spool_id: 12,
        printer_id: 7,
        ams_id: 128,
        tray_id: 0,
        move: true,
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it.each([
    ['empty', 'physical' as const],
    ['holding a spool it cannot identify', 'reset' as const],
  ])('a slot reporting no material (%s) has nothing to mismatch: picked or scanned, no question', async (_kind, emptyKind) => {
    const user = userEvent.setup();
    const slot = { ...baseProps, trayInfo: { type: '', color: '', location: 'AMS HT A', emptyKind } };
    const { unmount } = render(<AssignSpoolModal {...slot} onClose={vi.fn()} />);

    await user.click(await screen.findByRole('button', { name: /Polymaker PETG HF/ }));
    await user.click(screen.getByRole('button', { name: /^assign spool$/i }));
    await waitFor(() => expect(mocked.assignSpool).toHaveBeenCalledTimes(1));
    unmount();

    render(<AssignSpoolModal {...slot} onClose={vi.fn()} />);
    await openScanner();
    scan('https://bambuddy.local/inventory?spool=4'); // PETG
    await waitFor(() => expect(mocked.assignSpool).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Material mismatch')).not.toBeInTheDocument();
  });

  it('a spool picked from the list is assigned as before, without move', async () => {
    const user = userEvent.setup();
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);

    await user.click(await screen.findByRole('button', { name: /Polymaker PLA Basic/ }));
    await user.click(screen.getByRole('button', { name: /^assign spool$/i }));

    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0 }),
    );
  });
});

describe('AssignSpoolModal slot context', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSpools.mockResolvedValue([spool(3)]);
    mocked.getAssignments.mockResolvedValue([]);
    mocked.getSettings.mockResolvedValue({});
    mocked.getSpoolmanSlotAssignments.mockResolvedValue([]);
    mocked.getSpoolmanInventorySpools.mockResolvedValue([]);
  });

  it('shows the spool assigned to the slot now', async () => {
    mocked.getAssignments.mockResolvedValue([{ id: 1, spool_id: 3, printer_id: 7, ams_id: 128, tray_id: 0 }]);
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} />);

    expect(await screen.findByText('#3 Polymaker PLA Basic')).toBeInTheDocument();
    expect(screen.getByText(/Assigned:/)).toBeInTheDocument();
  });

  it('Spoolman: shows the Spoolman spool assigned to the slot now', async () => {
    mocked.getSpoolmanSlotAssignments.mockResolvedValue([
      { printer_id: 7, printer_name: 'H2C', ams_id: 128, tray_id: 0, spoolman_spool_id: 12, ams_label: null },
    ]);
    mocked.getSpoolmanInventorySpools.mockResolvedValue([spool(12, { brand: 'Elegoo' })]);
    render(<AssignSpoolModal {...baseProps} onClose={vi.fn()} spoolmanEnabled />);

    expect(await screen.findByText('#12 Elegoo PLA Basic')).toBeInTheDocument();
  });

  it.each([
    ['physical' as const, 'Empty slot'],
    ['reset' as const, 'No filament assigned'],
    [undefined, 'No filament assigned'],
  ])('a slot reporting no filament (%s) reads "%s" — empty only when the firmware says so', async (emptyKind, label) => {
    render(
      <AssignSpoolModal
        {...baseProps}
        onClose={vi.fn()}
        trayInfo={{ type: '', color: '', location: 'AMS HT A', emptyKind }}
      />,
    );

    expect(await screen.findByText(label)).toBeInTheDocument();
  });
});
