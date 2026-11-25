/**
 * SpoolBuddy inventory: the AMS location badge of an assigned spool, in both
 * inventory modes. A rack printer (H3C) names its per-hotend external inputs
 * "Ext-n", which needs the assignment's printer_model in Spoolman mode too.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { ToastProvider } from '../../contexts/ToastContext';
import { SpoolBuddyInventoryPage } from '../../pages/spoolbuddy/SpoolBuddyInventoryPage';

let apiResponses: Record<string, unknown> = {};

vi.mock('../../api/client', () => ({
  api: new Proxy({} as Record<string, unknown>, {
    get: (_t, p: string) => () => Promise.resolve(p in apiResponses ? apiResponses[p] : null),
  }),
}));

const spool = (id: number) => ({
  id,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Bambu Lab',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 0,
  archived_at: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: 'spoolman',
  k_profiles: [],
});

function renderPage() {
  const context = { selectedPrinterId: 1, sbState: { matchedSpool: null } };
  function Wrapper() {
    return <Outlet context={context} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/spoolbuddy/inventory']}>
          <Routes>
            <Route element={<Wrapper />}>
              <Route path="spoolbuddy/inventory" element={<SpoolBuddyInventoryPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>,
  );
}

describe('SpoolBuddy inventory slot badge', () => {
  beforeEach(() => {
    apiResponses = {};
  });

  it('names an H3C external input Ext-n in Spoolman mode', async () => {
    apiResponses = {
      getSpoolmanSettings: { spoolman_enabled: 'true', spoolman_url: 'http://spoolman:7912' },
      getSpoolmanInventorySpools: [spool(1), spool(2), spool(3)],
      getSpoolmanSlotAssignments: [
        // (255, 5) = input 250, which feeds hotend 5: Ext-6.
        { printer_id: 2, printer_name: 'H3C', ams_id: 255, tray_id: 5, spoolman_spool_id: 1, ams_label: null, printer_model: 'H3C' },
        { printer_id: 2, printer_name: 'H3C', ams_id: 32, tray_id: 6, spoolman_spool_id: 2, ams_label: null, printer_model: 'H3C' },
        { printer_id: 1, printer_name: 'X1C', ams_id: 255, tray_id: 0, spoolman_spool_id: 3, ams_label: null, printer_model: 'X1C' },
      ],
    };
    renderPage();

    expect(await screen.findByText('Ext-6')).toBeInTheDocument();
    expect(screen.getByText('3H-7')).toBeInTheDocument();
    expect(screen.getByText('Ext')).toBeInTheDocument();
  });

  it('names an H3C external input Ext-n with the built-in inventory', async () => {
    apiResponses = {
      getSpoolmanSettings: { spoolman_enabled: 'false', spoolman_url: '' },
      getSpools: [spool(1)],
      getAssignments: [{ id: 1, spool_id: 1, printer_id: 2, printer_name: 'H3C', ams_id: 255, tray_id: 8, printer_model: 'H3C' }],
    };
    renderPage();

    // (255, 8) = input 253, hotend 2: Ext-3.
    expect(await screen.findByText('Ext-3')).toBeInTheDocument();
  });
});
