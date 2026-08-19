/**
 * Tests for #2532: K-profile value shown directly on the AMS slot card,
 * not only inside the hover popup.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    ip_address: '192.168.1.100',
    serial_number: '00M09A350100001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    is_active: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockPrinterStatus = {
  connected: true,
  state: 'IDLE',
  awaiting_plate_clear: false,
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

describe('PrintersPage - K-profile always-visible display (#2532)', () => {
  beforeEach(() => {
    localStorage.removeItem('printerCardSize');

    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
      http.get('/api/v1/settings/', () => HttpResponse.json({
        auto_archive: true,
        save_thumbnails: true,
        capture_finish_photo: true,
        default_filament_cost: 25.0,
        currency: 'USD',
        ams_humidity_good: 40,
        ams_humidity_fair: 60,
        ams_temp_good: 30,
        ams_temp_fair: 35,
        require_plate_clear: true,
      })),
      http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({
        ams_humidity_good: 40,
        ams_humidity_fair: 60,
        ams_temp_good: 30,
        ams_temp_fair: 35,
        require_plate_clear: true,
      })),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
      http.get('/api/v1/spoolman/settings', () => HttpResponse.json({
        spoolman_enabled: 'false', spoolman_url: '',
      })),
    );
  });

  it('shows the K-value on a loaded standard AMS slot without hovering', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        // A standard (non-HT) AMS unit has 4 trays — htAms in PrintersPage.tsx
        // filters on tray.length === 1, so a single-tray mock here would
        // silently exercise the AMS-HT code path instead of this one.
        ams: [{
          id: 0,
          tray: [
            {
              id: 0,
              tray_type: 'PETG',
              tray_color: 'FF0000FF',
              tray_sub_brands: 'Bambu PETG HF',
              k: 0.024,
            },
            { id: 1, tray_type: null, state: 9 },
            { id: 2, tray_type: null, state: 9 },
            { id: 3, tray_type: null, state: 9 },
          ],
        }],
      })),
    );

    render(<PrintersPage />);

    // No hover/mouseEnter simulated here on purpose — the whole point of
    // #2532 is that this must be readable without one.
    await waitFor(() => {
      expect(screen.getByText('K 0.024')).toBeInTheDocument();
    });

    // The short "K" label is intentional (round-2 review: the full "K
    // Factor" label was clipping the value at narrow slot widths), but the
    // full localized name should still be reachable via the title attribute.
    expect(screen.getByText('K 0.024')).toHaveAttribute('title', 'K Factor');
  });

  it('does not show a K-value on an empty slot', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 0,
          tray: [
            { id: 0, tray_type: null, state: 9 },
            { id: 1, tray_type: null, state: 9 },
            { id: 2, tray_type: null, state: 9 },
            { id: 3, tray_type: null, state: 9 },
          ],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      // All 4 slots are empty, so several "Empty" labels render.
      expect(screen.getAllByText('Empty').length).toBeGreaterThan(0);
    });
    // ... but formatKValue() defaults to 0.020 when there's no tray data,
    // so this specifically guards against that default leaking onto an
    // empty slot as a misleading "K 0.020".
    expect(screen.queryByText(/^K 0\.020$/)).not.toBeInTheDocument();
  });

  it('does not show a fabricated K-value on a loaded slot with no reported K (review #2854)', async () => {
    // Regression test for maziggy's review on #2854: a slot can be loaded
    // (tray_type present) while the printer has simply never reported a K
    // value for it — k is null, not merely absent. This is common on X1C,
    // where K comes from a cali_idx lookup that can legitimately miss.
    // formatKValue() defaults null to 0.020, which reads as a real measured
    // value on this permanent, uncaptioned line, so the row must not render
    // at all in this case rather than showing that default.
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        // 4 trays — see comment on the first test above re: htAms filtering.
        ams: [{
          id: 0,
          tray: [
            {
              id: 0,
              tray_type: 'PETG',
              tray_color: 'FF0000FF',
              tray_sub_brands: 'Bambu PETG HF',
              k: null,
            },
            { id: 1, tray_type: null, state: 9 },
            { id: 2, tray_type: null, state: 9 },
            { id: 3, tray_type: null, state: 9 },
          ],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      // The slot itself is loaded and visible ...
      expect(screen.getByText('PETG')).toBeInTheDocument();
    });
    // ... but no K-value line, fabricated or otherwise, should appear. Read the
    // slot's own text rather than only querying for a "K " label: a guard that
    // leaks a value without the label would pass the label query.
    expect(screen.getByText('PETG').parentElement).toHaveTextContent(/^1PETG$/);
  });

  it('does not show a K-value when the printer reports exactly 0 (review #2854, round 2)', async () => {
    // maziggy's round-2 question: does tray.k != null let a real firmware "0"
    // through as a misleading "K 0.000"? It does -- printer_manager.py assigns
    // the tray's own reported k verbatim and only filters falsy values when
    // falling back to a stored K-profile, so a 0 does reach this component.
    // Gate on a truthy tray.k, and render it through a ternary: `tray.k && ...`
    // evaluates to the number 0, and React renders numbers, so the guard itself
    // would print a bare "0" where the value belongs.
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 0,
          tray: [
            {
              id: 0,
              tray_type: 'PETG',
              tray_color: 'FF0000FF',
              tray_sub_brands: 'Bambu PETG HF',
              k: 0,
            },
            { id: 1, tray_type: null, state: 9 },
            { id: 2, tray_type: null, state: 9 },
            { id: 3, tray_type: null, state: 9 },
          ],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('PETG')).toBeInTheDocument();
    });
    // The slot number and the material, and nothing else -- no "K 0.000", and
    // no stray "0" leaked by the guard.
    expect(screen.getByText('PETG').parentElement).toHaveTextContent(/^1PETG$/);
  });

  it('does not leak a stray 0 on an external or AMS-HT slot reporting exactly 0', async () => {
    // Same guard, same defect, on the two other slot types.
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{ id: 1, tray: [{ id: 0, tray_type: 'ASA', tray_color: 'FFFFFFFF', k: 0 }] }],
        vt_tray: [{ id: 254, tray_type: 'PLA', tray_color: '000000FF', k: 0 }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('ASA')).toBeInTheDocument();
    });
    expect(screen.getByText('ASA').parentElement).toHaveTextContent(/^1ASA$/);
    expect(screen.getByText('PLA').parentElement).toHaveTextContent(/^1PLA$/);
  });

  it('shows the K-value on a loaded AMS-HT slot without hovering', async () => {
    // AMS-HT units are identified by having exactly one tray in their
    // `tray` array (vs. four for a standard AMS unit) — see the htAms
    // filter in PrintersPage.tsx.
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 1,
          tray: [{
            id: 0,
            tray_type: 'ASA',
            tray_color: 'FFFFFFFF',
            tray_sub_brands: 'Bambu ASA',
            k: 0.018,
          }],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('K 0.018')).toBeInTheDocument();
    });
  });

  it('shows the K-value on a loaded external/dual-nozzle slot without hovering', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        vt_tray: [{
          id: 254,
          tray_type: 'PLA',
          tray_color: '000000FF',
          tray_sub_brands: 'Bambu PLA Basic',
          k: 0.022,
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('K 0.022')).toBeInTheDocument();
    });
  });

  it('reserves the row on slots without a K-value so fill bars stay aligned', async () => {
    // The K line is an extra block child, so on a partially calibrated unit --
    // the normal state, not an edge case -- the calibrated slot's fill bar would
    // sit a line below its neighbours'. The slots without a value hold the row
    // open instead. A card with no calibrated slot anywhere keeps its old
    // height, which the tests above assert.
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 0,
          tray: [
            { id: 0, tray_type: 'PETG', tray_color: 'FF0000FF', k: 0.024 },
            { id: 1, tray_type: 'PLA', tray_color: '00FF00FF', k: null },
            { id: 2, tray_type: null, state: 9 },
            { id: 3, tray_type: null, state: 9 },
          ],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('K 0.024')).toBeInTheDocument();
    });

    const calibrated = screen.getByText('PETG').parentElement as HTMLElement;
    const uncalibrated = screen.getByText('PLA').parentElement as HTMLElement;
    // Same number of children, so the fill bar sits at the same offset in both.
    expect(uncalibrated.children.length).toBe(calibrated.children.length);
    // The reserved row carries no readable text of its own.
    expect(uncalibrated).toHaveTextContent(/^2PLA$/);
  });
});
