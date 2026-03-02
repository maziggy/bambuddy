import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { WeightDisplay } from '../../components/spoolbuddy/WeightDisplay';

const mockTare = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
  spoolbuddyApi: {
    tare: (...args: unknown[]) => mockTare(...args),
  },
}));

const defaultProps = {
  weight: 823.4,
  weightStable: true,
  deviceOnline: true,
  deviceId: 'sb-0001',
};

describe('WeightDisplay', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTare.mockResolvedValue({ status: 'ok' });
  });

  it('renders weight value with 1 decimal place', () => {
    render(<WeightDisplay {...defaultProps} weight={823.456} />);
    expect(screen.getByText('823.5')).toBeInTheDocument();
  });

  it('shows green dot when stable and online', () => {
    const { container } = render(
      <WeightDisplay {...defaultProps} weightStable={true} deviceOnline={true} />
    );
    const dot = container.querySelector('.bg-green-500');
    expect(dot).toBeInTheDocument();
    expect(screen.getByText('Stable')).toBeInTheDocument();
  });

  it('shows amber dot when unstable', () => {
    const { container } = render(
      <WeightDisplay {...defaultProps} weightStable={false} deviceOnline={true} />
    );
    const dot = container.querySelector('.bg-amber-500');
    expect(dot).toBeInTheDocument();
    expect(screen.getByText('Measuring...')).toBeInTheDocument();
  });

  it('shows gray dot when offline', () => {
    const { container } = render(
      <WeightDisplay {...defaultProps} deviceOnline={false} />
    );
    const dot = container.querySelector('.bg-zinc-600');
    expect(dot).toBeInTheDocument();
    expect(screen.getByText('No reading')).toBeInTheDocument();
  });

  it('tare button calls spoolbuddyApi.tare(deviceId)', async () => {
    render(<WeightDisplay {...defaultProps} />);

    const tareButton = screen.getByText('Tare');
    fireEvent.click(tareButton);

    await waitFor(() => {
      expect(mockTare).toHaveBeenCalledWith('sb-0001');
    });
  });

  it('tare button is disabled when no deviceId', () => {
    render(<WeightDisplay {...defaultProps} deviceId={null} />);

    const tareButton = screen.getByText('Tare');
    expect(tareButton).toBeDisabled();
  });
});
