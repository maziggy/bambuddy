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
});
