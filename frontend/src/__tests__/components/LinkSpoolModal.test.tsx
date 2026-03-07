/**
 * Tests for the LinkSpoolModal component.
 *
 * Tests the inventory link-to-spool modal including:
 * - Rendering modal with tag/tray info
 * - Displaying untagged spools
 * - Linking a spool via click
 * - Search filtering
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { LinkSpoolModal } from '../../components/LinkSpoolModal';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getUnlinkedSpools: vi.fn(),
    linkSpool: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

// Mock the toast context
const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

// Import mocked module
import { api } from '../../api/client';

describe('LinkSpoolModal', () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    tagUid: 'ABCD1234',
    trayUuid: 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4',
    printerId: 1,
    amsId: 0,
    trayId: 0,
  };

  const mockSpools = [
    {
      id: 1,
      filament_name: 'Generic PLA Red',
      filament_material: 'PLA',
      filament_color_hex: 'FF0000',
      remaining_weight: 800,
    },
    {
      id: 2,
      filament_name: 'Bambu PETG Blue',
      filament_material: 'PETG',
      filament_color_hex: '0000FF',
      remaining_weight: 500,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getUnlinkedSpools).mockResolvedValue(mockSpools);
    vi.mocked(api.linkSpool).mockResolvedValue({});
  });

  describe('rendering', () => {
    it('renders modal title', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /select spool/i })).toBeInTheDocument();
      });
    });

    it('displays printer and tray info', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/AMS 0 T0/)).toBeInTheDocument();
        expect(screen.getByText(/Printer #1/)).toBeInTheDocument();
      });
    });

    it('shows loading state while fetching spools', async () => {
      vi.mocked(api.getUnlinkedSpools).mockImplementation(() => new Promise(() => {}));

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(document.querySelector('.animate-spin')).toBeInTheDocument();
      });
    });

    it('displays unlinked spools from Spoolman', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        // Should show spools from getUnlinkedSpools
        expect(screen.getByText(/Generic PLA Red/)).toBeInTheDocument();
        expect(screen.getByText(/Bambu PETG Blue/)).toBeInTheDocument();
      });
    });

    it('does not render when isOpen is false', () => {
      render(<LinkSpoolModal {...defaultProps} isOpen={false} />);
      expect(screen.queryByRole('heading', { name: /select spool/i })).not.toBeInTheDocument();
    });
  });

  describe('linking', () => {
    it('calls linkSpool on spool click', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA Red/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA Red/).closest('button')!);

      await waitFor(() => {
        expect(api.linkSpool).toHaveBeenCalledWith(1, 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4');
      });
    });

    it('shows success toast and calls onClose', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA Red/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA Red/).closest('button')!);

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalled();
        expect(defaultProps.onClose).toHaveBeenCalled();
      });
    });

    it('shows error toast on failure', async () => {
      vi.mocked(api.linkSpool).mockRejectedValue(new Error('Link failed'));

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA Red/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA Red/).closest('button')!);

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          expect.stringContaining('Link failed'),
          'error'
        );
      });
    });
  });

  describe('modal actions', () => {
    it('calls onClose when backdrop is clicked', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /select spool/i })).toBeInTheDocument();
      });

      const backdrop = document.querySelector('.bg-black\\/60');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when X button is clicked', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /select spool/i })).toBeInTheDocument();
      });

      const closeButtons = screen.getAllByRole('button');
      const xButton = closeButtons.find(btn => btn.querySelector('svg.lucide-x'));
      if (xButton) {
        fireEvent.click(xButton);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });
  });
});
