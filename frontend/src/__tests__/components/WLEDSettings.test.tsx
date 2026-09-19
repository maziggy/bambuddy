import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { WLEDSettings } from '../../components/WLEDSettings';
import { render } from '../utils';
import { server } from '../mocks/server';

const printer = {
  id: 1,
  name: 'X1C Workshop',
  serial_number: 'SERIAL1',
  ip_address: '192.168.1.10',
  model: 'X1C',
  location: null,
  nozzle_count: 1,
  supports_nozzle_flow_type: true,
  is_active: true,
  auto_archive: true,
  external_camera_url: null,
  external_camera_type: null,
  external_camera_enabled: false,
  external_camera_snapshot_url: null,
  camera_rotation: 0,
  plate_detection_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  wled_config: {
    enabled: false,
    base_url: 'http://wled.local',
    presets: {
      idle: 1,
      prepare: null,
      printing: 99,
      paused: null,
      finished: 9,
      error: null,
      queue_waiting: null,
      filament_problem: null,
      hms_error: null,
      offline: null,
    },
    finished_timeout_seconds: 120,
  },
};

describe('WLEDSettings', () => {
  it('keeps the integration card and disabled toggle visible without printers', async () => {
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json([])));
    render(<WLEDSettings />);

    expect(await screen.findByRole('heading', { name: 'WLED' })).toBeInTheDocument();
    expect(screen.getByText('No printers configured.')).toBeInTheDocument();
    expect(screen.getByLabelText('Enable WLED integration')).toBeDisabled();
  });

  it('keeps saved values visible and disabled until the integration is enabled', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.post('/api/v1/printers/1/wled/presets', () =>
        HttpResponse.json([{ id: 1, name: 'Idle White' }, { id: 9, name: 'Finished Green' }]),
      ),
    );
    render(<WLEDSettings />);

    const url = await screen.findByDisplayValue('http://wled.local');
    const printing = screen.getByLabelText('Printing');
    const timeout = screen.getByLabelText('Finished timeout (seconds)');
    expect(url).toBeDisabled();
    expect(printing).toBeDisabled();
    expect(printing).toHaveValue('99');
    expect(timeout).toBeDisabled();
    expect(timeout).toHaveValue(120);

    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    expect(url).toBeEnabled();
    expect(printing).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));

    expect(await screen.findByText('2 presets loaded')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Saved preset 99 — currently unavailable' })).toBeInTheDocument();
    expect(printing).toHaveValue('99');

    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    expect(url).toBeDisabled();
    expect(printing).toBeDisabled();
    expect(printing).toHaveValue('99');
    expect(timeout).toHaveValue(120);
  });

  it('loads presets automatically for a saved enabled configuration', async () => {
    let requests = 0;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...printer,
        wled_config: { ...printer.wled_config, enabled: true },
      }])),
      http.post('/api/v1/printers/1/wled/presets', () => {
        requests += 1;
        return HttpResponse.json([
          { id: 1, name: 'Idle White' },
          { id: 9, name: 'Finished Green' },
          { id: 99, name: 'Printing Blue' },
        ]);
      }),
    );
    render(<WLEDSettings />);

    expect(await screen.findByText('3 presets loaded')).toBeInTheDocument();
    const printing = screen.getByLabelText('Printing');
    expect(printing).toHaveValue('99');
    expect(within(printing).getByRole('option', { name: 'Printing Blue (99)' })).toBeInTheDocument();
    expect(requests).toBe(1);
  });

  it('edits and saves enabled, URL, preset and finished timeout', async () => {
    let saved: unknown;
    let testedPreset: unknown;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{ ...printer, wled_config: null }])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json([{ id: 3, name: 'Printing Blue' }])),
      http.post('/api/v1/printers/1/wled/test-connection', () =>
        HttpResponse.json({ name: 'Kitchen LEDs', version: '0.15.0' })),
      http.post('/api/v1/printers/1/wled/test-preset', async ({ request }) => {
        testedPreset = await request.json();
        return HttpResponse.json({ success: true });
      }),
      http.patch('/api/v1/printers/1', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ ...printer, ...(saved as object) });
      }),
    );
    render(<WLEDSettings />);

    await screen.findByText('X1C Workshop');
    await userEvent.click(screen.getByLabelText('Enable WLED integration'));
    await userEvent.type(screen.getByLabelText('WLED URL'), 'http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Connected to WLED (Kitchen LEDs) · v0.15.0')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    await screen.findByText('1 presets loaded');
    await userEvent.selectOptions(screen.getByLabelText('Printing'), '3');
    expect(screen.getByText('Configured states: 1 · Disabled states: 9')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Test Printing' }));
    await waitFor(() => expect(testedPreset).toEqual({ base_url: 'http://wled.local', preset_id: 3 }));
    await userEvent.clear(screen.getByLabelText('Finished timeout (seconds)'));
    await userEvent.type(screen.getByLabelText('Finished timeout (seconds)'), '30');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(saved).toEqual({
      wled_config: expect.objectContaining({
        enabled: true,
        base_url: 'http://wled.local',
        presets: expect.objectContaining({ printing: 3 }),
        finished_timeout_seconds: 30,
      }),
    }));
  });

  it('shows the offline state without clearing saved selections', async () => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{
        ...printer,
        wled_config: { ...printer.wled_config, enabled: true },
      }])),
      http.post('/api/v1/printers/1/wled/presets', () => HttpResponse.json({}, { status: 502 })),
      http.post('/api/v1/printers/1/wled/test-connection', () => HttpResponse.json({}, { status: 502 })),
    );
    render(<WLEDSettings />);

    await screen.findByDisplayValue('http://wled.local');
    await userEvent.click(screen.getByRole('button', { name: 'Load presets' }));
    expect((await screen.findAllByText('WLED is unavailable. Saved preset IDs are kept.')).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Printing')).toHaveValue('99');
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Unable to connect to WLED.')).toBeInTheDocument();
  });
});
