/**
 * Location-sensor readings should cost one request per location, not two.
 *
 * Card view (SpoolLocationFooter) and the table's Temperature/Humidity/
 * Battery columns used to each fetch their own copy — the footer under a
 * 'cardOnly' key, the table under an 'all' key — so a location with sensors
 * bound cost two requests per poll interval whenever a card was on screen,
 * and the table's 'all' request fired even with all three sensor columns
 * hidden, which is the default. Both queries now share one key and one
 * unfiltered fetch; the footer filters to show_on_card itself.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

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

const mockSpoolAtUnsensoredLocation = {
  ...mockSpool,
  id: 2,
  brand: 'eSun',
  location_id: 8,
  storage_location: 'Shelf 2',
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

const COLUMN_CONFIG_KEY = 'bambuddy-inventory-columns';

describe('InventoryPage - location sensor readings request count', () => {
  let readingsRequestCount: number;

  beforeEach(() => {
    vi.mocked(window.localStorage.getItem).mockReset();
    vi.mocked(window.localStorage.setItem).mockReset();
    readingsRequestCount = 0;
    server.use(
      http.get('/api/v1/inventory/spools', () => HttpResponse.json([mockSpool])),
      http.get('/api/v1/location-ha-sensors/', () => HttpResponse.json([mockSensor])),
      http.get('/api/v1/location-ha-sensors/by-location/7/readings', () => {
        readingsRequestCount += 1;
        return HttpResponse.json([mockReading]);
      }),
    );
  });

  it('does not fetch readings in table view with sensor columns hidden (the default)', async () => {
    render(<InventoryPageRouter />);

    await waitFor(() => {
      expect(screen.getAllByText('Polymaker').length).toBeGreaterThan(0);
    });
    // Give any stray request a moment to land before asserting its absence.
    await new Promise((r) => setTimeout(r, 50));
    expect(readingsRequestCount).toBe(0);
  });

  it('fetches readings exactly once in table view when a sensor column is visible', async () => {
    vi.mocked(window.localStorage.getItem).mockImplementation((key: string) =>
      key === COLUMN_CONFIG_KEY
        ? JSON.stringify([{ id: 'temperature', label: 'Temperature', visible: true }])
        : null
    );

    render(<InventoryPageRouter />);

    await waitFor(() => expect(screen.getByText('24.50 °C')).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 50));
    expect(readingsRequestCount).toBe(1);
  });

  it('fetches readings exactly once in card view, shared between the table query and the footer', async () => {
    const user = userEvent.setup();
    render(<InventoryPageRouter />);

    await waitFor(() => expect(screen.getAllByText('Polymaker').length).toBeGreaterThan(0));
    await user.click(screen.getByText('Cards'));

    await waitFor(() => expect(screen.getByText('24.50 °C')).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 50));
    expect(readingsRequestCount).toBe(1);
  });

  it('hides a sensor with show_on_card=false from the card footer without a second request', async () => {
    server.use(
      http.get('/api/v1/location-ha-sensors/by-location/7/readings', () => {
        readingsRequestCount += 1;
        return HttpResponse.json([{ ...mockReading, show_on_card: false }]);
      }),
    );
    const user = userEvent.setup();
    render(<InventoryPageRouter />);

    await waitFor(() => expect(screen.getAllByText('Polymaker').length).toBeGreaterThan(0));
    await user.click(screen.getByText('Cards'));

    // The footer never renders — its one sensor is hidden from cards — but
    // that must be a client-side filter, not a second, differently-scoped
    // request.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('24.50 °C')).toBeNull();
    expect(readingsRequestCount).toBe(1);
  });

  it('never queries a location with no bound sensor, in either view', async () => {
    // The gate that started this file: a location absent from
    // getLocationHASensors() must never appear in the readings useQueries
    // array at all — not merely disabled — so it costs nothing whether its
    // card/row is on screen or not. Most installs have far more storage
    // locations than ones actually wired up to Home Assistant.
    let unsensoredLocationRequestCount = 0;
    vi.mocked(window.localStorage.getItem).mockImplementation((key: string) =>
      key === COLUMN_CONFIG_KEY
        ? JSON.stringify([{ id: 'temperature', label: 'Temperature', visible: true }])
        : null
    );
    server.use(
      http.get('/api/v1/inventory/spools', () =>
        HttpResponse.json([mockSpool, mockSpoolAtUnsensoredLocation])
      ),
      http.get('/api/v1/location-ha-sensors/by-location/8/readings', () => {
        unsensoredLocationRequestCount += 1;
        return HttpResponse.json([]);
      }),
    );

    const user = userEvent.setup();
    render(<InventoryPageRouter />);

    // Table view, temperature column visible: location 7 (has a sensor) is queried.
    await waitFor(() => expect(screen.getByText('24.50 °C')).toBeInTheDocument());
    expect(screen.getAllByText('eSun').length).toBeGreaterThan(0);
    await new Promise((r) => setTimeout(r, 50));
    expect(readingsRequestCount).toBe(1);
    expect(unsensoredLocationRequestCount).toBe(0);

    // Card view too — the footer for location 8's card must not query it either.
    await user.click(screen.getByText('Cards'));
    await waitFor(() => expect(screen.getAllByText('eSun').length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 50));
    expect(unsensoredLocationRequestCount).toBe(0);
  });
});
