/**
 * Tests for the StatsPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { StatsPage } from '../../pages/StatsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Complete mock stats matching ArchiveStats interface
const mockStats = {
  total_prints: 150,
  successful_prints: 140,
  failed_prints: 10,
  total_print_time_hours: 500.5,
  total_filament_grams: 5500,
  total_cost: 125.50,
  prints_by_filament_type: {
    'PLA': 80,
    'PETG': 50,
    'ABS': 20,
  },
  prints_by_printer: {
    '1': 100,
    '2': 50,
  },
  average_time_accuracy: 98.5,
  time_accuracy_by_printer: {
    '1': 99.0,
    '2': 97.0,
  },
  total_energy_kwh: 45.5,
  total_energy_cost: 12.50,
};

const mockPrinters = [
  { id: 1, name: 'X1 Carbon', model: 'X1C', enabled: true },
  { id: 2, name: 'P1S', model: 'P1S', enabled: true },
];

const mockArchives = [
  {
    id: 1,
    created_at: '2024-01-01T10:00:00Z',
    started_at: '2024-01-01T10:00:00Z',
    completed_at: '2024-01-01T14:30:00Z',
    print_name: 'Benchy',
    status: 'completed',
    printer_id: 1,
    filament_type: 'PLA',
    filament_color: '#00FF00',
    filament_used_grams: 25,
    actual_time_seconds: 16200,
    print_time_seconds: 15000,
    cost: 0.75,
    depreciation_cost: 0.20,
    quantity: 1,
  },
  {
    id: 2,
    created_at: '2024-01-02T14:00:00Z',
    started_at: '2024-01-02T14:00:00Z',
    completed_at: '2024-01-02T22:00:00Z',
    print_name: 'Large Vase',
    status: 'completed',
    printer_id: 1,
    filament_type: 'PETG',
    filament_color: '#FF0000',
    filament_used_grams: 180,
    actual_time_seconds: 28800,
    print_time_seconds: 27000,
    cost: 5.40,
    depreciation_cost: 1.60,
    quantity: 1,
  },
  {
    id: 3,
    created_at: '2024-01-03T08:00:00Z',
    started_at: '2024-01-03T08:00:00Z',
    completed_at: null,
    print_name: 'Failed Bracket',
    status: 'failed',
    printer_id: 2,
    filament_type: 'ABS',
    filament_color: '#0000FF',
    filament_used_grams: 10,
    actual_time_seconds: 3600,
    print_time_seconds: 7200,
    cost: 0.30,
    depreciation_cost: null,
    quantity: 1,
  },
  {
    id: 4,
    created_at: '2024-01-03T20:00:00Z',
    started_at: '2024-01-03T20:00:00Z',
    completed_at: '2024-01-04T02:00:00Z',
    print_name: 'Phone Stand',
    status: 'completed',
    printer_id: 2,
    filament_type: 'PLA',
    filament_color: '#00FF00',
    filament_used_grams: 45,
    actual_time_seconds: 21600,
    print_time_seconds: 20000,
    cost: 1.35,
    depreciation_cost: 0.50,
    quantity: 1,
  },
];

const mockSettings = {
  currency: 'USD',
  check_updates: false,
  check_printer_firmware: false,
};

const mockFailureAnalysis = {
  period_days: 30,
  total_prints: 100,
  failed_prints: 5,
  failure_rate: 5.0,
  failures_by_reason: {
    'First layer adhesion': 3,
    'Filament runout': 2,
  },
  failures_by_filament: {
    'ABS': 3,
    'PLA': 2,
  },
  failures_by_printer: {
    '1': 2,
    '2': 3,
  },
  failures_by_hour: {},
  recent_failures: [],
  trend: [
    { week_start: '2024-01-01', total_prints: 50, failed_prints: 3, failure_rate: 6.0 },
    { week_start: '2024-01-08', total_prints: 50, failed_prints: 2, failure_rate: 5.0 },
  ],
};

describe('StatsPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/archives/stats', () => {
        return HttpResponse.json(mockStats);
      }),
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/archives/slim', () => {
        return HttpResponse.json(mockArchives);
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json(mockSettings);
      }),
      http.get('/api/v1/archives/analysis/failures', () => {
        return HttpResponse.json(mockFailureAnalysis);
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });

    it('shows quick stats widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Quick Stats')).toBeInTheDocument();
      });
    });

    it('shows total prints stat', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Total Prints')).toBeInTheDocument();
        expect(screen.getByText('150')).toBeInTheDocument();
      });
    });

    it('shows print time stat', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Time')).toBeInTheDocument();
        expect(screen.getByText('500.5h')).toBeInTheDocument();
      });
    });

    it('shows filament used stat', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Filament Used')).toBeInTheDocument();
        expect(screen.getByText('5.5kg')).toBeInTheDocument();
      });
    });
  });

  describe('success rate', () => {
    it('shows success rate widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Success Rate')).toBeInTheDocument();
        // Success rate: 140/(140+10) = 93%
        expect(screen.getByText('93%')).toBeInTheDocument();
      });
    });
  });

  describe('cost display', () => {
    it('shows filament cost', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        const elements = screen.getAllByText('Filament Cost');
        expect(elements.length).toBeGreaterThan(0);
      });
    });

    it('shows energy cost', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Energy Cost')).toBeInTheDocument();
      });
    });
  });

  describe('widgets', () => {
    it('shows time accuracy widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Time Accuracy')).toBeInTheDocument();
      });
    });

    it('shows print activity widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Activity')).toBeInTheDocument();
      });
    });

    it('shows failure analysis widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Failure Analysis')).toBeInTheDocument();
      });
    });

    it('shows printer stats widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Printer Stats')).toBeInTheDocument();
      });
    });

    it('shows filament trends widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Filament Trends')).toBeInTheDocument();
      });
    });

    it('shows records widget', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Records')).toBeInTheDocument();
      });
    });
  });

  describe('printer stats sub-cards', () => {
    it('shows prints by printer section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Prints by Printer')).toBeInTheDocument();
      });
    });

    it('shows print duration section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Duration')).toBeInTheDocument();
      });
    });

    it('shows print habits section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Habits')).toBeInTheDocument();
      });
    });

    it('shows print time of day section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Time of Day')).toBeInTheDocument();
      });
    });
  });

  describe('filament trends sub-cards', () => {
    it('shows by material section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('By Material')).toBeInTheDocument();
      });
    });

    it('shows success by material section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Success by Material')).toBeInTheDocument();
      });
    });

    it('shows color distribution section', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Color Distribution')).toBeInTheDocument();
      });
    });
  });

  describe('records widget', () => {
    it('shows longest print record', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Longest Print')).toBeInTheDocument();
      });
    });

    it('shows heaviest print record', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Heaviest Print')).toBeInTheDocument();
      });
    });

    it('shows most expensive record', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Most Expensive')).toBeInTheDocument();
      });
    });

    it('shows success streak record', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Success Streak')).toBeInTheDocument();
      });
    });
  });

  describe('export', () => {
    it('has export button', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Export Stats')).toBeInTheDocument();
      });
    });
  });

  describe('recalculate costs', () => {
    it('has recalculate costs button', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Recalculate Costs')).toBeInTheDocument();
      });
    });
  });

  describe('printer cost charts', () => {
    it('shows Printer Cost chart heading', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        expect(screen.getByText('Printer Cost')).toBeInTheDocument();
      });
    });

    it('shows Cost toggle option in Prints by Printer', async () => {
      render(<StatsPage />);

      await waitFor(() => {
        // The "Cost" button is in the MetricToggle for the "Prints by Printer" chart
        const costButtons = screen.getAllByText('Cost');
        expect(costButtons.length).toBeGreaterThan(0);
      });
    });
  });
});
