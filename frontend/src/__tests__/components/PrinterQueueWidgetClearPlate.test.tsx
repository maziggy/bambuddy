/**
 * Tests for PrinterQueueWidget queue-card behavior.
 *
 * The queued clear-plate action lives on the printer card, not in this widget.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { PrinterQueueWidget } from '../../components/PrinterQueueWidget';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockQueueItems = [
  {
    id: 1,
    printer_id: 1,
    archive_id: 1,
    position: 1,
    status: 'pending',
    archive_name: 'First Print',
    printer_name: 'X1 Carbon',
    print_time_seconds: 3600,
    scheduled_time: null,
  },
  {
    id: 2,
    printer_id: 1,
    archive_id: 2,
    position: 2,
    status: 'pending',
    archive_name: 'Second Print',
    printer_name: 'X1 Carbon',
    print_time_seconds: 7200,
    scheduled_time: null,
  },
];

describe('PrinterQueueWidget - Clear Plate', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/queue/', ({ request }) => {
        const url = new URL(request.url);
        const printerId = url.searchParams.get('printer_id');
        if (printerId === '1') {
          return HttpResponse.json(mockQueueItems);
        }
        return HttpResponse.json([]);
      }),
      http.post('/api/v1/printers/:id/clear-plate', () => {
        return HttpResponse.json({ success: true, message: 'Plate cleared' });
      })
    );
  });

  describe('queued clear plate button visibility', () => {
    it('shows passive link when printer state is FINISH', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when printer state is FAILED', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when printer state is IDLE', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="IDLE" />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when printer state is RUNNING', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="RUNNING" />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
    });

    it('shows passive link when printerState is not provided', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
    });

    it('shows passive link when FINISH but plateCleared is true', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={false} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when FAILED but plateCleared is true', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" awaitingPlateClear={false} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link in IDLE state when awaitingPlateClear is true (#961)', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="IDLE" awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link with no printerState when awaitingPlateClear is true', async () => {
      render(<PrinterQueueWidget printerId={1} awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });
  });

  describe('clear plate button shows queue info', () => {
    it('shows next item name in clear plate mode', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} />);

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
    });

    it('shows additional items badge in clear plate mode', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} />);

      await waitFor(() => {
        expect(screen.getByText('+1')).toBeInTheDocument();
      });
    });
  });

  describe('empty queue', () => {
    it('renders nothing in FINISH state with no queue items', async () => {
      const { container } = render(<PrinterQueueWidget printerId={999} printerState="FINISH" awaitingPlateClear={true} />);

      await waitFor(() => {
        expect(container.querySelector('a')).not.toBeInTheDocument();
      });
    });
  });

  describe('filament compatibility filtering', () => {
    const petgQueueItems = [
      {
        id: 10,
        printer_id: 1,
        archive_id: 10,
        position: 1,
        status: 'pending',
        archive_name: 'PETG Print',
        printer_name: 'H2S',
        print_time_seconds: 3600,
        scheduled_time: null,
        required_filament_types: ['PETG'],
      },
    ];

    it('hides widget when queue item requires filament not loaded on printer', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(petgQueueItems))
      );

      const { container } = render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PLA'])}
        />
      );

      // Wait for query to settle, then confirm widget is not rendered
      await waitFor(() => {
        expect(container.querySelector('a')).not.toBeInTheDocument();
      });
      expect(screen.queryByText('PETG Print')).not.toBeInTheDocument();
    });

    it('shows widget when queue item required filaments match loaded', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(petgQueueItems))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          requirePlateClear={true}
          loadedFilamentTypes={new Set(['PLA', 'PETG'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('PETG Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows widget when queue item has no required_filament_types', async () => {
      // Default mockQueueItems have no required_filament_types
      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          requirePlateClear={true}
          loadedFilamentTypes={new Set(['PLA'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows widget when loadedFilamentTypes prop is not provided', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(petgQueueItems))
      );

      render(
        <PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={true} />
      );

      await waitFor(() => {
        expect(screen.getByText('PETG Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('skips incompatible first item and shows compatible second item', async () => {
      const mixedQueue = [
        {
          id: 10,
          printer_id: 1,
          archive_id: 10,
          position: 1,
          status: 'pending',
          archive_name: 'PETG Print',
          printer_name: 'H2S',
          print_time_seconds: 3600,
          scheduled_time: null,
          required_filament_types: ['PETG'],
        },
        {
          id: 11,
          printer_id: 1,
          archive_id: 11,
          position: 2,
          status: 'pending',
          archive_name: 'PLA Print',
          printer_name: 'H2S',
          print_time_seconds: 1800,
          scheduled_time: null,
          required_filament_types: ['PLA'],
        },
      ];

      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(mixedQueue))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PLA'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('PLA Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('PETG Print')).not.toBeInTheDocument();
    });

    it('matches filament types case-insensitively', async () => {
      const lowercaseQueue = [
        {
          id: 10,
          printer_id: 1,
          archive_id: 10,
          position: 1,
          status: 'pending',
          archive_name: 'Petg Print',
          printer_name: 'H2S',
          print_time_seconds: 3600,
          scheduled_time: null,
          required_filament_types: ['petg'],
        },
      ];

      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(lowercaseQueue))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          requirePlateClear={true}
          loadedFilamentTypes={new Set(['PETG'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Petg Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });
  });

  describe('filament override color filtering', () => {
    const whitePetgOverrideItem = [
      {
        id: 20,
        printer_id: null,
        archive_id: 20,
        position: 1,
        status: 'pending',
        archive_name: 'White PETG Print',
        printer_name: null,
        print_time_seconds: 3600,
        scheduled_time: null,
        required_filament_types: ['PETG'],
        filament_overrides: [{ slot_id: 1, type: 'PETG', color: '#FFFFFF' }],
      },
    ];

    it('hides widget when override color does not match loaded filaments', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(whitePetgOverrideItem))
      );

      const { container } = render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PETG'])}
          loadedFilaments={new Set(['PETG:0000ff'])}
        />
      );

      await waitFor(() => {
        expect(container.querySelector('a')).not.toBeInTheDocument();
      });
      expect(screen.queryByText('White PETG Print')).not.toBeInTheDocument();
    });

    it('shows widget when override color matches loaded filaments', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(whitePetgOverrideItem))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          requirePlateClear={true}
          loadedFilamentTypes={new Set(['PETG'])}
          loadedFilaments={new Set(['PETG:ffffff'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('White PETG Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('normalizes override color format (strips # and lowercases)', async () => {
      const upperCaseColorItem = [
        {
          id: 21,
          printer_id: null,
          archive_id: 21,
          position: 1,
          status: 'pending',
          archive_name: 'Red PLA Print',
          printer_name: null,
          print_time_seconds: 3600,
          scheduled_time: null,
          required_filament_types: ['PLA'],
          filament_overrides: [{ slot_id: 1, type: 'PLA', color: '#FF0000' }],
        },
      ];

      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(upperCaseColorItem))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PLA'])}
          loadedFilaments={new Set(['PLA:ff0000'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Red PLA Print')).toBeInTheDocument();
      });
    });

    it('shows widget when no loadedFilaments prop is provided (no color filtering)', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(whitePetgOverrideItem))
      );

      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PETG'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('White PETG Print')).toBeInTheDocument();
      });
    });

    it('shows widget when queue item has no filament overrides', async () => {
      // Default mockQueueItems have no filament_overrides
      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilaments={new Set(['PLA:000000'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
    });

    it('matches any override when multiple overrides exist', async () => {
      const multiOverrideItem = [
        {
          id: 22,
          printer_id: null,
          archive_id: 22,
          position: 1,
          status: 'pending',
          archive_name: 'Multi Color Print',
          printer_name: null,
          print_time_seconds: 3600,
          scheduled_time: null,
          required_filament_types: ['PLA'],
          filament_overrides: [
            { slot_id: 1, type: 'PLA', color: '#FF0000' },
            { slot_id: 2, type: 'PLA', color: '#00FF00' },
          ],
        },
      ];

      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(multiOverrideItem))
      );

      // Printer has green PLA but not red — should still match (at least one override)
      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          awaitingPlateClear={true}
          loadedFilamentTypes={new Set(['PLA'])}
          loadedFilaments={new Set(['PLA:00ff00'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Multi Color Print')).toBeInTheDocument();
      });
    });
  });

  describe('requirePlateClear setting', () => {
    it('shows passive link when requirePlateClear is false even in FINISH state', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={false} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when requirePlateClear is false even in FAILED state', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" awaitingPlateClear={true} requirePlateClear={false} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when requirePlateClear is true (explicit)', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when requirePlateClear is not provided (defaults to false)', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('still shows next item info in passive link when requirePlateClear is false', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={false} />);

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
    });
  });

  describe('staged (manual_start) items', () => {
    const stagedItems = [
      { id: 10, printer_id: 1, archive_id: 1, position: 1, status: 'pending', archive_name: 'Staged Print 1', manual_start: true, scheduled_time: null },
      { id: 11, printer_id: 1, archive_id: 2, position: 2, status: 'pending', archive_name: 'Staged Print 2', manual_start: true, scheduled_time: null },
    ];

    it('does not show clear plate button when all items are staged', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(stagedItems)),
      );

      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} />);

      // Should show the passive link (not the clear plate button)
      await waitFor(() => {
        expect(screen.getByText('Staged Print 1')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when mix of staged and auto-dispatch items', async () => {
      const mixedItems = [
        { id: 10, printer_id: 1, archive_id: 1, position: 1, status: 'pending', archive_name: 'Staged Print', manual_start: true, scheduled_time: null },
        { id: 11, printer_id: 1, archive_id: 2, position: 2, status: 'pending', archive_name: 'Auto Print', manual_start: false, scheduled_time: null },
      ];
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(mixedItems)),
      );

      render(<PrinterQueueWidget printerId={1} printerState="FINISH" awaitingPlateClear={true} requirePlateClear={true} />);

      await waitFor(() => {
        expect(screen.getByText('Staged Print')).toBeInTheDocument();
      });
      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });
  });
});
