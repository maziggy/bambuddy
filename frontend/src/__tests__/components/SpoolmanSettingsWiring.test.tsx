/**
 * Wiring tests for SpoolmanSettings: the auto-saved settings payload,
 * Connect, the per-printer / all-printers AMS sync, and the two weight-sync
 * actions (built-in and Spoolman) that overwrite stored weights and so must
 * only fire after confirmation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SpoolmanSettings } from '../../components/SpoolmanSettings';
import { api } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      getSettings: vi.fn().mockResolvedValue({}),
      updateSettings: vi.fn().mockResolvedValue({}),
      getSpoolmanSettings: vi.fn(),
      updateSpoolmanSettings: vi.fn(),
      getSpoolmanStatus: vi.fn(),
      connectSpoolman: vi.fn(),
      syncAllPrintersAms: vi.fn(),
      syncPrinterAms: vi.fn(),
      syncWeightsFromAms: vi.fn(),
      syncSpoolmanAmsWeights: vi.fn(),
      migrateSpoolmanTags: vi.fn(),
      getPrinters: vi.fn(),
      getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const syncResult = { success: true, synced_count: 1, skipped_count: 0, skipped: [], errors: [] };

function settings(enabled: boolean, url = '') {
  return {
    spoolman_enabled: enabled ? 'true' : 'false',
    spoolman_url: url,
    spoolman_sync_mode: 'auto',
    spoolman_disable_weight_sync: 'false',
    spoolman_report_partial_usage: 'true',
    auto_add_unknown_rfid: 'true',
  };
}

describe('SpoolmanSettings wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({});
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false });
    mocked.getSpoolmanSettings.mockResolvedValue(settings(false));
    mocked.updateSpoolmanSettings.mockResolvedValue(settings(false));
    mocked.getSpoolmanStatus.mockResolvedValue({ enabled: false, connected: false, url: null });
    mocked.getPrinters.mockResolvedValue([
      { id: 4, name: 'X1C Left' },
      { id: 8, name: 'H2D Right' },
    ]);
    mocked.connectSpoolman.mockResolvedValue({ success: true, message: 'ok' });
    mocked.syncAllPrintersAms.mockResolvedValue(syncResult);
    mocked.syncPrinterAms.mockResolvedValue(syncResult);
    mocked.syncWeightsFromAms.mockResolvedValue({ synced: 2, skipped: 0, printing: 0 });
    mocked.syncSpoolmanAmsWeights.mockResolvedValue({ synced: 2, skipped: 0, printing: 0 });
  });

  it('switching to Spoolman and entering a URL auto-saves the full settings payload', async () => {
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: /Spoolman/ }));
    await user.type(screen.getByPlaceholderText('http://192.168.1.100:7912'), 'http://sm:7912');
    await user.selectOptions(screen.getByDisplayValue(/auto/i), 'manual');

    await waitFor(
      () => {
        const calls = mocked.updateSpoolmanSettings.mock.calls;
        expect(calls.length).toBeGreaterThan(0);
        expect(calls[calls.length - 1][0]).toEqual({
          spoolman_enabled: 'true',
          spoolman_url: 'http://sm:7912',
          spoolman_sync_mode: 'manual',
          spoolman_disable_weight_sync: 'false',
          spoolman_report_partial_usage: 'true',
          auto_add_unknown_rfid: 'true',
        });
      },
      { timeout: 3000 },
    );
  });

  it('switching back to Built-in auto-saves spoolman_enabled=false', async () => {
    mocked.getSpoolmanSettings.mockResolvedValue(settings(true, 'http://sm:7912'));
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: /Built-in Inventory/ }));

    await waitFor(
      () =>
        expect(mocked.updateSpoolmanSettings).toHaveBeenCalledWith(
          expect.objectContaining({ spoolman_enabled: 'false', spoolman_url: 'http://sm:7912' }),
        ),
      { timeout: 3000 },
    );
  });

  it('Connect calls connectSpoolman', async () => {
    mocked.getSpoolmanSettings.mockResolvedValue(settings(true, 'http://sm:7912'));
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: /Connect/ }));
    await waitFor(() => expect(mocked.connectSpoolman).toHaveBeenCalledTimes(1));
    expect(mocked.updateSpoolmanSettings).not.toHaveBeenCalled();
  });

  it('Sync with "All Printers" selected calls syncAllPrintersAms, not the per-printer sync', async () => {
    mocked.getSpoolmanSettings.mockResolvedValue(settings(true, 'http://sm:7912'));
    mocked.getSpoolmanStatus.mockResolvedValue({ enabled: true, connected: true, url: 'http://sm:7912' });
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: /^Sync$/ }));
    await waitFor(() => expect(mocked.syncAllPrintersAms).toHaveBeenCalledTimes(1));
    expect(mocked.syncPrinterAms).not.toHaveBeenCalled();
  });

  it('Sync with one printer selected calls syncPrinterAms(thatPrinterId)', async () => {
    mocked.getSpoolmanSettings.mockResolvedValue(settings(true, 'http://sm:7912'));
    mocked.getSpoolmanStatus.mockResolvedValue({ enabled: true, connected: true, url: 'http://sm:7912' });
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await screen.findByRole('option', { name: 'H2D Right' });
    await user.selectOptions(screen.getByDisplayValue('All Printers'), '8');
    await user.click(screen.getByRole('button', { name: /^Sync$/ }));

    await waitFor(() => expect(mocked.syncPrinterAms).toHaveBeenCalledWith(8));
    expect(mocked.syncAllPrintersAms).not.toHaveBeenCalled();
  });

  it('built-in "Sync Weights from AMS" only calls syncWeightsFromAms after confirm', async () => {
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: 'Sync Weights from AMS' }));
    expect(await screen.findByText('Sync Spool Weights from AMS')).toBeInTheDocument();
    expect(mocked.syncWeightsFromAms).not.toHaveBeenCalled();

    const confirmButtons = screen.getAllByRole('button', { name: 'Sync Weights from AMS' });
    await user.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => expect(mocked.syncWeightsFromAms).toHaveBeenCalledTimes(1));
    expect(mocked.syncSpoolmanAmsWeights).not.toHaveBeenCalled();
  });

  it('built-in weight sync: cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<SpoolmanSettings />);
    await user.click(await screen.findByRole('button', { name: 'Sync Weights from AMS' }));
    await screen.findByText('Sync Spool Weights from AMS');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Sync Spool Weights from AMS')).not.toBeInTheDocument());
    expect(mocked.syncWeightsFromAms).not.toHaveBeenCalled();
  });

  it('Spoolman weight sync only calls syncSpoolmanAmsWeights after confirm; cancel sends nothing', async () => {
    mocked.getSpoolmanSettings.mockResolvedValue(settings(true, 'http://sm:7912'));
    mocked.getSpoolmanStatus.mockResolvedValue({ enabled: true, connected: true, url: 'http://sm:7912' });
    const user = userEvent.setup();
    render(<SpoolmanSettings />);

    await user.click(await screen.findByRole('button', { name: 'Sync Spoolman Weights from AMS' }));
    await screen.findByText('Sync Spoolman Spool Weights from AMS');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(mocked.syncSpoolmanAmsWeights).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Sync Spoolman Weights from AMS' }));
    await screen.findByText('Sync Spoolman Spool Weights from AMS');
    const confirmButtons = screen.getAllByRole('button', { name: 'Sync Spoolman Weights from AMS' });
    await user.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => expect(mocked.syncSpoolmanAmsWeights).toHaveBeenCalledTimes(1));
    expect(mocked.syncWeightsFromAms).not.toHaveBeenCalled();
  });
});
