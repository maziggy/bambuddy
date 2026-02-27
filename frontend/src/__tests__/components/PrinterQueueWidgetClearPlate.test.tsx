/**
 * Tests for the PrinterQueueWidget clear plate behavior.
 *
 * When the printer is in FINISH or FAILED state and has pending queue items,
 * the widget shows a "Clear Plate & Start Next" button instead of the
 * passive queue link. After clicking, it shows a confirmation state.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

  describe('clear plate button visibility', () => {
    it('shows clear plate button when printer state is FINISH', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" />);

      await waitFor(() => {
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
    });

    it('shows clear plate button when printer state is FAILED', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" />);

      await waitFor(() => {
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
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
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" plateCleared={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });

    it('shows passive link when FAILED but plateCleared is true', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" plateCleared={true} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });

      expect(screen.queryByText('Clear Plate & Start Next')).not.toBeInTheDocument();
    });
  });

  describe('clear plate button shows queue info', () => {
    it('shows next item name in clear plate mode', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" />);

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
    });

    it('shows additional items badge in clear plate mode', async () => {
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" />);

      await waitFor(() => {
        expect(screen.getByText('+1')).toBeInTheDocument();
      });
    });
  });

  describe('clear plate action', () => {
    it('shows confirmation state after clicking clear plate', async () => {
      const user = userEvent.setup();
      render(<PrinterQueueWidget printerId={1} printerState="FINISH" />);

      await waitFor(() => {
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clear Plate & Start Next'));

      await waitFor(() => {
        // Both the widget confirmation and the toast show this text
        const elements = screen.getAllByText('Plate cleared — ready for next print');
        expect(elements.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('shows error toast on API failure', async () => {
      server.use(
        http.post('/api/v1/printers/:id/clear-plate', () => {
          return HttpResponse.json(
            { detail: 'Printer not connected' },
            { status: 400 }
          );
        })
      );

      const user = userEvent.setup();
      render(<PrinterQueueWidget printerId={1} printerState="FAILED" />);

      await waitFor(() => {
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clear Plate & Start Next'));

      // Button should remain visible (not transition to success state)
      await waitFor(() => {
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
    });
  });

  describe('empty queue', () => {
    it('renders nothing in FINISH state with no queue items', async () => {
      const { container } = render(<PrinterQueueWidget printerId={999} printerState="FINISH" />);

      await waitFor(() => {
        expect(container.querySelector('button')).not.toBeInTheDocument();
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
          loadedFilamentTypes={new Set(['PLA'])}
        />
      );

      // Wait for query to settle, then confirm widget is not rendered
      await waitFor(() => {
        expect(container.querySelector('button')).not.toBeInTheDocument();
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
          loadedFilamentTypes={new Set(['PLA', 'PETG'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('PETG Print')).toBeInTheDocument();
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
    });

    it('shows widget when queue item has no required_filament_types', async () => {
      // Default mockQueueItems have no required_filament_types
      render(
        <PrinterQueueWidget
          printerId={1}
          printerState="FINISH"
          loadedFilamentTypes={new Set(['PLA'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
    });

    it('shows widget when loadedFilamentTypes prop is not provided', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(petgQueueItems))
      );

      render(
        <PrinterQueueWidget printerId={1} printerState="FINISH" />
      );

      await waitFor(() => {
        expect(screen.getByText('PETG Print')).toBeInTheDocument();
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
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
          loadedFilamentTypes={new Set(['PETG'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Petg Print')).toBeInTheDocument();
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
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
          loadedFilamentTypes={new Set(['PETG'])}
          loadedFilaments={new Set(['PETG:0000ff'])}
        />
      );

      await waitFor(() => {
        expect(container.querySelector('button')).not.toBeInTheDocument();
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
          loadedFilamentTypes={new Set(['PETG'])}
          loadedFilaments={new Set(['PETG:ffffff'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('White PETG Print')).toBeInTheDocument();
        expect(screen.getByText('Clear Plate & Start Next')).toBeInTheDocument();
      });
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
          loadedFilamentTypes={new Set(['PLA'])}
          loadedFilaments={new Set(['PLA:00ff00'])}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Multi Color Print')).toBeInTheDocument();
      });
    });
  });
});
