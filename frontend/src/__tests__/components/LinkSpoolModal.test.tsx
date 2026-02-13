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
    getSpools: vi.fn(),
    linkTagToSpool: vi.fn(),
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
      material: 'PLA',
      brand: 'Generic',
      subtype: '',
      color_name: 'Red',
      rgba: 'FF0000FF',
      label_weight: 1000,
      weight_used: 200,
      tag_uid: null,
      tray_uuid: null,
    },
    {
      id: 2,
      material: 'PETG',
      brand: 'Bambu',
      subtype: 'Basic',
      color_name: 'Blue',
      rgba: '0000FFFF',
      label_weight: 1000,
      weight_used: 500,
      tag_uid: null,
      tray_uuid: null,
    },
    {
      id: 3,
      material: 'ABS',
      brand: 'Brand',
      subtype: '',
      color_name: 'White',
      rgba: 'FFFFFFFF',
      label_weight: 1000,
      weight_used: 0,
      tag_uid: 'EXISTING_TAG',
      tray_uuid: 'EXISTING_UUID',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpools).mockResolvedValue(mockSpools);
    vi.mocked(api.linkTagToSpool).mockResolvedValue({});
  });

  describe('rendering', () => {
    it('renders modal title', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /link to spool/i })).toBeInTheDocument();
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
      vi.mocked(api.getSpools).mockImplementation(() => new Promise(() => {}));

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(document.querySelector('.animate-spin')).toBeInTheDocument();
      });
    });

    it('displays untagged spools only', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        // Spools 1 and 2 have no tag_uid/tray_uuid — should be shown
        expect(screen.getByText(/Generic PLA/)).toBeInTheDocument();
        expect(screen.getByText(/Bambu PETG/)).toBeInTheDocument();
      });

      // Spool 3 has tag_uid — should be filtered out
      expect(screen.queryByText(/Brand ABS/)).not.toBeInTheDocument();
    });

    it('does not render when isOpen is false', () => {
      render(<LinkSpoolModal {...defaultProps} isOpen={false} />);
      expect(screen.queryByRole('heading', { name: /link to spool/i })).not.toBeInTheDocument();
    });
  });

  describe('linking', () => {
    it('calls linkTagToSpool on spool click', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA/).closest('button')!);

      await waitFor(() => {
        expect(api.linkTagToSpool).toHaveBeenCalledWith(1, {
          tag_uid: 'ABCD1234',
          tray_uuid: 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4',
          tag_type: 'bambulab',
          data_origin: 'nfc_link',
        });
      });
    });

    it('shows success toast and calls onClose', async () => {
      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA/).closest('button')!);

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalled();
        expect(defaultProps.onClose).toHaveBeenCalled();
      });
    });

    it('shows error toast on failure', async () => {
      vi.mocked(api.linkTagToSpool).mockRejectedValue(new Error('Link failed'));

      render(<LinkSpoolModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/Generic PLA/)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/Generic PLA/).closest('button')!);

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
        expect(screen.getByRole('heading', { name: /link to spool/i })).toBeInTheDocument();
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
        expect(screen.getByRole('heading', { name: /link to spool/i })).toBeInTheDocument();
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
