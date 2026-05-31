/**
 * Tests for the scan-to-location target picker + in-page scanner (#1574).
 * jsdom exposes no camera (navigator.mediaDevices is undefined), so the scan
 * step deterministically surfaces the "needs HTTPS" camera message — which is
 * exactly what we assert to prove the target → scan transition works.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent, render } from '../utils';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { QrAssignTargetModal } from '../../components/QrAssignTargetModal';

function noop() {}

describe('QrAssignTargetModal', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X1C' }])),
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({
          ams: [{ id: 0, is_ams_ht: false, tray: [{ id: 0 }, { id: 1 }, { id: 2 }, { id: 3 }] }],
          vt_tray: [],
        }),
      ),
    );
  });

  it('gates the scan button until an AMS slot is chosen, then opens the scanner', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} storageSuggestions={[]} />);

    // Slots render from the printer status (formatSlotLabel(0,0,..) => "A1").
    const slotA1 = await screen.findByRole('button', { name: 'A1' });

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    fireEvent.click(slotA1);
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);

    // Scan step: header switches and (no camera in jsdom) the HTTPS hint shows.
    expect(await screen.findByText('Scan a spool QR')).toBeInTheDocument();
    expect(await screen.findByText(/secure \(HTTPS\) connection/i)).toBeInTheDocument();
    // Target chip reflects the chosen slot.
    expect(screen.getByText(/Target: X1C · A1/)).toBeInTheDocument();
  });

  it('storage tab: scan button enables only after a location is entered', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} storageSuggestions={['Shelf A']} />);

    fireEvent.click(screen.getByRole('button', { name: /storage/i }));

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    const input = screen.getByPlaceholderText(/Shelf A, Drawer 1/i);
    fireEvent.change(input, { target: { value: 'Bin 7' } });
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);
    expect(await screen.findByText(/Target: Bin 7/)).toBeInTheDocument();
  });
});
