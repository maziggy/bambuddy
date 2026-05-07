import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { InventorySpoolInfoCard } from '../../components/spoolbuddy/InventorySpoolInfoCard';
import type { InventorySpool } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getSpoolKProfiles: vi.fn().mockResolvedValue([]),
  },
  spoolbuddyApi: {
    updateSpoolWeight: vi.fn().mockResolvedValue({ status: 'ok', weight_used: 300 }),
  },
}));

const mockSpool: InventorySpool = {
  id: 42,
  material: 'PLA',
  subtype: 'Matte',
  color_name: 'Jade White',
  rgba: 'E8F5E9FF',
  extra_colors: null,
  effect_type: null,
  brand: 'Bambu',
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 200,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: 'AABBCCDD11223344',
  tray_uuid: null,
  data_origin: 'local',
  tag_type: null,
  archived_at: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  category: null,
  low_stock_threshold_pct: null,
  k_profiles: [],
};

describe('InventorySpoolInfoCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('disables "Assign to AMS" button when isAssigned=true', () => {
    render(
      <InventorySpoolInfoCard
        spool={mockSpool}
        liveScaleWeight={null}
        onAssignToAms={vi.fn()}
        isAssigned
      />
    );
    expect(screen.getByText('Assign to AMS')).toBeDisabled();
  });

  it('enables "Assign to AMS" button when isAssigned=false', () => {
    render(
      <InventorySpoolInfoCard
        spool={mockSpool}
        liveScaleWeight={null}
        onAssignToAms={vi.fn()}
        isAssigned={false}
      />
    );
    expect(screen.getByText('Assign to AMS')).not.toBeDisabled();
  });

  it('shows Unassign button when isAssigned=true and onUnassignFromAms is provided', () => {
    render(
      <InventorySpoolInfoCard
        spool={mockSpool}
        liveScaleWeight={null}
        onAssignToAms={vi.fn()}
        isAssigned
        onUnassignFromAms={vi.fn()}
      />
    );
    expect(screen.getByText(/unassign/i)).toBeInTheDocument();
  });

  it('does not show Unassign button when onUnassignFromAms is not provided', () => {
    render(
      <InventorySpoolInfoCard
        spool={mockSpool}
        liveScaleWeight={null}
        onAssignToAms={vi.fn()}
        isAssigned
      />
    );
    expect(screen.queryByText(/unassign/i)).not.toBeInTheDocument();
  });

  it('calls onUnassignFromAms when Unassign button is clicked', () => {
    const onUnassign = vi.fn();
    render(
      <InventorySpoolInfoCard
        spool={mockSpool}
        liveScaleWeight={null}
        onAssignToAms={vi.fn()}
        isAssigned
        onUnassignFromAms={onUnassign}
      />
    );
    fireEvent.click(screen.getByText(/unassign/i));
    expect(onUnassign).toHaveBeenCalledTimes(1);
  });
});
