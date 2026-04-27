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
    getSpoolmanInventorySpools: vi.fn(),
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
  slicer_filament_name: 'PLA',
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
  slicer_filament_name: 'PLA',
};

const anotherManualSpool = {
  id: 3,
  material: 'PLA',
  subtype: 'HF',
  brand: 'Overture',
  color_name: 'Black',
  rgba: '000000FF',
  label_weight: 1000,
  weight_used: 200,
  tag_uid: null,
  tray_uuid: null,
  slicer_filament_name: 'PLA',
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

  // Inverted from the original "filters out BL spools" expectation in #1133.
  // Bambu Lab spools (tag_uid + tray_uuid populated by SpoolBuddy NFC scan or
  // auto-creation) used to be hidden from this picker, blocking the workflow
  // where a user has a BL spool in inventory but doesn't want to scan it via
  // SpoolBuddy each time and just wants to pick it from the list. The picker
  // now lists every spool that isn't already assigned to another slot.
  it('lists Bambu Lab spools (with tag_uid/tray_uuid) alongside manual ones (#1133)', async () => {
    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    });

    expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    expect(screen.getByText(/Overture/)).toBeInTheDocument();
    // The previously-excluded BL spool is now visible.
    expect(screen.getByText(/Jade White/)).toBeInTheDocument();
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

  // Empty-state premise reworked for #1133: BL spools no longer trigger
  // the empty state by virtue of being BL, so we exercise the only
  // remaining trigger — every spool already taken by another slot.
  it('shows noAvailableSpools message when every spool is already assigned elsewhere', async () => {
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([manualSpool]);
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
      // manualSpool (id=1) is taken by a different (printer/ams/tray) tuple,
      // so it must be filtered out of THIS slot's picker.
      { id: 99, spool_id: 1, printer_id: 1, ams_id: 0, tray_id: 1 },
    ]);

    render(<AssignSpoolModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/No spools available/i)).toBeInTheDocument();
    });
  });

  // The toggle's label says "Show all spools" but originally only bypassed
  // material/profile filtering — spools assigned elsewhere stayed hidden
  // even with the toggle on. That made it impossible to recover from the
  // case where MQTT auto-reassignment beat a manual unassign by a few
  // milliseconds, leaving the just-freed spool in another slot's
  // assignment row and out of reach of this picker. With the toggle on,
  // every spool is now listed regardless of where it's currently assigned.
  it('lists spools assigned to other slots when "Show all spools" toggle is enabled', async () => {
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([manualSpool, anotherManualSpool]);
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
      // anotherManualSpool (id=3) is taken by a different slot.
      { id: 99, spool_id: 3, printer_id: 1, ams_id: 0, tray_id: 1 },
    ]);

    render(<AssignSpoolModal {...defaultProps} />);

    // Default state: spool 3 is hidden because it's assigned elsewhere.
    await waitFor(() => {
      expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Overture/)).not.toBeInTheDocument();

    // Flip the toggle — both spools must now appear, including the one
    // currently assigned to the other slot.
    const toggle = screen.getByLabelText(/show all spools/i);
    toggle.click();

    await waitFor(() => {
      expect(screen.getByText(/Overture/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Polymaker/)).toBeInTheDocument();
  });

  it('lists spool with no slicer profile when material matches the tray (#1047)', async () => {
    const spoolWithoutSlicerProfile = {
      id: 10,
      material: 'PLA',
      subtype: 'Basic',
      brand: 'Devil Design',
      color_name: 'Red',
      rgba: 'FF0000FF',
      label_weight: 1000,
      weight_used: 0,
      tag_uid: null,
      tray_uuid: null,
      slicer_filament_name: null,
      slicer_filament: null,
    };
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([spoolWithoutSlicerProfile]);

    render(
      <AssignSpoolModal
        {...defaultProps}
        trayInfo={{
          type: 'PLA',
          material: 'PLA',
          profile: 'Devil Design PLA Basic',
          color: 'FF0000',
          location: 'AMS 1 - Slot 1',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Devil Design/)).toBeInTheDocument();
    });
  });

  it('lists spool with shorter material when tray advertises a qualified variant (#1047)', async () => {
    // Spool.material = "PLA", tray material = "PLA Basic" — partial match in either direction.
    const shortMaterialSpool = {
      id: 11,
      material: 'PLA',
      subtype: 'Basic',
      brand: 'Devil Design',
      color_name: 'Red',
      rgba: 'FF0000FF',
      label_weight: 1000,
      weight_used: 0,
      tag_uid: null,
      tray_uuid: null,
      slicer_filament_name: null,
      slicer_filament: null,
    };
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([shortMaterialSpool]);

    render(
      <AssignSpoolModal
        {...defaultProps}
        trayInfo={{
          type: 'PLA Basic',
          material: 'PLA Basic',
          color: 'FF0000',
          location: 'AMS 1 - Slot 1',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Devil Design/)).toBeInTheDocument();
    });
  });

  it('lists spool whose slicer profile has an @printer qualifier that strips to the tray profile (#1047)', async () => {
    const qualifiedProfileSpool = {
      id: 12,
      material: 'PLA',
      subtype: 'Basic',
      brand: 'Devil Design',
      color_name: 'Red',
      rgba: 'FF0000FF',
      label_weight: 1000,
      weight_used: 0,
      tag_uid: null,
      tray_uuid: null,
      slicer_filament_name: 'Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)',
    };
    // Use a non-matching material to force the filter to rely on the profile path only.
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([qualifiedProfileSpool]);

    render(
      <AssignSpoolModal
        {...defaultProps}
        trayInfo={{
          type: 'ABS',
          material: 'ABS',
          profile: 'Devil Design PLA Basic',
          color: 'FF0000',
          location: 'AMS 1 - Slot 1',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Devil Design/)).toBeInTheDocument();
    });
  });
});

describe('AssignSpoolModal — Spoolman enabled (T-Gap 7)', () => {
  const spoolmanSpool = {
    id: 200,
    material: 'PETG',
    subtype: 'HF',
    brand: 'Bambu Lab',
    color_name: 'Blue',
    rgba: '0000FFFF',
    label_weight: 1000,
    weight_used: 0,
    tag_uid: null,
    tray_uuid: null,
    archived_at: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([spoolmanSpool]);
  });

  it('shows Spoolman spool section when spoolmanEnabled=true', async () => {
    render(<AssignSpoolModal {...defaultProps} spoolmanEnabled />);

    await waitFor(() => {
      // Spoolman spool brand should appear in the modal
      expect(screen.getByText(/Bambu Lab/)).toBeInTheDocument();
    });
    expect(api.getSpoolmanInventorySpools).toHaveBeenCalledWith(false);
  });

  it('does not fetch Spoolman spools when spoolmanEnabled=false', async () => {
    render(<AssignSpoolModal {...defaultProps} spoolmanEnabled={false} />);

    // Give the component time to settle
    await waitFor(() => {
      expect(api.getSpools).toHaveBeenCalled();
    });
    expect(api.getSpoolmanInventorySpools).not.toHaveBeenCalled();
  });

  it('hides archived Spoolman spools', async () => {
    const archivedSpool = { ...spoolmanSpool, id: 201, brand: 'Prusa', archived_at: '2025-01-01T00:00:00Z' };
    (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([spoolmanSpool, archivedSpool]);

    render(<AssignSpoolModal {...defaultProps} spoolmanEnabled />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab/)).toBeInTheDocument();
    });
    // Archived spool brand must NOT appear
    expect(screen.queryByText(/Prusa/)).not.toBeInTheDocument();
  });
});
