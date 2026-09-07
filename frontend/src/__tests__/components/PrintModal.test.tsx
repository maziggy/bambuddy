/**
 * Tests for the unified PrintModal component.
 *
 * The PrintModal supports two modes:
 * - 'create': Create a print queue item
 * - 'edit-queue-item': Edit existing queue item (single printer)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintModal } from '../../components/PrintModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import type { PrintQueueItem } from '../../api/client';
import { formatDateInput, parseDateInput, parseTimeInput } from '../../utils/date';

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
  wait_for_drying_complete: false,
  auto_off_after: false,
  gcode_injection: false,
  manual_start: false,
  force_color_match: true,
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
  batch_id: null,
  batch_name: null,
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
      http.post('/api/v1/queue/', () => {
        return HttpResponse.json({ id: 1, status: 'pending' });
      }),
      http.patch('/api/v1/queue/:id', () => {
        return HttpResponse.json({ id: 1, status: 'pending' });
      })
    );
  });

  describe('create mode', () => {
    it('renders the modal title', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByRole('heading', { name: 'Print' })).toBeInTheDocument();
    });

    it('shows archive name', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByText('Benchy')).toBeInTheDocument();
    });

    it('shows printer selection with checkboxes for multi-select', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.getByText('P1S')).toBeInTheDocument();
      });
    });

    it('defaults to the existing first-model fallback', async () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const anyModel = await screen.findByRole('button', { name: 'Any A1M' });
      const specificPrinter = screen.getByRole('button', { name: 'Specific Printer' });

      expect(anyModel).toHaveClass('border-bambu-green');
      expect(anyModel.compareDocumentPosition(specificPrinter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('defaults farm assignment to the model the file was sliced for', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      const user = userEvent.setup();
      server.use(
        http.get('/api/v1/archives/:id', () => HttpResponse.json({ sliced_for_model: 'X2D' })),
        http.get('/api/v1/printers/', () => HttpResponse.json([
          ...mockPrinters,
          { id: 4, name: 'X2D', model: 'X2D', ip_address: '192.168.1.103', enabled: true, is_active: true },
        ])),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      const anyX2D = await screen.findByRole('button', { name: 'Any X2D' });
      expect(anyX2D).toHaveClass('border-bambu-green');

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: null, target_model: 'X2D' });
    });

    it('updates the archive model assignment when sliced metadata loads after printers', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      const user = userEvent.setup();
      server.use(
        http.get('/api/v1/archives/:id', async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({ sliced_for_model: 'X2D' });
        }),
        http.get('/api/v1/printers/', () => HttpResponse.json([
          ...mockPrinters,
          { id: 4, name: 'X2D', model: 'X2D', ip_address: '192.168.1.103', enabled: true, is_active: true },
        ])),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      expect(await screen.findByRole('button', { name: 'Any A1M' })).toBeInTheDocument();
      expect(await screen.findByRole('button', { name: 'Any X2D' })).toHaveClass('border-bambu-green');

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: null, target_model: 'X2D' });
    });

    it('updates the library-file model assignment when sliced metadata loads after printers', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      const user = userEvent.setup();
      server.use(
        http.get('/api/v1/library/files/:id', async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({ sliced_for_model: 'X2D' });
        }),
        http.get('/api/v1/library/files/:id/plates', () => {
          return HttpResponse.json({ is_multi_plate: false, plates: [] });
        }),
        http.get('/api/v1/library/files/:id/filament-requirements', () => {
          return HttpResponse.json({ file_id: 5, filename: 'benchy.gcode.3mf', filaments: [] });
        }),
        http.get('/api/v1/printers/', () => HttpResponse.json([
          ...mockPrinters,
          { id: 4, name: 'X2D', model: 'X2D', ip_address: '192.168.1.103', enabled: true, is_active: true },
        ])),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );

      render(
        <PrintModal
          mode="create"
          libraryFileId={5}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      expect(await screen.findByRole('button', { name: 'Any A1M' })).toBeInTheDocument();
      expect(await screen.findByRole('button', { name: 'Any X2D' })).toHaveClass('border-bambu-green');

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: null, target_model: 'X2D', library_file_id: 5 });
    });

    it('falls back to specific-printer assignment when printer models are unknown', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      const user = userEvent.setup();
      server.use(
        http.get('/api/v1/printers/', () => HttpResponse.json([
          { ...mockPrinters[0], model: null },
        ])),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      const printer = await screen.findByRole('button', { name: /X1 Carbon/ });
      await user.click(printer);
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: 1, target_model: null });
    });

    it('keeps direct-print flows assigned to their preselected printer', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      const user = userEvent.setup();
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: 1, target_model: null });
    });

    it('has print button', () => {
      render(
        <PrintModal
          mode="create"
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
          mode="create"
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
          mode="create"
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
          mode="create"
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
          mode="create"
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

    it('shows print options toggle', async () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Print Options')).toBeInTheDocument();
      });
    });

    it('keeps model filament controls collapsed until they are needed', async () => {
      let filamentRequirementsRequested = false;
      server.use(
        http.get('/api/v1/archives/:id/plates', () =>
          HttpResponse.json({ is_multi_plate: false, plates: [] }),
        ),
        http.get('/api/v1/archives/:id/filament-requirements', () => {
          filamentRequirementsRequested = true;
          return HttpResponse.json({
            filaments: [
              { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 },
            ],
          });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={99}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Any A1M' }));
      await waitFor(() => expect(filamentRequirementsRequested).toBe(true));

      const filamentOptions = await screen.findByRole('button', { name: 'Filament Requirements' });
      expect(filamentOptions).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByLabelText(/Match colour/i)).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/PLA filament profile/i)).not.toBeInTheDocument();

      await user.click(filamentOptions);

      expect(filamentOptions).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByLabelText(/Match colour/i)).toBeInTheDocument();
      expect(await screen.findByLabelText(/PLA filament profile/i)).toBeInTheDocument();
    });

    it('queues a postponed print with the selected UTC start time', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />,
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      const dateInput = screen.getByLabelText('Do not start before') as HTMLInputElement;
      const timeInput = screen.getByLabelText('Postpone time') as HTMLInputElement;
      await waitFor(() => expect(dateInput.value).not.toBe(''));
      const date = parseDateInput(dateInput.value, 'system')!;
      const time = parseTimeInput(timeInput.value)!;
      date.setHours(time.hours, time.minutes, 0, 0);

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.scheduled_time).toBe(date.toISOString());
    });

    it('queues a postponed model-assigned print with its target model', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(<PrintModal mode="create" archiveId={1} archiveName="Benchy" onClose={mockOnClose} />);

      await user.click(await screen.findByRole('button', { name: 'Any A1M' }));
      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ target_model: 'A1M' });
      expect(capturedBody?.scheduled_time).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z$/);
    });

    it('persists model filament overrides and the colour-match policy', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.get('/api/v1/archives/:id/filament-requirements', () => HttpResponse.json({
          filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 }],
        })),
        http.get('/api/v1/printers/available-filaments', () => HttpResponse.json([
          { type: 'PLA', color: '#00FF00', tray_info_idx: '1', tray_sub_brands: 'PLA Basic', extruder_id: null },
        ])),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(<PrintModal mode="create" archiveId={1} archiveName="Benchy" onClose={mockOnClose} />);

      await user.click(await screen.findByRole('button', { name: 'Any A1M' }));
      await user.click(await screen.findByRole('button', { name: 'Filament Requirements' }));
      await user.click(screen.getByLabelText(/Match colour/i));
      await user.click(await screen.findByRole('combobox', { name: /PLA filament profile/i }));
      await user.click(await screen.findByRole('option', { name: /PLA Basic \(Green\)/i }));
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.force_color_match).toBe(false);
      expect(capturedBody?.filament_overrides).toEqual([
        expect.objectContaining({ slot_id: 1, type: 'PLA', color: '#00FF00', force_color_match: false }),
      ]);
    });

    it('queues printer-targeted jobs with force colour matching enabled by default', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.get('/api/v1/archives/:id/filament-requirements', () =>
          HttpResponse.json({
            filaments: [
              { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 },
            ],
          }),
        ),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const forceMatch = await screen.findByLabelText(/Match colour/i) as HTMLInputElement;
      expect(forceMatch).toBeChecked();

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.printer_id).toBe(1);
      expect(capturedBody?.force_color_match).toBe(true);
      expect(capturedBody?.filament_overrides).toEqual([
        expect.objectContaining({
          slot_id: 1,
          type: 'PLA',
          color: '#FF0000',
          force_color_match: true,
        }),
      ]);
    });

    it('preserves an explicit per-slot force colour opt-out', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.get('/api/v1/archives/:id/filament-requirements', () =>
          HttpResponse.json({
            filaments: [
              { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 },
            ],
          }),
        ),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const forceMatch = await screen.findByLabelText(/Match colour/i) as HTMLInputElement;
      await user.click(forceMatch);
      expect(forceMatch).not.toBeChecked();
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.force_color_match).toBe(false);
      expect(capturedBody?.filament_overrides).toEqual([
        expect.objectContaining({ slot_id: 1, force_color_match: false }),
      ]);
    });

    it('persists a manually selected printer filament as the enforced profile', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.get('/api/v1/archives/:id/filament-requirements', () =>
          HttpResponse.json({
            filaments: [
              { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, used_meters: 3 },
            ],
          }),
        ),
        http.get('/api/v1/printers/:id/status', () =>
          HttpResponse.json({
            connected: true,
            state: 'IDLE',
            ams: [
              {
                id: 0,
                tray: [
                  { id: 0, tray_type: 'PLA', tray_color: '000000FF', tray_sub_brands: 'PLA Basic' },
                  { id: 1, tray_type: 'PLA', tray_color: 'FFFFFFFF', tray_sub_brands: 'PLA Matte' },
                ],
              },
            ],
            vt_tray: [],
          }),
        ),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const mappingSelect = await waitFor(() => {
        const select = screen
          .getAllByRole('combobox')
          .find((candidate) => candidate.querySelector('option[value="1"]'));
        expect(select).toBeDefined();
        return select!;
      });
      fireEvent.change(mappingSelect, { target: { value: '1' } });
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.filament_overrides).toEqual([
        expect.objectContaining({
          slot_id: 1,
          type: 'PLA',
          color: '#FFFFFF',
          force_color_match: true,
        }),
      ]);
      expect(capturedBody?.ams_mapping).toEqual([1]);
    });
  });

  describe('create mode', () => {
    it('renders the modal title', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('heading', { name: 'Print' })).toBeInTheDocument();
    });

    it('shows archive name', () => {
      render(
        <PrintModal
          mode="create"
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
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('shows queue insertion controls', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByRole('checkbox', { name: /insert at top of queue/i })).toBeInTheDocument();
    });

    it('defaults heat soak off, shows bed-only guidance, and submits explicit settings', async () => {
      let body: Record<string, unknown> | undefined;
      server.use(http.post('/api/v1/queue/', async ({ request }) => {
        body = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ id: 1, status: 'pending' });
      }));
      const user = userEvent.setup();
      render(<PrintModal mode="create" archiveId={1} initialSelectedPrinterIds={[1]} onClose={mockOnClose} />);
      await user.click(screen.getByRole('button', { name: /queue options/i }));
      const toggle = screen.getByRole('checkbox', { name: 'Chamber heat-soak' });
      expect(toggle).not.toBeChecked();
      expect(screen.queryByRole('spinbutton', { name: 'Target temperature (°C)' })).not.toBeInTheDocument();
      await user.click(toggle);
      const temperature = screen.getByRole('spinbutton', { name: 'Target temperature (°C)' });
      const duration = screen.getByRole('spinbutton', { name: 'Soak duration (minutes)' });
      expect(temperature).toHaveValue(60);
      expect(duration).toHaveValue(30);
      const chamberGroup = toggle.closest('label')?.parentElement;
      expect(chamberGroup).not.toBeNull();
      expect(chamberGroup).toContainElement(temperature);
      expect(chamberGroup).toContainElement(duration);
      expect(screen.getByText(/Chamber Heater not available on this machine/)).toBeInTheDocument();
      fireEvent.change(temperature, { target: { value: '55' } });
      fireEvent.change(duration, { target: { value: '15' } });
      await user.click(screen.getByRole('button', { name: /^print$/i }));
      await waitFor(() => expect(body).toMatchObject({ chamber_heat_soak: true, heat_soak_temperature: 55, heat_soak_minutes: 15 }));
    });

    it('restores heat-soak settings when editing and rejects invalid ranges', async () => {
      const user = userEvent.setup();
      render(<PrintModal mode="edit-queue-item" queueItem={createMockQueueItem({ chamber_heat_soak: true, heat_soak_temperature: 50, heat_soak_minutes: 10 })} onClose={mockOnClose} />);
      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByRole('checkbox', { name: 'Chamber heat-soak' })).toBeChecked();
      const temperature = screen.getByRole('spinbutton', { name: 'Target temperature (°C)' });
      expect(temperature).toHaveValue(50);
      expect(screen.getByRole('spinbutton', { name: 'Soak duration (minutes)' })).toHaveValue(10);
      fireEvent.change(temperature, { target: { value: '61' } });
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
      fireEvent.change(temperature, { target: { value: '' } });
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
      fireEvent.change(temperature, { target: { value: '60' } });
      fireEvent.change(screen.getByRole('spinbutton', { name: 'Soak duration (minutes)' }), { target: { value: '0' } });
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
    });

    it('offers an ungated wait-for-drying policy and defaults it off', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      const waitForDrying = screen.getByRole('checkbox', { name: 'Wait for drying to complete' });
      expect(waitForDrying).toBeInTheDocument();
      expect(waitForDrying).not.toBeChecked();
    });

    it('submits the wait-for-drying queue policy', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Wait for drying to complete' }));
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.wait_for_drying_complete).toBe(true);
    });

    it('uses a native time picker with a quarter-hour default when postponing a print', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      const postponePrint = screen.getByRole('checkbox', { name: 'Postpone print' });
      await user.click(postponePrint);
      expect(screen.getByText('Do not start before')).toBeInTheDocument();
      expect(postponePrint.closest('label')?.nextElementSibling).toHaveTextContent('Do not start before');
      const timePicker = screen.getByLabelText('Postpone time');
      expect(timePicker).toHaveAttribute('type', 'time');
      expect(timePicker).toHaveAttribute('step', '60');
      expect((timePicker as HTMLInputElement).value).toMatch(/^\d{2}:(00|15|30|45)$/);
      const calendarTrigger = screen.getByTitle('Open calendar');
      await user.click(calendarTrigger);
      const datePicker = screen.getByRole('dialog', { name: 'Choose date' });
      expect(datePicker).toBeInTheDocument();
      expect(datePicker).toHaveClass('bottom-full', 'z-50');
      expect(within(datePicker).getByRole('grid', { name: 'Choose date' })).toBeInTheDocument();
      expect(within(datePicker).getAllByRole('columnheader')).toHaveLength(7);
      const previousMonth = within(datePicker).getByRole('button', { name: 'Previous month' });
      expect(previousMonth).toBeInTheDocument();
      expect(within(datePicker).getByRole('button', { name: 'Next month' })).toBeInTheDocument();
      expect(previousMonth).toHaveFocus();
      const save = within(datePicker).getByRole('button', { name: 'Save' });
      save.focus();
      await user.tab();
      expect(previousMonth).toHaveFocus();
      await user.tab({ shift: true });
      expect(save).toHaveFocus();
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog', { name: 'Choose date' })).not.toBeInTheDocument();
      expect(calendarTrigger).toHaveFocus();
    });

    it('applies a date selected in the postpone calendar', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      const dateInput = screen.getByLabelText('Do not start before') as HTMLInputElement;
      await waitFor(() => expect(dateInput.value).not.toBe(''));
      const selectedDay = String(parseDateInput(dateInput.value, 'system')!.getDate());

      await user.click(screen.getByTitle('Open calendar'));
      const datePicker = screen.getByRole('dialog', { name: 'Choose date' });
      const selectedDayButton = within(datePicker).getAllByRole('button').find((button) => button.textContent === selectedDay);
      expect(selectedDayButton).toBeDefined();
      await user.click(selectedDayButton!);
      await user.click(within(datePicker).getByRole('button', { name: 'Save' }));

      expect(screen.queryByRole('dialog', { name: 'Choose date' })).not.toBeInTheDocument();
      expect(dateInput).not.toHaveValue('');
    });

    it('prevents a postponed print from using a past start time', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      fireEvent.change(screen.getByLabelText('Postpone time'), { target: { value: '00:00' } });

      expect(screen.getByText('Choose a future date and time')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^print$/i })).toBeDisabled();
    });

    it('clears the scheduled value when the postpone date becomes invalid', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      const dateInput = screen.getByLabelText('Do not start before');
      expect(dateInput).not.toHaveValue('');

      await user.clear(dateInput);

      expect(screen.getByText('Please enter a valid date and time')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^print$/i })).toBeDisabled();
    });

    it('allows a postponed print more than six months away', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      const dateInput = screen.getByLabelText('Do not start before');
      await waitFor(() => expect(dateInput).not.toHaveValue(''));
      const longFutureDate = new Date(2099, 11, 31, 9, 30, 0, 0);
      fireEvent.change(dateInput, { target: { value: formatDateInput(longFutureDate, 'system') } });
      fireEvent.change(screen.getByLabelText('Postpone time'), { target: { value: '09:30' } });

      expect(screen.queryByText('Choose a date within six months')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^print$/i })).toBeEnabled();
    });

    it('only shows power off when the selected printer has a smart socket', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.queryByText(/power off/i)).not.toBeInTheDocument();
    });

    it('shows power off for a selected printer with a smart socket', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await waitFor(() => {
        expect(screen.getByText(/power off/i)).toBeInTheDocument();
      });
    });

    it('removes the queue, ASAP, and schedule switcher', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.queryByRole('button', { name: /^(asap|queue|schedule)$/i })).not.toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByRole('checkbox', { name: /insert at top of queue/i })).not.toBeChecked();
    });

    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
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

    it('preserves a long-future scheduled time when saving an existing queue item', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.patch('/api/v1/queue/:id', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();
      const scheduledTime = new Date(2099, 11, 31, 9, 30, 0, 0);
      const item = createMockQueueItem({ scheduled_time: scheduledTime.toISOString() });

      render(<PrintModal mode="edit-queue-item" archiveId={1} archiveName="Test Print" queueItem={item} onClose={mockOnClose} />);

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByRole('checkbox', { name: 'Postpone print' })).toBeChecked();
      await user.click(screen.getByRole('button', { name: /^save$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: 1, target_model: null });
      expect(capturedBody?.scheduled_time).toBe(scheduledTime.toISOString());
    });

    it('preserves an existing model assignment when editing a queue item', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.patch('/api/v1/queue/:id', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();
      const item = createMockQueueItem({ printer_id: null, target_model: 'P1S', target_location: null });

      render(<PrintModal mode="edit-queue-item" archiveId={1} archiveName="Test Print" queueItem={item} onClose={mockOnClose} />);

      await user.click(screen.getByRole('button', { name: /^save$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody).toMatchObject({ printer_id: null, target_model: 'P1S' });
    });

    it('clears a scheduled time when postponing is disabled during edit', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.patch('/api/v1/queue/:id', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        }),
      );
      const user = userEvent.setup();
      const scheduledTime = new Date(2099, 11, 31, 9, 30, 0, 0);
      const item = createMockQueueItem({ scheduled_time: scheduledTime.toISOString() });

      render(<PrintModal mode="edit-queue-item" archiveId={1} archiveName="Test Print" queueItem={item} onClose={mockOnClose} />);

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: 'Postpone print' }));
      await user.click(screen.getByRole('button', { name: /^save$/i }));

      await waitFor(() => expect(capturedBody).not.toBeNull());
      expect(capturedBody?.scheduled_time).toBeNull();
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

    it('shows queue insertion controls', async () => {
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

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByRole('checkbox', { name: /insert at top of queue/i })).toBeInTheDocument();
    });

    it('shows power off option', async () => {
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

      await user.click(screen.getByRole('button', { name: /queue options/i }));
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
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });
    });

    it('shows selected count when multiple printers selected', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
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
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });
    });

    it('persists each custom mapping and exposes one match policy for all selected printers', async () => {
      const capturedBodies: Array<Record<string, unknown>> = [];
      server.use(
        http.get('/api/v1/archives/:id/filament-requirements', () =>
          HttpResponse.json({
            filaments: [
              { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, used_meters: 3 },
            ],
          }),
        ),
        http.get('/api/v1/printers/:id/status', ({ params }) => {
          const printerId = Number(params.id);
          return HttpResponse.json({
            connected: true,
            state: 'IDLE',
            ams: [
              {
                id: 0,
                tray: printerId === 1
                  ? [
                      { id: 0, tray_type: 'PLA', tray_color: '000000FF', tray_sub_brands: 'PLA Basic' },
                      { id: 1, tray_type: 'PLA', tray_color: 'FFFFFFFF', tray_sub_brands: 'PLA Matte' },
                      { id: 2, tray_type: 'ABS', tray_color: 'FF0000FF', tray_sub_brands: 'ABS' },
                    ]
                  : [
                      { id: 0, tray_type: 'PLA', tray_color: '000000FF', tray_sub_brands: 'PLA Basic' },
                    ],
              },
            ],
            vt_tray: [],
          });
        }),
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({ id: capturedBodies.length, status: 'pending' });
        }),
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await user.click((await screen.findByText('X1 Carbon')).closest('button')!);
      await user.click((await screen.findByText('P1S')).closest('button')!);
      const customMapping = await screen.findAllByLabelText('Customise this printer');
      await user.click(customMapping[0]);
      const mappingSelect = await waitFor(() => {
        const select = screen
          .getAllByRole('combobox')
          .find((candidate) => candidate.querySelector('option[value="1"]'));
        expect(select).toBeDefined();
        return select!;
      });
      expect(mappingSelect.querySelector('option[value="2"]')).not.toBeInTheDocument();
      fireEvent.change(mappingSelect, { target: { value: '1' } });
      const matchPolicy = screen.getByLabelText(/Match colour/i);
      expect(matchPolicy).toBeChecked();
      await user.click(matchPolicy);
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => expect(capturedBodies).toHaveLength(2));
      const printerOne = capturedBodies.find((body) => body.printer_id === 1);
      const printerTwo = capturedBodies.find((body) => body.printer_id === 2);
      expect(printerOne?.filament_overrides).toEqual([
        expect.objectContaining({ type: 'PLA', color: '#FFFFFF', force_color_match: false }),
      ]);
      expect(printerOne?.force_color_match).toBe(false);
      expect(printerOne?.ams_mapping).toEqual([1]);
      expect(printerTwo?.filament_overrides).toEqual([
        expect.objectContaining({ type: 'PLA', color: '#000000', force_color_match: false }),
      ]);
      expect(printerTwo?.force_color_match).toBe(false);
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

    it('shows state badges on printers in create mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
        expect(screen.getByText('Idle')).toBeInTheDocument();
        expect(screen.getByText('Finished')).toBeInTheDocument();
      }, { timeout: 5000 });
    });

    it('allows selecting a busy printer in create mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
      }, { timeout: 5000 });

      const busyButton = screen.getByText('X1 Carbon').closest('button');
      expect(busyButton).not.toBeDisabled();
      await user.click(busyButton!);

      await waitFor(() => {
        expect(screen.getByText('1 printer selected')).toBeInTheDocument();
      });
    });

    it('select all includes busy printers in create mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
        expect(screen.getByText('Printing')).toBeInTheDocument();
      }, { timeout: 5000 });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        expect(screen.getByText(/3 printers selected/)).toBeInTheDocument();
      });
    });

    it('allows selecting busy printers in create mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
      }, { timeout: 5000 });

      // The busy printer button should NOT be disabled in queue mode
      const busyButton = screen.getByText('X1 Carbon').closest('button');
      expect(busyButton).not.toBeDisabled();

      await user.click(busyButton!);

      await waitFor(() => {
        expect(screen.getByText('1 printer selected')).toBeInTheDocument();
      });
    });

    it('shows Offline badge for disconnected printers', async () => {
      const user = userEvent.setup();
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
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        const offlineBadges = screen.getAllByText('Offline');
        expect(offlineBadges.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('shows calibration stage name when printer is calibrating', async () => {
      const user = userEvent.setup();
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
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        const badges = screen.getAllByText('Auto bed leveling');
        expect(badges.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  describe('removed stagger scheduling controls', () => {
    it('does not show stagger option with single printer in queue mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Select single printer
      await user.click(screen.getByText('X1 Carbon'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
    });

    it('does not show stagger option when multiple printers are selected', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
    });

    it('does not show stagger option in create mode with multiple printers', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      await waitFor(() => {
        expect(screen.getByText(/2 printers selected|3 printers selected/)).toBeInTheDocument();
      });

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
    });

    it('does not show stagger preview controls in create mode', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
      expect(screen.queryByText(/printers.*groups/)).not.toBeInTheDocument();
    });

    it('does not show stagger option in create mode with single printer', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Select only one printer
      await user.click(screen.getByText('X1 Carbon'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
    });

    it('does not show stagger input controls', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
      expect(screen.queryByText('Group size')).not.toBeInTheDocument();
      expect(screen.queryByText('Interval (min)')).not.toBeInTheDocument();
    });

    it('does not show stagger preview after selecting all printers', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      await waitFor(() => {
        expect(screen.getByText('Select all')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select all'));

      expect(screen.queryByText('Stagger printer starts')).not.toBeInTheDocument();
      expect(screen.queryByText(/3 printers.*2 groups/)).not.toBeInTheDocument();
    });
  });

  describe('multi-plate selection', () => {
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

    it('shows "Select All" button only in create mode', async () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select All 3 Plates')).toBeInTheDocument();
      });
    });

    it('shows "Select All" button in create mode', async () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plate 1')).toBeInTheDocument();
      });
      expect(screen.getByText('Select All 3 Plates')).toBeInTheDocument();
    });

    it('selects all plates when "Select All" is clicked', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select All 3 Plates')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select All 3 Plates'));

      // All plates should be highlighted (green border)
      await waitFor(() => {
        const plateButtons = document.querySelectorAll('button[type="button"].border-bambu-green');
        // 3 plate buttons + the "Deselect All" toggle button = 4 green-bordered buttons
        expect(plateButtons.length).toBeGreaterThanOrEqual(3);
      });
    });

    it('allows selecting a subset of plates to queue', async () => {
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
          mode="create"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      // Wait for plates and select a printer
      await waitFor(() => {
        expect(screen.getByText('Select All 3 Plates')).toBeInTheDocument();
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Select printer
      await user.click(screen.getByText('X1 Carbon'));

      // Plate 1 is auto-selected. Click Plate 3 to add it (multi-select in create mode)
      await user.click(screen.getByText('Plate 3'));

      // Submit — should queue plates 1 and 3
      const submitButton = document.querySelector('button[type="submit"]') as HTMLElement;
      await user.click(submitButton);

      await waitFor(() => {
        expect(queueRequests.length).toBe(2);
      });

      expect((queueRequests[0] as { plate_id: number }).plate_id).toBe(1);
      expect((queueRequests[1] as { plate_id: number }).plate_id).toBe(3);
    });

    it('creates one queue item per plate when submitting with select-all', async () => {
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
          mode="create"
          archiveId={1}
          archiveName="MultiPlate.3mf"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(await screen.findByRole('button', { name: 'Specific Printer' }));
      // Wait for plates and select a printer
      await waitFor(() => {
        expect(screen.getByText('Select All 3 Plates')).toBeInTheDocument();
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Select printer
      await user.click(screen.getByText('X1 Carbon'));

      // Click select all
      await user.click(screen.getByText('Select All 3 Plates'));

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

  describe('batch quantity', () => {
    it('shows quantity input in create mode', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByLabelText('Quantity')).toBeInTheDocument();
    });

    it('shows quantity input in create mode', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByLabelText('Quantity')).toBeInTheDocument();
    });

    it('does not show quantity input in edit-queue-item mode', () => {
      render(
        <PrintModal
          mode="edit-queue-item"
          archiveId={1}
          archiveName="Benchy"
          queueItem={createMockQueueItem()}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.queryByLabelText('Quantity')).not.toBeInTheDocument();
    });

    it('defaults quantity to 1', () => {
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const input = screen.getByLabelText('Quantity') as HTMLInputElement;
      expect(input.value).toBe('1');
    });

    it('quantity input has default value of 1 and accepts changes', async () => {
      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const input = screen.getByLabelText('Quantity') as HTMLInputElement;
      expect(input.value).toBe('1');

      await user.tripleClick(input);
      await user.keyboard('5');
      expect(input.value).toBe('5');
    });
  });

  describe('reprint G-code injection dispatch (#422 / auto-eject)', () => {
    // Guards the fix: when "Inject auto-print G-code" is ticked on a create with
    // quantity > 1, ALL copies must go through the queue so every one is injected by
    // the scheduler. The first copy must NOT be dispatched immediately via the direct
    // create path — that path bypasses injection and would leave the first copy stuck
    // on the plate for auto-eject setups.
    const withSnippets = () =>
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ gcode_snippets: { X1C: { start_gcode: '', end_gcode: 'M400' } } }),
      );

    it('injection ON queues all copies and dispatches none immediately', async () => {
      const queueCalls: Record<string, unknown>[] = [];
      server.use(
        withSnippets(),
        http.post('/api/v1/queue/', async ({ request }) => {
          queueCalls.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({ id: queueCalls.length, status: 'pending' });
        }),
      );

      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      // Quantity > 1 so the injection checkbox is offered
      const qty = (await screen.findByLabelText('Quantity')) as HTMLInputElement;
      await user.tripleClick(qty);
      await user.keyboard('3');
      expect(qty.value).toBe('3');

      // Checkbox only renders once snippets are loaded AND quantity > 1
      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(await screen.findByLabelText(/inject auto-print/i));

      await user.click(document.querySelector('button[type="submit"]') as HTMLElement);

      await waitFor(() => expect(queueCalls.length).toBe(1));
      // One queue item carrying all copies, and zero immediate reprint dispatches
      expect(queueCalls[0].quantity).toBe(3);
    });

    it('injection OFF queues all copies through the scheduler path', async () => {
      const queueCalls: Record<string, unknown>[] = [];
      server.use(
        withSnippets(),
        http.post('/api/v1/queue/', async ({ request }) => {
          queueCalls.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({ id: queueCalls.length, status: 'pending' });
        }),
      );

      const user = userEvent.setup();
      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const qty = (await screen.findByLabelText('Quantity')) as HTMLInputElement;
      await user.tripleClick(qty);
      await user.keyboard('3');
      expect(qty.value).toBe('3');

      // Leave injection unticked: unified dispatch still queues all copies.
      await user.click(document.querySelector('button[type="submit"]') as HTMLElement);

      await waitFor(() => expect(queueCalls.length).toBe(1));
      expect(queueCalls[0].quantity).toBe(3);
    });
  });

  describe('project_id forwarding', () => {
    beforeEach(() => {
      // Additional handlers needed for library file mode
      server.use(
        http.get('/api/v1/library/files/:id', () => {
          return HttpResponse.json({
            id: 5,
            filename: 'benchy.gcode.3mf',
            print_name: null,
            file_type: '3mf',
            folder_id: null,
            project_id: null,
            file_hash: null,
            file_size_bytes: 1024,
            thumbnail_path: null,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
          });
        }),
        http.get('/api/v1/library/files/:id/plates', () => {
          return HttpResponse.json({ is_multi_plate: false, plates: [] });
        }),
        http.get('/api/v1/library/files/:id/filament-requirements', () => {
          return HttpResponse.json({ file_id: 5, filename: 'benchy.gcode.3mf', filaments: [] });
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] });
        }),
      );
    });

    it('includes project_id in queue item when printing a library file with projectId set', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          libraryFileId={5}
          archiveName="Benchy"
          projectId={42}
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      // Wait for the modal to load printer and file data
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
        expect(capturedBody?.library_file_id).toBe(5);
        expect(capturedBody?.project_id).toBe(42);
      });
    });

    it('queues archive prints through the scheduler path', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          projectId={42}
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
        expect(capturedBody?.archive_id).toBe(1);
        expect(capturedBody?.project_id).toBe(42);
      });
    });

    it('adds prints to the top of the queue when selected', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      await user.click(screen.getByRole('checkbox', { name: /insert at top of queue/i }));
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
        expect(capturedBody?.insert_at_top).toBe(true);
        expect(capturedBody?.insert_position).toBe(1);
        expect(capturedBody?.manual_start).toBe(false);
        expect(capturedBody?.scheduled_time).toBeUndefined();
      });
    });

    it('adds prints to the back by default and supports manual start', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          archiveId={1}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await user.click(screen.getByRole('button', { name: /queue options/i }));
      expect(screen.getByLabelText(/require manual start/i)).toBeInTheDocument();
      await user.click(screen.getByLabelText(/require manual start/i));
      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
        expect(capturedBody?.insert_at_top).toBeUndefined();
        expect(capturedBody?.insert_position).toBeUndefined();
        expect(capturedBody?.manual_start).toBe(true);
      });
    });
  });

  describe('cleanup_library_after_dispatch forwarding (#730)', () => {
    // The Printers-page Direct-Print flow passes cleanupLibraryAfterDispatch so the
    // transient LibraryFile created by FileUploadModal is deleted once the archive
    // owns its own copy. File Manager / Project Detail flows leave the prop unset so
    // their deliberately-added library entries survive the print.
    beforeEach(() => {
      server.use(
        http.get('/api/v1/library/files/:id', () => {
          return HttpResponse.json({
            id: 5,
            filename: 'benchy.gcode.3mf',
            file_type: '3mf',
            folder_id: null,
            project_id: null,
            file_hash: null,
            file_size_bytes: 1024,
            thumbnail_path: null,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
          });
        }),
        http.get('/api/v1/library/files/:id/plates', () => {
          return HttpResponse.json({ is_multi_plate: false, plates: [] });
        }),
        http.get('/api/v1/library/files/:id/filament-requirements', () => {
          return HttpResponse.json({ file_id: 5, filename: 'benchy.gcode.3mf', filaments: [] });
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] });
        }),
      );
    });

    it('forwards cleanup_library_after_dispatch=true when the Direct-Print prop is set', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          libraryFileId={5}
          archiveName="Benchy"
          cleanupLibraryAfterDispatch
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
        expect(capturedBody?.cleanup_library_after_dispatch).toBe(true);
      });
    });

    it('defaults to omitting cleanup_library_after_dispatch (File Manager / Project flows survive)', async () => {
      let capturedBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/v1/queue/', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ id: 1, status: 'pending' });
        })
      );
      const user = userEvent.setup();

      render(
        <PrintModal
          mode="create"
          libraryFileId={5}
          archiveName="Benchy"
          initialSelectedPrinterIds={[1]}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^print$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^print$/i }));

      await waitFor(() => {
        expect(capturedBody).not.toBeNull();
      });
      // Either omitted entirely or explicitly undefined — both interpret as "keep file"
      expect(capturedBody?.cleanup_library_after_dispatch).toBeUndefined();
    });
  });
});
