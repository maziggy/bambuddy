/**
 * InventoryPage, SpoolLocationFooter, and SettingsPage's sensor overview all
 * read one location's live readings under the same query key now
 * (['locationHaSensorReadings', locationId], no 'all'/'cardOnly' suffix).
 * SettingsPage was missed when the other two were unified and kept its own
 * 'all'-suffixed key — this pins that navigating between the two pages in
 * the same session reuses the cache instead of firing a second request for
 * a location that was just fetched.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { AuthProvider } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import InventoryPageRouter from '../../pages/InventoryPage';
import { SettingsPage } from '../../pages/SettingsPage';

const mockSpool = {
  id: 1,
  material: 'PLA',
  subtype: null,
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 100,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: null,
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  location_id: 7,
  storage_location: 'Drybox 1',
};

const mockSensor = {
  id: 1,
  location_id: 7,
  name: 'Drybox 1 Temperature',
  entity_id: 'sensor.drybox_1_temperature',
  kind: 'numeric',
  device_class: 'temperature',
  unit: '°C',
  alert_state: null,
  alert_above: null,
  alert_below: null,
  notify_on_alert: false,
  show_on_card: true,
  sort_order: 0,
  last_state: '24.5',
  last_changed: '2025-01-01T00:00:00Z',
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
};

const mockReading = {
  id: 1,
  name: 'Drybox 1 Temperature',
  entity_id: 'sensor.drybox_1_temperature',
  kind: 'numeric',
  device_class: 'temperature',
  unit: '°C',
  state: '24.5',
  value: 24.5,
  alerting: false,
  reachable: true,
  alert_state: null,
  alert_above: null,
  alert_below: null,
  last_changed: '2025-01-01T00:00:00Z',
  show_on_card: true,
};

// A shared client, mimicking the app's real one: unlike the default test
// client's gcTime: 0, unmounting a page must not evict the cache the next
// page is meant to reuse.
function SharedProviders({ children, client }: { children: React.ReactNode; client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>{children}</ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

describe('location sensor readings — shared cache across pages', () => {
  let readingsRequestCount: number;
  let client: QueryClient;

  beforeEach(() => {
    readingsRequestCount = 0;
    // staleTime matches App.tsx's real QueryClient — the default of 0 would
    // make every remount refetch regardless of whether the keys line up,
    // which is not what production does and would make this test pass for
    // the wrong reason.
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 1000 * 60 }, mutations: { retry: false } },
    });
    server.use(
      http.get('/api/v1/inventory/spools', () => HttpResponse.json([mockSpool])),
      http.get('/api/v1/location-ha-sensors/', () => HttpResponse.json([mockSensor])),
      http.get('/api/v1/inventory/locations', () =>
        HttpResponse.json([{ id: 7, name: 'Drybox 1', identifier: null, spool_count: 1, created_at: '', updated_at: '' }])
      ),
      http.get('/api/v1/location-ha-sensors/by-location/7/readings', () => {
        readingsRequestCount += 1;
        return HttpResponse.json([mockReading]);
      }),
    );
  });

  it('does not refetch a location Inventory already loaded when Settings opens next', async () => {
    const user = userEvent.setup();

    render(
      <SharedProviders client={client}>
        <InventoryPageRouter />
      </SharedProviders>
    );
    await user.click(await screen.findByText('Cards'));
    await waitFor(() => expect(screen.getByText('24.50 °C')).toBeInTheDocument());
    expect(readingsRequestCount).toBe(1);

    cleanup();

    render(
      <SharedProviders client={client}>
        <SettingsPage />
      </SharedProviders>
    );
    await user.click(await screen.findByText('Sensors'));
    await screen.findByText('sensor.drybox_1_temperature');

    // Give a stray refetch a moment to land before asserting its absence —
    // if the keys still mismatched, this is where the second request fires.
    await new Promise((r) => setTimeout(r, 50));
    expect(readingsRequestCount).toBe(1);
  });
});
