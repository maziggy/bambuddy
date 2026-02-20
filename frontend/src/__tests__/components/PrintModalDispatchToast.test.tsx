/**
 * Test that reprint mode does not show the "Print queued for printer" toast.
 * The background dispatch websocket toast handles feedback instead.
 *
 * Separate file because vi.mock(ToastContext) must be module-scoped
 * and would interfere with the main PrintModal test suite.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock the toast context before importing the component
const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

import { render } from '../utils';
import { PrintModal } from '../../components/PrintModal';

const mockPrinters = [
  { id: 1, name: 'X1 Carbon', model: 'X1C', ip_address: '192.168.1.100', enabled: true, is_active: true },
];

describe('PrintModal dispatch toast', () => {
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
        return HttpResponse.json({ status: 'dispatched', dispatch_job_id: 1 });
      }),
    );
  });

  it('does not show "queued" toast in reprint mode (dispatch toast handles it)', async () => {
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

    // Wait for printers to load, then select one
    await waitFor(() => {
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });
    await user.click(screen.getByText('X1 Carbon'));

    // Submit the print
    const printButton = screen.getByRole('button', { name: /^print$/i });
    await user.click(printButton);

    // Wait for the API call to complete and modal to close
    await waitFor(() => {
      expect(mockOnClose).toHaveBeenCalled();
    });

    // showToast should NOT have been called with "Print queued for printer"
    const toastMessages = mockShowToast.mock.calls.map(call => call[0]);
    expect(toastMessages).not.toContain('Print queued for printer');
  });
});
