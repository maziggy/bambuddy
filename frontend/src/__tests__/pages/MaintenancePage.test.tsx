/**
 * Tests for the MaintenancePage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { MaintenancePage } from '../../pages/MaintenancePage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    model: 'X1C',
    serial_number: '00M09A350100001',
  },
];

const mockMaintenanceTypes = [
  {
    id: 1,
    name: 'Clean Nozzle',
    description: 'Clean the printer nozzle',
    default_interval_hours: 50,
    applies_to_models: ['X1C', 'P1S'],
  },
  {
    id: 2,
    name: 'Lubricate Rods',
    description: 'Lubricate linear rods',
    default_interval_hours: 200,
    applies_to_models: ['X1C', 'P1S'],
  },
];

const mockMaintenanceTasks = [
  {
    id: 1,
    printer_id: 1,
    maintenance_type_id: 1,
    maintenance_type_name: 'Clean Nozzle',
    interval_hours: 50,
    last_completed_at: '2024-01-01T00:00:00Z',
    next_due_at: '2024-01-03T00:00:00Z',
    hours_until_due: 10,
    is_due: false,
    notes: null,
  },
  {
    id: 2,
    printer_id: 1,
    maintenance_type_id: 2,
    maintenance_type_name: 'Lubricate Rods',
    interval_hours: 200,
    last_completed_at: '2023-12-01T00:00:00Z',
    next_due_at: '2023-12-15T00:00:00Z',
    hours_until_due: -100,
    is_due: true,
    notes: 'Use PTFE lubricant',
  },
];

describe('MaintenancePage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/maintenance/types', () => {
        return HttpResponse.json(mockMaintenanceTypes);
      }),
      http.get('/api/v1/maintenance/', () => {
        return HttpResponse.json(mockMaintenanceTasks);
      }),
      http.get('/api/v1/maintenance/overview', () => {
        // Overview is an array of printer summaries
        return HttpResponse.json([
          {
            printer_id: 1,
            printer_name: 'X1 Carbon',
            due_count: 1,
            warning_count: 0,
            total_print_hours: 100,
            maintenance_items: [
              {
                id: 1,
                maintenance_type_id: 1,
                maintenance_type_name: 'Clean Nozzle',
                interval_hours: 50,
                hours_since_last: 45,
                hours_until_due: 5,
                is_due: false,
                is_warning: false,
              },
              {
                id: 2,
                maintenance_type_id: 2,
                maintenance_type_name: 'Lubricate Rods',
                interval_hours: 200,
                hours_since_last: 250,
                hours_until_due: -50,
                is_due: true,
                is_warning: false,
              },
            ],
          },
        ]);
      }),
      http.post('/api/v1/maintenance/', async ({ request }) => {
        const body = await request.json() as { name: string };
        return HttpResponse.json({ id: 3, ...body });
      }),
      http.post('/api/v1/maintenance/:id/complete', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<MaintenancePage />);

      await waitFor(() => {
        expect(screen.getByText('Maintenance')).toBeInTheDocument();
      });
    });

    it('renders maintenance page content', async () => {
      render(<MaintenancePage />);

      await waitFor(() => {
        // Page should render with printer tabs or tasks
        expect(screen.getByText('Maintenance')).toBeInTheDocument();
      });
    });
  });

  describe('printer tabs', () => {
    it('shows printer tabs when printers exist', async () => {
      render(<MaintenancePage />);

      await waitFor(() => {
        // Should show printer name in tabs
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
    });
  });
});
