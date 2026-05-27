import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SpoolInfoCard, UnknownTagCard } from '../../components/spoolbuddy/SpoolInfoCard';
import type { MatchedSpool } from '../../hooks/useSpoolBuddyState';

const mockUpdateSpoolWeight = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
  spoolbuddyApi: {
    updateSpoolWeight: (...args: unknown[]) => mockUpdateSpoolWeight(...args),
  },
}));

const mockSpool: MatchedSpool = {
  id: 42,
  tag_uid: 'AABBCCDD11223344',
  material: 'PLA',
  subtype: 'Matte',
  color_name: 'Jade White',
  rgba: 'E8F5E9FF',
  brand: 'Bambu',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 200,
};

describe('SpoolInfoCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateSpoolWeight.mockResolvedValue({ status: 'ok', weight_used: 300 });
  });

  it('renders spool material, brand, color name', () => {
    render(<SpoolInfoCard spool={mockSpool} scaleWeight={null} />);

    expect(screen.getByText('Jade White')).toBeInTheDocument();
    expect(screen.getByText(/Bambu/)).toBeInTheDocument();
    expect(screen.getByText(/PLA/)).toBeInTheDocument();
  });

  it('shows spool color circle with correct hex color', () => {
    const { container } = render(<SpoolInfoCard spool={mockSpool} scaleWeight={null} />);

    // SpoolIcon renders an SVG circle with fill=colorHex
    const circle = container.querySelector('circle[fill="#E8F5E9"]');
    expect(circle).toBeInTheDocument();
  });

  it('shows remaining weight and fill percentage', () => {
    // scaleWeight=900g, core=250g → remaining = 900-250 = 650g
    // fillPercent = round(650/1000 * 100) = 65%
    render(<SpoolInfoCard spool={mockSpool} scaleWeight={900} />);

    expect(screen.getByText('650g')).toBeInTheDocument();
    expect(screen.getByText('65%')).toBeInTheDocument();
  });

  it('calls onAssignToAms when "Assign to AMS" button clicked', () => {
    const onAssign = vi.fn();
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={800} onAssignToAms={onAssign} />
    );

    fireEvent.click(screen.getByText('Assign to AMS'));
    expect(onAssign).toHaveBeenCalledTimes(1);
  });

  it('calls onSyncWeight when sync button clicked', async () => {
    const onSync = vi.fn();
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={800} onSyncWeight={onSync} />
    );

    fireEvent.click(screen.getByText('Sync Weight'));

    await waitFor(() => {
      expect(mockUpdateSpoolWeight).toHaveBeenCalledWith(42, 800);
    });
  });

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn();
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={null} onClose={onClose} />
    );

    fireEvent.click(screen.getByText('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('disables "Assign to AMS" button when isAssigned=true', () => {
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={800} onAssignToAms={vi.fn()} isAssigned />
    );
    expect(screen.getByText('Assign to AMS')).toBeDisabled();
  });

  it('enables "Assign to AMS" button when isAssigned=false', () => {
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={800} onAssignToAms={vi.fn()} isAssigned={false} />
    );
    expect(screen.getByText('Assign to AMS')).not.toBeDisabled();
  });

  it('shows Unassign button when isAssigned=true and onUnassignFromAms is provided', () => {
    render(
      <SpoolInfoCard
        spool={mockSpool}
        scaleWeight={800}
        onAssignToAms={vi.fn()}
        isAssigned
        onUnassignFromAms={vi.fn()}
      />
    );
    expect(screen.getByText(/unassign/i)).toBeInTheDocument();
  });

  it('does not show Unassign button when onUnassignFromAms is not provided', () => {
    render(
      <SpoolInfoCard spool={mockSpool} scaleWeight={800} onAssignToAms={vi.fn()} isAssigned />
    );
    expect(screen.queryByText(/unassign/i)).not.toBeInTheDocument();
  });

  it('calls onUnassignFromAms when Unassign button is clicked', () => {
    const onUnassign = vi.fn();
    render(
      <SpoolInfoCard
        spool={mockSpool}
        scaleWeight={800}
        onAssignToAms={vi.fn()}
        isAssigned
        onUnassignFromAms={onUnassign}
      />
    );
    fireEvent.click(screen.getByText(/unassign/i));
    expect(onUnassign).toHaveBeenCalledTimes(1);
  });
});

describe('UnknownTagCard', () => {
  it('renders tag UID', () => {
    render(<UnknownTagCard tagUid="DEADBEEF12345678" scaleWeight={null} />);

    expect(screen.getByText('DEADBEEF12345678')).toBeInTheDocument();
    expect(screen.getByText('New Tag Detected')).toBeInTheDocument();
  });

  it('shows "Add to Inventory" button', () => {
    const onAdd = vi.fn();
    render(
      <UnknownTagCard tagUid="DEADBEEF" scaleWeight={null} onAddToInventory={onAdd} />
    );

    const btn = screen.getByText('Add to Inventory');
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onAdd).toHaveBeenCalledTimes(1);
  });
});
