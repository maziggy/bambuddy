/**
 * Tests for the LinkSpoolModal component.
 *
 * Tests the Spoolman link spool modal including:
 * - Displaying unlinked spools
 * - Selecting a spool to link
 * - Link success with toast notification
 * - Link error with toast notification
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
    getAuthStatus: vi.fn().mockResolvedValue({ enabled: false, configured: false }),
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
    trayUuid: 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4',
    trayInfo: {
      type: 'PLA Basic',
      color: 'FF0000',
      location: 'AMS A1',
    },
  };

  const mockUnlinkedSpools = [
    {
      id: 1,
      filament_name: 'PLA Red',
      filament_material: 'PLA',
      filament_color_hex: 'FF0000',
      remaining_weight: 800,
      location: 'Shelf A',
    },
    {
      id: 2,
      filament_name: 'PETG Blue',
      filament_material: 'PETG',
      filament_color_hex: '0000FF',
      remaining_weight: 500,
      location: null,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getUnlinkedSpools).mockResolvedValue(mockUnlinkedSpools);
    vi.mocked(api.linkSpool).mockResolvedValue({ success: true, message: 'Linked' });
  });

  describe('rendering', () => {
    it('renders modal title', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        // Look for the title in h2 element
        expect(screen.getByRole('heading', { name: /link to spoolman/i })).toBeInTheDocument();
      });
    });

    it('displays tray info', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Basic')).toBeInTheDocument();
        expect(screen.getByText('(AMS A1)')).toBeInTheDocument();
      });
    });

    it('displays tray UUID', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(defaultProps.trayUuid)).toBeInTheDocument();
      });
    });

    it('shows loading state while fetching spools', async () => {
      // Delay the response
      vi.mocked(api.getUnlinkedSpools).mockImplementation(
        () => new Promise(() => {})
      );

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(document.querySelector('.animate-spin')).toBeInTheDocument();
      });
    });

    it('displays unlinked spools list', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
        expect(screen.getByText('PETG Blue')).toBeInTheDocument();
      });
    });

    it('shows message when no unlinked spools', async () => {
      vi.mocked(api.getUnlinkedSpools).mockResolvedValue([]);

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('No unlinked spools available')).toBeInTheDocument();
      });
    });

    it('does not render when isOpen is false', () => {
      render(<LinkSpoolModal {...defaultProps} isOpen={false} />);
      expect(screen.queryByRole('heading', { name: /link to spoolman/i })).not.toBeInTheDocument();
    });
  });

  describe('spool selection', () => {
    it('allows selecting a spool', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      // Click to select spool
      fireEvent.click(screen.getByText('PLA Red'));

      // Should show check mark (via visual styling)
      const selectedButton = screen.getByText('PLA Red').closest('button');
      expect(selectedButton).toHaveClass('border-bambu-green');
    });

    it('link button is disabled until spool is selected', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      const linkButton = screen.getByRole('button', { name: /link to spoolman/i });
      expect(linkButton).toBeDisabled();

      // Select a spool
      fireEvent.click(screen.getByText('PLA Red'));

      expect(linkButton).not.toBeDisabled();
    });
  });

  describe('linking', () => {
    it('calls linkSpool API on submit', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      // Select a spool
      fireEvent.click(screen.getByText('PLA Red'));

      // Click link button
      fireEvent.click(screen.getByRole('button', { name: /link to spoolman/i }));

      await waitFor(() => {
        expect(api.linkSpool).toHaveBeenCalledWith(1, defaultProps.trayUuid);
      });
    });

    it('shows success toast on successful link', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('PLA Red'));
      fireEvent.click(screen.getByRole('button', { name: /link to spoolman/i }));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          'Spool linked to Spoolman successfully',
          'success'
        );
      });
    });

    it('calls onClose after successful link', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('PLA Red'));
      fireEvent.click(screen.getByRole('button', { name: /link to spoolman/i }));

      await waitFor(() => {
        expect(defaultProps.onClose).toHaveBeenCalled();
      });
    });

    it('shows error toast on link failure', async () => {
      const errorMessage = 'Failed to update spool';
      vi.mocked(api.linkSpool).mockRejectedValue(new Error(errorMessage));

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('PLA Red')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('PLA Red'));
      fireEvent.click(screen.getByRole('button', { name: /link to spoolman/i }));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith(
          `Failed to link spool: ${errorMessage}`,
          'error'
        );
      });
    });
  });

  describe('modal actions', () => {
    it('calls onClose when cancel button is clicked', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('Cancel')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Cancel'));
      expect(defaultProps.onClose).toHaveBeenCalled();
    });

    it('calls onClose when backdrop is clicked', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /link to spoolman/i })).toBeInTheDocument();
      });

      // Click the backdrop (the element with bg-black/60)
      const backdrop = document.querySelector('.bg-black\\/60');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when X button is clicked', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /link to spoolman/i })).toBeInTheDocument();
      });

      // Find and click the X button in the header
      const closeButtons = screen.getAllByRole('button');
      const xButton = closeButtons.find(btn => btn.querySelector('svg.lucide-x'));
      if (xButton) {
        fireEvent.click(xButton);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });
  });
});
