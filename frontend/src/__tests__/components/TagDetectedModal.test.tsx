import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { TagDetectedModal } from '../../components/spoolbuddy/TagDetectedModal';
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
  id: 7,
  tag_uid: 'AA11BB22CC33DD44',
  material: 'PETG',
  subtype: 'HF',
  color_name: 'Orange',
  rgba: 'FF6600FF',
  brand: 'Overture',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 100,
};

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  spool: mockSpool,
  tagUid: 'AA11BB22CC33DD44',
  scaleWeight: 950.0,
  weightStable: true,
  onSyncWeight: vi.fn(),
  onAssignToAms: vi.fn(),
  onLinkSpool: vi.fn(),
  onAddToInventory: vi.fn(),
};

describe('TagDetectedModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateSpoolWeight.mockResolvedValue({ status: 'ok', weight_used: 300 });
  });

  it('does not render when isOpen=false', () => {
    render(<TagDetectedModal {...defaultProps} isOpen={false} />);
    expect(screen.queryByText('Spool Detected')).not.toBeInTheDocument();
  });

  it('renders known spool view when spool provided', () => {
    render(<TagDetectedModal {...defaultProps} />);

    expect(screen.getByText('Spool Detected')).toBeInTheDocument();
    expect(screen.getByText('Orange')).toBeInTheDocument();
    expect(screen.getByText(/Overture/)).toBeInTheDocument();
    expect(screen.getByText(/PETG/)).toBeInTheDocument();
  });

  it('renders unknown tag view when spool is null', () => {
    render(
      <TagDetectedModal
        {...defaultProps}
        spool={null}
        tagUid="DEADBEEF11223344"
      />
    );

    expect(screen.getByText('New Tag Detected')).toBeInTheDocument();
    expect(screen.getByText('DEADBEEF11223344')).toBeInTheDocument();
  });

  it('closes on Escape key', () => {
    const onClose = vi.fn();
    render(<TagDetectedModal {...defaultProps} onClose={onClose} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows weight from scale', () => {
    // scaleWeight=950g, core=250g → remaining = 950-250 = 700g
    render(<TagDetectedModal {...defaultProps} scaleWeight={950} />);

    expect(screen.getByText('700g')).toBeInTheDocument();
  });

  it('shows action buttons (Assign to AMS, Sync Weight)', () => {
    const onAssign = vi.fn();
    const onSync = vi.fn();
    render(
      <TagDetectedModal
        {...defaultProps}
        onAssignToAms={onAssign}
        onSyncWeight={onSync}
      />
    );

    expect(screen.getByText('Assign to AMS')).toBeInTheDocument();
    expect(screen.getByText('Sync Weight')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Assign to AMS'));
    expect(onAssign).toHaveBeenCalledTimes(1);
  });
});
