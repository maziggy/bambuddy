/**
 * A Spoolman spool's usage list (#3407): the prints Bambuddy charged to it,
 * read and cleared through the Spoolman routes, never a built-in spool's that
 * shares its id.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SpoolUsageHistory } from '../../components/SpoolUsageHistory';
import { api } from '../../api/client';
import type { SpoolUsageRecord } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSpoolUsageHistory: vi.fn(),
      clearSpoolUsageHistory: vi.fn(),
      getSpoolmanSpoolUsageHistory: vi.fn(),
      clearSpoolmanSpoolUsageHistory: vi.fn(),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const record = (printName: string): SpoolUsageRecord => ({
  id: 1,
  spool_id: null,
  spoolman_spool_id: 6,
  printer_id: 1,
  print_name: printName,
  weight_used: 42,
  percent_used: 4,
  status: 'completed',
  cost: 1.05,
  created_at: '2026-10-20T10:00:00Z',
});

describe('SpoolUsageHistory for a Spoolman spool (#3407)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSpoolmanSpoolUsageHistory.mockResolvedValue([record('Bracket')]);
    mocked.getSpoolUsageHistory.mockResolvedValue([record('Built-in spool 6')]);
    mocked.clearSpoolmanSpoolUsageHistory.mockResolvedValue({ status: 'cleared' });
  });

  it("lists the Spoolman spool's own prints", async () => {
    render(<SpoolUsageHistory spoolId={6} spoolman />);

    expect(await screen.findByText('Bracket')).toBeInTheDocument();
    expect(mocked.getSpoolmanSpoolUsageHistory).toHaveBeenCalledWith(6);
    expect(mocked.getSpoolUsageHistory).not.toHaveBeenCalled();
  });

  it('clears through the Spoolman route', async () => {
    render(<SpoolUsageHistory spoolId={6} spoolman />);
    fireEvent.click(await screen.findByRole('button', { name: /clear/i }));

    await waitFor(() => expect(mocked.clearSpoolmanSpoolUsageHistory).toHaveBeenCalledWith(6));
    expect(mocked.clearSpoolUsageHistory).not.toHaveBeenCalled();
  });

  it('a built-in spool reads its own', async () => {
    render(<SpoolUsageHistory spoolId={6} />);

    expect(await screen.findByText('Built-in spool 6')).toBeInTheDocument();
    expect(mocked.getSpoolmanSpoolUsageHistory).not.toHaveBeenCalled();
  });

  it("leaves out rows from before the spool existed, an earlier spool's under its id", async () => {
    mocked.getSpoolmanSpoolUsageHistory.mockResolvedValue([
      { ...record('Bracket'), id: 1, created_at: '2026-10-20T10:00:00Z' },
      { ...record('Old spool print'), id: 2, created_at: '2026-01-01T10:00:00Z' },
    ]);
    render(<SpoolUsageHistory spoolId={6} spoolman createdAt="2026-10-01T00:00:00Z" />);

    expect(await screen.findByText('Bracket')).toBeInTheDocument();
    expect(screen.queryByText('Old spool print')).toBeNull();
  });
});
