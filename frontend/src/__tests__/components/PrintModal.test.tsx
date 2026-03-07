/**
 * Tests for the unified PrintModal component.
 *
 * The PrintModal supports three modes:
 * - 'reprint': Immediate print from archive (multi-printer support)
 * - 'add-to-queue': Schedule print to queue (multi-printer support)
 * - 'edit-queue-item': Edit existing queue item (single printer)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintModal } from '../../components/PrintModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import type { PrintQueueItem } from '../../api/client';

const mockPrinters = [
  { id: 1, name: 'X1 Carbon', model: 'X1C', ip_address: '192.168.1.100', enabled: true, is_active: true },
  { id: 2, name: 'P1S', model: 'P1S', ip_address: '192.168.1.101', enabled: true, is_active: true },
  { id: 3, name: 'A1 Mini', model: 'A1M', ip_address: '192.168.1.102', enabled: true, is_active: true },
];

const createMockQueueItem = (overrides: Partial<PrintQueueItem> = {}): PrintQueueItem => ({
  id: 1,
  printer_id: 1,
  archive_id: 1,
  position: 1,
  scheduled_time: null,
  require_previous_success: false,
  auto_off_after: false,
  manual_start: false,
  ams_mapping: null,
  plate_id: null,
  bed_levelling: true,
  flow_cali: false,
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
  use_ams: true,
  status: 'pending',
  started_at: null,
  completed_at: null,
  error_message: null,
  created_at: '2024-01-01T00:00:00Z',
  archive_name: 'Test Print',
  archive_thumbnail: null,
  printer_name: 'Test Printer',
  print_time_seconds: 3600,
  ...overrides,
});

describe('PrintModal', () => {
  const mockOnClose = vi.fn();
  const mockOnSuccess = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/archives/:id/plates', () => {
        return HttpResponse.json({ is_multi_plate: false, plates: [] });
      }),
      http.get('/api/v1/archives/:id/filament-requirements', () => {
        return HttpResponse.json({ filaments: [] });
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] });
      }),
      http.post('/api/v1/archives/:id/reprint', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/queue/', () => {
        return HttpResponse.json({ id: 1, status: 'pending' });
      }),
      http.patch('/api/v1/queue/:id', () => {
        return HttpResponse.json({ id: 1, status: 'pending' });
      })
    );
  });

  describe('reprint mode', () => {
    it('renders the modal title', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByText('Re-print')).toBeInTheDocument();
    });

    it('shows archive name', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByText('Benchy')).toBeInTheDocument();
    });

    it('shows printer selection with checkboxes for multi-select', async () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.getByText('P1S')).toBeInTheDocument();
      });
    });

    it('has print button', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      // Get the submit button specifically (not printer selection buttons)
      const submitButton = screen.getByRole('button', { name: /^print$/i });
      expect(submitButton).toBeInTheDocument();
    });

    it('has cancel button', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('print button is disabled until printer is selected', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      // Get the submit button specifically (not printer selection buttons)
      const printButton = screen.getByRole('button', { name: /^print$/i });
      expect(printButton).toBeDisabled();
    });

    it('shows no printers message when none active', async () => {
      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([]);
        })
      );

      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('No active printers available')).toBeInTheDocument();
      });
    });

    it('shows print options toggle', () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByText('Print Options')).toBeInTheDocument();
    });
  });

  describe('add-to-queue mode', () => {
    it('renders the modal title', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Schedule Print')).toBeInTheDocument();
    });

    it('shows archive name', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Test Print')).toBeInTheDocument();
    });

    it('shows add button', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /add to queue/i })).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('shows Queue Only option', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Queue Only')).toBeInTheDocument();
    });

    it('shows power off option', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText(/power off/i)).toBeInTheDocument();
    });

    it('shows schedule options', () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('ASAP')).toBeInTheDocument();
      expect(screen.getByText('Scheduled')).toBeInTheDocument();
    });

    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('edit-queue-item mode', () => {
    it('renders the modal title', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Edit Queue Item')).toBeInTheDocument();
    });

    it('shows save button', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('shows print options toggle', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Print Options')).toBeInTheDocument();
    });

    it('shows Queue Only option', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Queue Only')).toBeInTheDocument();
    });

    it('shows power off option', () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText(/power off/i)).toBeInTheDocument();
    });

    it('calls onClose when cancel button is clicked', async () => {
      const user = userEvent.setup();
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      await user.click(cancelButton);

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('shows printer selector for single selection', async () => {
      const item = createMockQueueItem();

      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Test Print"
          queueItem={item}
          onClose={mockOnClose}
        />
      );

      // PrinterSelector shows printer names directly
      await waitFor(() => {
        expect(screen.getByText('P1S')).toBeInTheDocument();
      });
    });
  });

  describe('multi-printer selection', () => {
    it('shows select all button when multiple printers available', async () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });
    });

    it('shows selected count when multiple printers selected', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        expect(screen.getByText(/3 printers selected/)).toBeInTheDocument();
      });
    });

    it('updates button text when multiple printers selected', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /print to 3 printers/i })).toBeInTheDocument();
      });
    });
  });

  describe('busy printer handling (#622)', () => {
    beforeEach(() => {
      // Set up per-printer statuses: printer 1 RUNNING, printer 2 IDLE, printer 3 FINISH
      server.use(
        http.get('/api/v1/printers/:id/status', ({ params }) => {
          const id = Number(params.id);
          if (id === 1) {
            return HttpResponse.json({
              connected: true, state: 'RUNNING', stg_cur_name: null,
              ams: [], vt_tray: [], nozzles: [],
            });
          }
          if (id === 2) {
            return HttpResponse.json({
              connected: true, state: 'IDLE', stg_cur_name: null,
              ams: [], vt_tray: [], nozzles: [],
            });
          }
          // printer 3
          return HttpResponse.json({
            connected: true, state: 'FINISH', stg_cur_name: null,
            ams: [], vt_tray: [], nozzles: [],
          });
        })
      );
    });

    it('shows state badges on printers in reprint mode', async () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
        expect(screen.getByText('Idle')).toBeInTheDocument();
        expect(screen.getByText('Finished')).toBeInTheDocument();
      });
    });

    it('prevents selecting a busy printer in reprint mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
      });

      // The busy printer button should be disabled
      const busyButton = screen.getByText('X1 Carbon').closest('button');
      expect(busyButton).toBeDisabled();

      // Click the busy printer — selection should not change
      await user.click(busyButton!);

      // Idle printer should still be selectable
      const idleButton = screen.getByText('P1S').closest('button');
      expect(idleButton).not.toBeDisabled();
      await user.click(idleButton!);

      await waitFor(() => {
        expect(screen.getByText('1 printer selected')).toBeInTheDocument();
      });
    });

    it('select all skips busy printers in reprint mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
        expect(screen.getByText('Printing')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        // Only 2 available printers selected (IDLE + FINISH), not the RUNNING one
        expect(screen.getByText(/2 printers selected/)).toBeInTheDocument();
      });
    });

    it('allows selecting busy printers in add-to-queue mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
      });

      // The busy printer button should NOT be disabled in queue mode
      const busyButton = screen.getByText('X1 Carbon').closest('button');
      expect(busyButton).not.toBeDisabled();

      await user.click(busyButton!);

      await waitFor(() => {
        expect(screen.getByText('1 printer selected')).toBeInTheDocument();
      });
    });

    it('shows Offline badge for disconnected printers', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({
            connected: false, state: null, stg_cur_name: null,
            ams: [], vt_tray: [], nozzles: [],
          });
        })
      );

      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        const offlineBadges = screen.getAllByText('Offline');
        expect(offlineBadges.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('shows calibration stage name when printer is calibrating', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({
            connected: true, state: 'RUNNING', stg_cur_name: 'Auto bed leveling',
            ams: [], vt_tray: [], nozzles: [],
          });
        })
      );

      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        const badges = screen.getAllByText('Auto bed leveling');
        expect(badges.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  describe('queue all plates', () => {
    const multiPlateResponse = {
      is_multi_plate: true,
      plates: [
        { index: 1, name: 'Plate 1', has_thumbnail: false, thumbnail_url: null, objects: ['Part A'], filaments: [{ type: 'PLA', color: '#FF0000' }], print_time_seconds: 1800, filament_used_grams: 50 },
        { index: 2, name: 'Plate 2', has_thumbnail: false, thumbnail_url: null, objects: ['Part B'], filaments: [{ type: 'PLA', color: '#00FF00' }], print_time_seconds: 2400, filament_used_grams: 60 },
        { index: 3, name: 'Plate 3', has_thumbnail: false, thumbnail_url: null, objects: ['Part C'], filaments: [{ type: 'PETG', color: '#0000FF' }], print_time_seconds: 3000, filament_used_grams: 70 },
      ],
    };

    beforeEach(() => {
      server.use(
        http.get('/api/v1/archives/:id/plates', () => {
          return HttpResponse.json(multiPlateResponse);
        }),
      );
    });

    it('shows "Queue All" button only in add-to-queue mode', async () => {
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Queue All 3 Plates')).toBeInTheDocument();
      });
    });

    it('does not show "Queue All" button in reprint mode', async () => {
      render(
        <PrintModal
          mode="reprint"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plate 1')).toBeInTheDocument();
      });
      expect(screen.queryByText('Queue All 3 Plates')).not.toBeInTheDocument();
    });

    it('highlights all plates when "Queue All" is clicked', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Queue All 3 Plates')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Queue All 3 Plates'));

      // All plates should show check marks
      await waitFor(() => {
        const checks = document.querySelectorAll('.text-bambu-green.flex-shrink-0');
        expect(checks.length).toBe(3);
      });
    });

    it('creates one queue item per plate when submitting with queue-all', async () => {
      const queueRequests: unknown[] = [];
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          const body = await request.json();
          queueRequests.push(body);
          return HttpResponse.json({ id: queueRequests.length, status: 'pending' });
        }),
      );

      const user = userEvent.setup();
      render(
        <PrintModal
          mode="add-to-queue"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      // Wait for plates and select a printer
      await waitFor(() => {
        expect(screen.getByText('Queue All 3 Plates')).toBeInTheDocument();
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Select printer
      await user.click(screen.getByText('X1 Carbon'));

      // Click queue all
      await user.click(screen.getByText('Queue All 3 Plates'));

      // Find the submit button (type="submit") — distinct from the toggle button (type="button")
      const submitButton = document.querySelector('button[type="submit"]') as HTMLElement;
      await user.click(submitButton);

      await waitFor(() => {
        expect(queueRequests.length).toBe(3);
      });

      // Verify each request has the correct plate_id
      expect((queueRequests[0] as { plate_id: number }).plate_id).toBe(1);
      expect((queueRequests[1] as { plate_id: number }).plate_id).toBe(2);
      expect((queueRequests[2] as { plate_id: number }).plate_id).toBe(3);
    });
  });
});
