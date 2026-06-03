/**
 * Tests for PrinterDetailPage — motion guard and jog step selection.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrinterDetailPage } from '../../pages/PrinterDetailPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ printerId: '1' }),
    useNavigate: () => vi.fn(),
  };
});

const mockPrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  is_active: true,
  nozzle_count: 1,
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const baseStatus = {
  id: 1,
  name: 'X1 Carbon',
  connected: true,
  state: 'IDLE',
  current_print: null,
  subtask_name: null,
  current_archive_id: null,
  current_plate_id: null,
  gcode_file: null,
  progress: 0,
  remaining_time: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  cover_url: null,
  hms_errors: [],
  ams: [],
  ams_exists: false,
  vt_tray: [],
  store_to_sdcard: false,
  timelapse: false,
  ipcam: true,
  wifi_signal: -50,
  wired_network: false,
  door_open: false,
  nozzles: [{ nozzle_type: 'hardened_steel', nozzle_diameter: '0.4' }],
  nozzle_rack: [],
  print_options: null,
  stg_cur: -1,
  stg_cur_name: null,
  stg: [],
  airduct_mode: 0,
  speed_level: 2,
  chamber_light: false,
  active_extruder: 0,
  ams_mapping: [],
  ams_extruder_map: {},
  fila_switch: null,
  tray_now: 254,
  ams_status_main: 0,
  ams_status_sub: 0,
  mc_print_sub_stage: 0,
  last_ams_update: 0,
  printable_objects_count: 0,
  cooling_fan_speed: 0,
  big_fan1_speed: 0,
  big_fan2_speed: 0,
  heatbreak_fan_speed: 0,
  firmware_version: '1.0',
  developer_mode: true,
  awaiting_plate_clear: false,
  supports_drying: false,
};

describe('PrinterDetailPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/1', () => HttpResponse.json(mockPrinter)),
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({ ...baseStatus, state: 'IDLE' })
      ),
      http.get('/api/v1/auth/camera-stream-token', () =>
        HttpResponse.json({ token: 'test-token' })
      )
    );
  });

  it('renders control panel and printer name', async () => {
    render(<PrinterDetailPage />);
    await waitFor(() => {
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });
    expect(screen.getByText(/control/i)).toBeInTheDocument();
  });

  it('disables motion home button while printing', async () => {
    server.use(
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({ ...baseStatus, state: 'RUNNING', progress: 50 })
      )
    );
    render(<PrinterDetailPage />);
    await waitFor(() => {
      expect(screen.getByText(/motion.*disabled|disabled while/i)).toBeInTheDocument();
    });
    const homeBtn = screen.getByRole('button', { name: /auto home|home/i });
    expect(homeBtn).toBeDisabled();
  });

  it('selects jog step 50', async () => {
    const user = userEvent.setup();
    render(<PrinterDetailPage />);
    await waitFor(() => {
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });
    const step50 = screen.getAllByRole('button', { name: '50' })[0];
    await user.click(step50);
    expect(step50).toHaveClass('text-bambu-green');
  });
});
