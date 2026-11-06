/**
 * Wiring tests for ConfigureAmsSlotModal's "Reset Slot" path: the request
 * must target exactly the opened slot (printer, AMS id, tray id), including
 * AMS-HT (128+) and external (255/254) ids, only after a confirm, and never
 * while a print is loaded.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ConfigureAmsSlotModal } from '../../components/ConfigureAmsSlotModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getCloudSettings: vi.fn(),
    getKProfiles: vi.fn(),
    configureAmsSlot: vi.fn(),
    getCloudSettingDetail: vi.fn(),
    saveSlotPreset: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getSlicerPrinterModels: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    getLocalPresets: vi.fn(),
    getBuiltinFilaments: vi.fn(),
    orcaCloudListProfiles: vi.fn(),
    orcaCloudGetProfile: vi.fn(),
    searchColors: vi.fn(),
    getColorCatalog: vi.fn(),
    resetAmsSlot: vi.fn(),
    getPrinterStatus: vi.fn().mockResolvedValue({}),
  },
}));

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const onClose = vi.fn();
const onSuccess = vi.fn();

function props(amsId: number, trayId: number, printerId = 9, printerState: string | null = 'IDLE') {
  return {
    isOpen: true,
    onClose,
    onSuccess,
    printerId,
    slotInfo: { amsId, trayId, trayType: 'PLA', trayColor: 'FFFFFF', traySubBrands: 'PLA Basic' },
    nozzleDiameter: '0.4',
    printerState,
  };
}

function confirmDialog(): HTMLElement {
  return screen.getByText('Reset slot?').closest('div.fixed') as HTMLElement;
}

describe('ConfigureAmsSlotModal reset wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mocked.getPrinterStatus.mockResolvedValue({});
    mocked.getSettings.mockResolvedValue({});
    mocked.getSlicerPrinterModels.mockResolvedValue({});
    mocked.getCloudSettings.mockResolvedValue({ filament: [] });
    mocked.getKProfiles.mockResolvedValue({ profiles: [] });
    mocked.getLocalPresets.mockResolvedValue({ filament: [] });
    mocked.orcaCloudListProfiles.mockResolvedValue({ filament: [], printer: [], process: [] });
    mocked.getBuiltinFilaments.mockResolvedValue([]);
    mocked.searchColors.mockResolvedValue([]);
    mocked.getColorCatalog.mockResolvedValue([]);
    mocked.resetAmsSlot.mockResolvedValue({ success: true, message: 'ok' });
  });

  it.each([
    ['regular AMS slot', 2, 3],
    ['AMS-HT unit', 128, 0],
    ['external spool holder', 255, 254],
  ])('%s: Reset Slot sends resetAmsSlot(printerId, amsId, trayId)', async (_label, amsId, trayId) => {
    const user = userEvent.setup();
    render(<ConfigureAmsSlotModal {...props(amsId, trayId)} />);

    await user.click(await screen.findByRole('button', { name: /Reset Slot/ }));
    expect(mocked.resetAmsSlot).not.toHaveBeenCalled();
    await user.click(within(confirmDialog()).getByRole('button', { name: 'Reset Slot' }));

    await waitFor(() => expect(mocked.resetAmsSlot).toHaveBeenCalledTimes(1));
    expect(mocked.resetAmsSlot).toHaveBeenCalledWith(9, amsId, trayId);
    expect(mocked.configureAmsSlot).not.toHaveBeenCalled();
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it('Cancel closes without resetting or configuring', async () => {
    const user = userEvent.setup();
    render(<ConfigureAmsSlotModal {...props(1, 1)} />);
    await screen.findByRole('button', { name: /Reset Slot/ });
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(mocked.resetAmsSlot).not.toHaveBeenCalled();
    expect(mocked.configureAmsSlot).not.toHaveBeenCalled();
  });

  it('cancelling the confirm sends nothing and keeps the slot dialog open', async () => {
    const user = userEvent.setup();
    render(<ConfigureAmsSlotModal {...props(2, 3)} />);

    await user.click(await screen.findByRole('button', { name: /Reset Slot/ }));
    await user.click(within(confirmDialog()).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByText('Reset slot?')).toBeNull();
    expect(mocked.resetAmsSlot).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Escape in the confirm closes only the confirm', async () => {
    const user = userEvent.setup();
    render(<ConfigureAmsSlotModal {...props(2, 3)} />);

    await user.click(await screen.findByRole('button', { name: /Reset Slot/ }));
    // One key press: it bubbles through document (the slot dialog) to window (the confirm).
    fireEvent.keyDown(document.body, { key: 'Escape' });

    expect(screen.queryByText('Reset slot?')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(mocked.resetAmsSlot).not.toHaveBeenCalled();
  });

  it.each(['RUNNING', 'PAUSE', 'PREPARE', 'SLICING'])(
    'Reset Slot is disabled while the printer is %s',
    async (state) => {
      const user = userEvent.setup();
      render(<ConfigureAmsSlotModal {...props(2, 3, 9, state)} />);

      const reset = await screen.findByRole('button', { name: /Reset Slot/ });
      expect(reset).toBeDisabled();
      await user.click(reset);

      expect(screen.queryByText('Reset slot?')).toBeNull();
      expect(mocked.resetAmsSlot).not.toHaveBeenCalled();
    },
  );

  it.each(['IDLE', 'FINISH', 'FAILED'])('Reset Slot is available while the printer is %s', async (state) => {
    render(<ConfigureAmsSlotModal {...props(2, 3, 9, state)} />);

    expect(await screen.findByRole('button', { name: /Reset Slot/ })).toBeEnabled();
  });
});
