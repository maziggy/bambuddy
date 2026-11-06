/**
 * Wiring tests for AssignSpoolModal: pin the exact request payload sent
 * when a spool is assigned to a slot (built-in and Spoolman inventory),
 * and that the material-mismatch confirm gates the request.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
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

const plaSpool = {
  id: 41,
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
};

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 7,
  amsId: 128,
  trayId: 0,
};

describe('AssignSpoolModal wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSpools.mockResolvedValue([plaSpool]);
    mocked.getAssignments.mockResolvedValue([]);
    mocked.getSettings.mockResolvedValue({});
    mocked.refreshPrinterStatus.mockResolvedValue({ status: 'ok' });
    mocked.getSpoolmanSlotAssignments.mockResolvedValue([]);
    mocked.assignSpool.mockResolvedValue({ id: 1, spool_id: 41, printer_id: 7, ams_id: 128, tray_id: 0 });
    mocked.assignSpoolmanSlot.mockResolvedValue({});
  });

  it('built-in: sends assignSpool with the picked spool id and the opened slot (AMS-HT 128)', async () => {
    const user = userEvent.setup();
    render(
      <AssignSpoolModal
        {...baseProps}
        trayInfo={{ type: 'PLA', material: 'PLA', profile: 'PLA', color: 'FF0000', location: 'AMS-HT' }}
      />,
    );
    await user.click(await screen.findByText(/Polymaker/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() => expect(mocked.assignSpool).toHaveBeenCalledTimes(1));
    expect(mocked.assignSpool).toHaveBeenCalledWith({
      spool_id: 41,
      printer_id: 7,
      ams_id: 128,
      tray_id: 0,
    });
    expect(mocked.assignSpoolmanSlot).not.toHaveBeenCalled();
  });

  it('built-in: external slot ids (254/255) are passed through unchanged', async () => {
    const user = userEvent.setup();
    render(
      <AssignSpoolModal
        {...baseProps}
        amsId={255}
        trayId={254}
        trayInfo={{ type: 'PLA', material: 'PLA', profile: 'PLA', color: 'FF0000', location: 'External' }}
      />,
    );
    await user.click(await screen.findByText(/Polymaker/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 41, printer_id: 7, ams_id: 255, tray_id: 254 }),
    );
  });

  it('material mismatch: confirm dialog appears and nothing is sent until "Assign Anyway"', async () => {
    const user = userEvent.setup();
    render(
      <AssignSpoolModal
        {...baseProps}
        amsId={1}
        trayId={2}
        trayInfo={{ type: 'PETG', material: 'PETG', profile: 'PETG HF', color: 'FF0000', location: 'AMS 2 - Slot 3' }}
      />,
    );
    // PLA spool is filtered out for a PETG tray; reveal it with the toggle.
    await screen.findByText(/No spools available|show all spools/i);
    await user.click(screen.getByLabelText(/show all spools/i));
    await user.click(await screen.findByText(/Polymaker/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    expect(await screen.findByText('Material mismatch')).toBeInTheDocument();
    expect(mocked.assignSpool).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Assign Anyway' }));
    await waitFor(() =>
      expect(mocked.assignSpool).toHaveBeenCalledWith({ spool_id: 41, printer_id: 7, ams_id: 1, tray_id: 2 }),
    );
    expect(mocked.assignSpool).toHaveBeenCalledTimes(1);
  });

  it('material mismatch: cancelling the confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(
      <AssignSpoolModal
        {...baseProps}
        amsId={1}
        trayId={2}
        trayInfo={{ type: 'PETG', material: 'PETG', profile: 'PETG HF', color: 'FF0000', location: 'AMS 2 - Slot 3' }}
      />,
    );
    await screen.findByText(/No spools available|show all spools/i);
    await user.click(screen.getByLabelText(/show all spools/i));
    await user.click(await screen.findByText(/Polymaker/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await screen.findByText('Material mismatch');
    // The confirm dialog renders after the modal, so its Cancel is the last one.
    const cancels = screen.getAllByRole('button', { name: 'Cancel' });
    await user.click(cancels[cancels.length - 1]);

    await waitFor(() => expect(screen.queryByText('Material mismatch')).not.toBeInTheDocument());
    expect(mocked.assignSpool).not.toHaveBeenCalled();
  });

  it('Spoolman: sends assignSpoolmanSlot with spoolman_spool_id and the opened slot', async () => {
    const user = userEvent.setup();
    mocked.getSpools.mockResolvedValue([]);
    mocked.getSpoolmanInventorySpools.mockResolvedValue([
      { ...plaSpool, id: 900, brand: 'Spoolman Brand', archived_at: null },
    ]);
    render(
      <AssignSpoolModal
        {...baseProps}
        amsId={2}
        trayId={3}
        spoolmanEnabled
        trayInfo={{ type: 'PLA', material: 'PLA', profile: 'PLA', color: 'FF0000', location: 'AMS 3 - Slot 4' }}
      />,
    );
    await user.click(await screen.findByText(/Spoolman Brand/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() =>
      expect(mocked.assignSpoolmanSlot).toHaveBeenCalledWith({
        spoolman_spool_id: 900,
        printer_id: 7,
        ams_id: 2,
        tray_id: 3,
      }),
    );
    expect(mocked.assignSpool).not.toHaveBeenCalled();
  });
});
