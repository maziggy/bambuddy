/**
 * Tests for the scan-to-location target picker (#1574). The scan step uses a
 * native-camera photo capture (file input), so here we only assert the target
 * selection + transition to the photo step; decoding a captured image is a
 * platform path not exercisable in jsdom.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent, render, waitFor } from '../utils';
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
      http.get('/api/v1/inventory/locations', () =>
        HttpResponse.json([
          { id: 7, name: 'Shelf A', identifier: null, spool_count: 2, created_at: '', updated_at: '' },
        ]),
      ),
    );
  });

  it('gates the scan button until an AMS slot is chosen, then opens the photo step', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    // Slots render from the printer status (formatSlotLabel(0,0,..) => "A1").
    const slotA1 = await screen.findByRole('button', { name: 'A1' });

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    fireEvent.click(slotA1);
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);

    // Photo step: header switches, the Take-photo action and target chip show.
    expect(await screen.findByText('Scan a spool QR')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /take photo/i })).toBeInTheDocument();
    expect(screen.getByText(/Target: X1C · A1/)).toBeInTheDocument();
  });

  it('storage tab: scan button enables only after a structured location is picked', async () => {
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    fireEvent.click(screen.getByRole('button', { name: /storage/i }));

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    // Options come from the Locations catalog, not free text.
    const select = await screen.findByRole('combobox');
    expect(await screen.findByRole('option', { name: 'Shelf A' })).toBeInTheDocument();

    fireEvent.change(select, { target: { value: '7' } });
    expect(startBtn).toBeEnabled();

    fireEvent.click(startBtn);
    expect(await screen.findByText(/Target: Shelf A/)).toBeInTheDocument();
  });

  it('storage tab: creating a location picks it immediately, without waiting for a refetch', async () => {
    server.use(
      http.post('/api/v1/inventory/locations', () =>
        HttpResponse.json({ id: 9, name: 'Bin 7', identifier: null, spool_count: 0, created_at: '', updated_at: '' }),
      ),
    );
    render(<QrAssignTargetModal isOpen onClose={noop} spoolmanMode={false} />);

    fireEvent.click(screen.getByRole('button', { name: /storage/i }));
    await screen.findByRole('option', { name: 'Shelf A' });

    const startBtn = screen.getByRole('button', { name: /set target & scan/i });
    expect(startBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Shelf A, Drawer 1/i), { target: { value: 'Bin 7' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    // The created location is selected straight away and resolves by id.
    await waitFor(() => expect(startBtn).toBeEnabled());
    fireEvent.click(startBtn);
    expect(await screen.findByText(/Target: Bin 7/)).toBeInTheDocument();
  });
});
