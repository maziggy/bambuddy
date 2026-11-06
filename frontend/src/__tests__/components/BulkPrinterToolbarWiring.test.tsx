/**
 * BulkPrinterToolbar wiring: which action the toolbar reports, which state the
 * select-by-state dropdown reports, and the permission gates on the destructive
 * buttons (printers:control for stop/pause/resume/clear HMS,
 * printers:clear_plate for Clear Bed).
 *
 * The toolbar reads printer statuses straight from the query cache, so each
 * test seeds the cache it renders with.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Printer } from '../../api/client';

const permissions = { granted: [] as string[] };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permissions.granted.includes(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { BulkPrinterToolbar } from '../../components/BulkPrinterToolbar';

const printer = (id: number, location: string | null = null) =>
  ({ id, name: `P${id}`, model: 'X1C', location }) as unknown as Printer;

const printers = [printer(1, 'Lab'), printer(2, 'Lab'), printer(3, 'Shed')];

// 1 printing, 2 paused (awaiting plate clear), 3 idle.
function seededClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(['printerStatus', 1], { connected: true, state: 'RUNNING' });
  qc.setQueryData(['printerStatus', 2], { connected: true, state: 'PAUSE', awaiting_plate_clear: true });
  qc.setQueryData(['printerStatus', 3], { connected: true, state: 'IDLE' });
  return qc;
}

function renderToolbar(selected: number[] = [1, 2]) {
  const props = {
    selectedIds: new Set(selected),
    printers,
    onClose: vi.fn(),
    onSelectAll: vi.fn(),
    onSelectByLocation: vi.fn(),
    onSelectByState: vi.fn(),
    onAction: vi.fn(),
    actionPending: false,
  };
  render(
    <QueryClientProvider client={seededClient()}>
      <BulkPrinterToolbar {...props} />
    </QueryClientProvider>,
  );
  return props;
}

const btn = (name: string) => screen.getByRole('button', { name });

describe('BulkPrinterToolbar wiring', () => {
  beforeEach(() => {
    permissions.granted = ['printers:control', 'printers:clear_plate'];
    vi.clearAllMocks();
  });

  it.each([
    ['Stop', 'stop'],
    ['Pause', 'pause'],
    ['Resume', 'resume'],
    ['Clear Bed', 'clearPlate'],
  ])('clicking %s reports action %s', async (label, action) => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(btn(label));
    expect(props.onAction).toHaveBeenCalledTimes(1);
    expect(props.onAction).toHaveBeenCalledWith(action);
  });

  it('disables Stop, Pause and Resume without printers:control', async () => {
    permissions.granted = ['printers:clear_plate'];
    const user = userEvent.setup();
    const props = renderToolbar();
    for (const label of ['Stop', 'Pause', 'Resume', 'Clear Notifications']) {
      expect(btn(label)).toBeDisabled();
      expect(btn(label)).toHaveAttribute('title', 'You do not have permission to control printers');
      await user.click(btn(label));
    }
    // Clear Bed has its own permission and stays usable.
    expect(btn('Clear Bed')).toBeEnabled();
    expect(props.onAction).not.toHaveBeenCalled();
  });

  it('disables Clear Bed without printers:clear_plate even with control', async () => {
    permissions.granted = ['printers:control'];
    const user = userEvent.setup();
    const props = renderToolbar();
    expect(btn('Clear Bed')).toBeDisabled();
    await user.click(btn('Clear Bed'));
    expect(props.onAction).not.toHaveBeenCalled();
    expect(btn('Stop')).toBeEnabled();
  });

  it('disables actions none of the selected printers can take', () => {
    // Only the idle printer is selected: nothing to stop, pause, resume or clear.
    renderToolbar([3]);
    for (const label of ['Stop', 'Pause', 'Resume', 'Clear Bed']) {
      expect(btn(label)).toBeDisabled();
    }
  });

  it('disables every action while one is pending', () => {
    const props = {
      selectedIds: new Set([1, 2]),
      printers,
      onClose: vi.fn(),
      onSelectAll: vi.fn(),
      onSelectByLocation: vi.fn(),
      onSelectByState: vi.fn(),
      onAction: vi.fn(),
      actionPending: true,
    };
    render(
      <QueryClientProvider client={seededClient()}>
        <BulkPrinterToolbar {...props} />
      </QueryClientProvider>,
    );
    for (const label of ['Stop', 'Pause', 'Resume', 'Clear Bed']) {
      expect(btn(label)).toBeDisabled();
    }
  });

  it('select by state reports the picked state', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(btn('Select by State'));
    // Only states with printers in them are offered.
    expect(screen.queryByRole('button', { name: /Offline/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: /Paused/ }));
    expect(props.onSelectByState).toHaveBeenCalledTimes(1);
    expect(props.onSelectByState).toHaveBeenCalledWith('paused');
    // The dropdown closes after a pick.
    await waitFor(() => expect(screen.queryByRole('button', { name: /Paused/ })).toBeNull());
  });

  it('select by location reports the picked location', async () => {
    const user = userEvent.setup();
    const props = renderToolbar();
    await user.click(btn('Select by Location'));
    await user.click(screen.getByRole('button', { name: 'Shed' }));
    expect(props.onSelectByLocation).toHaveBeenCalledWith('Shed');
    expect(props.onSelectByState).not.toHaveBeenCalled();
  });
});
