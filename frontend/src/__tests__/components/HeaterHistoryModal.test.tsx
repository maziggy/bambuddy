/**
 * Tests for the HeaterHistoryModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { HeaterHistoryModal } from '../../components/HeaterHistoryModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getPrinterSensorHistory: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('recharts', () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Legend: () => null,
}));

const mockResponse = {
  printer_id: 1,
  series: [
    {
      sensor_kind: 'nozzle' as const,
      data: [
        { recorded_at: '2024-12-11T10:00:00Z', value: 210, target: 220 },
        { recorded_at: '2024-12-11T10:05:00Z', value: 215, target: 220 },
        { recorded_at: '2024-12-11T10:10:00Z', value: 220, target: 220 },
      ],
      min_value: 210,
      max_value: 220,
      avg_value: 215,
    },
    {
      sensor_kind: 'bed' as const,
      data: [
        { recorded_at: '2024-12-11T10:00:00Z', value: 55, target: 60 },
        { recorded_at: '2024-12-11T10:05:00Z', value: 60, target: 60 },
      ],
      min_value: 55,
      max_value: 60,
      avg_value: 57.5,
    },
    {
      sensor_kind: 'chamber' as const,
      data: [],
      min_value: null,
      max_value: null,
      avg_value: null,
    },
  ],
};

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 1,
  printerName: 'Test Printer',
  initialKind: 'nozzle' as const,
  availableKinds: ['nozzle', 'bed', 'chamber'] as const,
};

describe('HeaterHistoryModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getPrinterSensorHistory as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse);
  });

  it('renders nothing when closed', () => {
    render(<HeaterHistoryModal {...defaultProps} isOpen={false} />);
    expect(screen.queryByText(/Heater History/i)).not.toBeInTheDocument();
  });

  it('renders title + printer name when open', async () => {
    render(<HeaterHistoryModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText(/Heater History/i)).toBeInTheDocument();
      expect(screen.getByText('Test Printer')).toBeInTheDocument();
    });
  });

  it('shows current nozzle value from last data point', async () => {
    render(<HeaterHistoryModal {...defaultProps} />);
    await waitFor(() => {
      // current=220, target=220, max=220 — multiple matches are expected.
      expect(screen.getAllByText(/220°C/).length).toBeGreaterThan(0);
    });
  });

  it('switches series when bed mode button clicked', async () => {
    render(<HeaterHistoryModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText('Test Printer')).toBeInTheDocument();
    });

    const bedButtons = screen.getAllByText(/^Bed$/);
    fireEvent.click(bedButtons[0]);

    await waitFor(() => {
      // 60 = current bed value (and max, and target).
      expect(screen.getAllByText(/60°C/).length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when series has no data', async () => {
    render(<HeaterHistoryModal {...defaultProps} initialKind="chamber" />);
    await waitFor(() => {
      expect(screen.getByText(/No data recorded yet/i)).toBeInTheDocument();
    });
  });

  it('close button triggers onClose', async () => {
    const onClose = vi.fn();
    render(<HeaterHistoryModal {...defaultProps} onClose={onClose} />);
    await waitFor(() => {
      expect(screen.getByText('Test Printer')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText(/Close/i));
    expect(onClose).toHaveBeenCalled();
  });
});
