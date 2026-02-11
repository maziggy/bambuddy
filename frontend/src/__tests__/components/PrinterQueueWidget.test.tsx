/**
 * Tests for the PrinterQueueWidget component.
 *
 * This is a compact widget that shows "Next in queue" with the first pending
 * item's name and a "+N" badge if there are more items. Returns null when empty.
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

describe('PrinterQueueWidget', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/queue/', ({ request }) => {
        const url = new URL(request.url);
        const printerId = url.searchParams.get('printer_id');
        if (printerId === '1') {
          return HttpResponse.json(mockQueueItems);
        }
        return HttpResponse.json([]);
      })
    );
  });

  describe('rendering', () => {
    it('shows next in queue label', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('Next in queue')).toBeInTheDocument();
      });
    });

    it('shows first pending item name', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });
    });

    it('shows additional items badge when multiple pending', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        // Shows "+1" badge since there are 2 items
        expect(screen.getByText('+1')).toBeInTheDocument();
      });
    });

    it('shows ASAP for unscheduled items', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('ASAP')).toBeInTheDocument();
      });
    });
  });

  describe('empty state', () => {
    it('renders nothing when no pending items', async () => {
      const { container } = render(<PrinterQueueWidget printerId={999} />);

      // Wait for query to resolve
      await waitFor(() => {
        // Widget returns null when empty, so container should have no visible widget
        expect(container.querySelector('a[href="/queue"]')).not.toBeInTheDocument();
      });
    });
  });

  describe('single item', () => {
    it('does not show badge when only one item', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([mockQueueItems[0]]);
        })
      );

      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('First Print')).toBeInTheDocument();
      });

      // Should not have a "+N" badge
      expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
    });
  });

  describe('link behavior', () => {
    it('links to queue page', async () => {
      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        const link = screen.getByRole('link');
        expect(link).toHaveAttribute('href', '/queue');
      });
    });
  });

  describe('file manager (library file) items', () => {
    it('shows library_file_name for items from file manager', async () => {
      const libraryFileItem = {
        id: 3,
        printer_id: 1,
        library_file_id: 5,
        archive_id: null,
        position: 1,
        status: 'pending',
        library_file_name: 'MyModel.3mf',
        printer_name: 'X1 Carbon',
        print_time_seconds: 3600,
        scheduled_time: null,
      };

      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([libraryFileItem]);
        })
      );

      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('MyModel.3mf')).toBeInTheDocument();
      });
    });

    it('shows library_file_name even when archive_name is null', async () => {
      const libraryFileItem = {
        id: 3,
        printer_id: 1,
        library_file_id: 5,
        archive_id: null,
        position: 1,
        status: 'pending',
        archive_name: null,
        library_file_name: 'TestPrint.3mf',
        printer_name: 'X1 Carbon',
        print_time_seconds: 3600,
        scheduled_time: null,
      };

      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([libraryFileItem]);
        })
      );

      render(<PrinterQueueWidget printerId={1} />);

      await waitFor(() => {
        expect(screen.getByText('TestPrint.3mf')).toBeInTheDocument();
        // Should NOT show "Archive #null"
        expect(screen.queryByText('Archive #null')).not.toBeInTheDocument();
      });
    });
  });
});
