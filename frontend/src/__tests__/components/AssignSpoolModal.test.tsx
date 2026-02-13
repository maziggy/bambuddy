import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { AssignSpoolModal } from '../../components/AssignSpoolModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn(),
    getAssignments: vi.fn(),
    assignSpool: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 1,
  amsId: 0,
  trayId: 0,
  trayInfo: { type: 'PLA', color: 'FF0000', location: 'AMS 1 - Slot 1' },
};

const manualSpool = {
  id: 1,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 0,
  tag_uid: null,
  tray_uuid: null,
};

const blSpool = {
  id: 2,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Bambu',
  color_name: 'Jade White',
  rgba: 'FFFFFFFE',
  label_weight: 1000,
  weight_used: 50,
  tag_uid: '05CC1E0F00000100',
  tray_uuid: 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4',
};

const anotherManualSpool = {
  id: 3,
  material: 'PETG',
  subtype: 'HF',
  brand: 'Overture',
  color_name: 'Black',
  rgba: '000000FF',
  label_weight: 1000,
  weight_used: 200,
  tag_uid: null,
  tray_uuid: null,
};

describe('AssignSpoolModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([manualSpool, blSpool, anotherManualSpool]);
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it('renders nothing when closed', () => {
    render(<AssignSpoolModal {...defaultProps} isOpen={false} />);
    expect(screen.queryByText('Assign Spool')).not.toBeInTheDocument();
  });

  it('filters out Bambu Lab spools (with tag_uid/tray_uuid)', async () => {
    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    });

    // Manual spools should be visible
    expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    expect(screen.getByText(/Overture/)).toBeInTheDocument();

    // BL spool should NOT be visible
    expect(screen.queryByText(/Jade White/)).not.toBeInTheDocument();
  });

  it('filters out spools already assigned to other slots', async () => {
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 1, spool_id: 3, printer_id: 1, ams_id: 0, tray_id: 1 }, // spool 3 assigned to different slot
    ]);

    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    });

    // Spool 1 (not assigned) should be visible
    expect(screen.getByText(/Polymaker/)).toBeInTheDocument();

    // Spool 3 (assigned to another slot) should NOT be visible
    expect(screen.queryByText(/Overture/)).not.toBeInTheDocument();
  });

  it('keeps spool visible if assigned to the current slot', async () => {
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 1, spool_id: 1, printer_id: 1, ams_id: 0, tray_id: 0 }, // spool 1 assigned to THIS slot
    ]);

    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    });

    // Spool 1 (assigned to current slot) should still be visible for re-assignment
    expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
  });

  it('shows noManualSpools message when all spools are BL or assigned', async () => {
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([blSpool]);

    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/No manually added spools/i)).toBeInTheDocument();
    });
  });
});
