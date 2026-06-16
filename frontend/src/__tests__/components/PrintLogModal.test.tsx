import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { PrintLogModal } from '../../components/PrintLogModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getArchiveRuns: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const sampleRuns = {
  total: 2,
  items: [
    {
      id: 99,
      archive_id: 42,
      print_name: 'Benchy',
      printer_name: 'X1C-01',
      printer_id: 3,
      status: 'failed',
      started_at: '2026-05-10T10:00:00Z',
      completed_at: '2026-05-10T10:05:00Z',
      duration_seconds: 300,
      filament_type: 'PLA',
      filament_color: '#FF0000',
      filament_used_grams: 10.0,
      cost: 0.25,
      energy_kwh: 0.05,
      energy_cost: 0.01,
      failure_reason: 'Cancelled by user',
      thumbnail_path: null,
      created_by_id: 1,
      created_by_username: 'admin',
      created_at: '2026-05-10T10:05:00Z',
    },
    {
      id: 50,
      archive_id: 42,
      print_name: 'Benchy',
      printer_name: 'X1C-01',
      printer_id: 3,
      status: 'completed',
      started_at: '2026-05-01T10:00:00Z',
      completed_at: '2026-05-01T11:00:00Z',
      duration_seconds: 3600,
      filament_type: 'PLA',
      filament_color: '#FF0000',
      filament_used_grams: 100.0,
      cost: 2.5,
      energy_kwh: 0.2,
      energy_cost: 0.05,
      failure_reason: null,
      thumbnail_path: null,
      created_by_id: 1,
      created_by_username: 'admin',
      created_at: '2026-05-01T11:00:00Z',
    },
  ],
};

describe('PrintLogModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getArchiveRuns).mockResolvedValue(sampleRuns);
  });

  it('renders the archive name in the modal title', async () => {
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/Benchy/)).toBeInTheDocument();
    });
  });

  it('falls back to "this archive" when name is null', async () => {
    render(<PrintLogModal archiveId={42} archiveName={null} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/this archive/i)).toBeInTheDocument();
    });
  });

  it('lists every run with its filament + cost', async () => {
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/100\.0 g/)).toBeInTheDocument();
      expect(screen.getByText(/10\.0 g/)).toBeInTheDocument();
    });
  });

  it('shows failure_reason under failed runs', async () => {
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Cancelled by user')).toBeInTheDocument();
    });
  });

  it('translates camelCase failure_reason keys (#1687 follow-up)', async () => {
    vi.mocked(api.getArchiveRuns).mockResolvedValue({
      total: 1,
      items: [
        {
          ...sampleRuns.items[0],
          failure_reason: 'filamentRunout',
        },
      ],
    });
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Filament runout')).toBeInTheDocument();
    });
    expect(screen.queryByText('filamentRunout')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no runs', async () => {
    vi.mocked(api.getArchiveRuns).mockResolvedValue({ total: 0, items: [] });
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/no print events/i)).toBeInTheDocument();
    });
  });

  it('calls onClose when Escape is pressed', async () => {
    const onClose = vi.fn();
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={onClose} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when backdrop is clicked', async () => {
    const onClose = vi.fn();
    const { container } = render(
      <PrintLogModal archiveId={42} archiveName="Benchy" onClose={onClose} />
    );
    const backdrop = container.querySelector('.fixed.inset-0');
    if (!backdrop) throw new Error('Backdrop not found');
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when clicking inside the modal body', async () => {
    const onClose = vi.fn();
    render(<PrintLogModal archiveId={42} archiveName="Benchy" onClose={onClose} />);
    await waitFor(() => {
      expect(screen.getByText(/Benchy/)).toBeInTheDocument();
    });
    // Click on the title text inside the modal
    fireEvent.click(screen.getByText(/Benchy/));
    expect(onClose).not.toHaveBeenCalled();
  });
});
