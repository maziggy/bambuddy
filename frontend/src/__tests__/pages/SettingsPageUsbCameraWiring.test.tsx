/**
 * Settings > Camera: a USB camera is picked from the video devices the server
 * lists, a saved device that is unplugged stays selected, and the path can
 * still be typed by hand.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';
import { SettingsPage } from '../../pages/SettingsPage';

const PRINTER = {
  id: 7,
  name: 'Bench',
  serial_number: 'TEST123',
  ip_address: '192.168.1.100',
  access_code: 'XXXX',
  model: 'P1S',
  location: null,
  nozzle_count: 1,
  is_active: true,
  auto_archive: true,
  external_camera_url: '/dev/video0',
  external_camera_type: 'usb',
  external_camera_enabled: true,
  external_camera_snapshot_url: null,
  camera_rotation: 0,
  plate_detection_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const WEBCAM = { device: '/dev/video0', name: 'HD Pro Webcam C920', capabilities: ['capture'] };
const SECOND = { device: '/dev/video2', name: 'USB Camera', capabilities: ['capture'] };

let listCalls: number;
let cameras: typeof WEBCAM[];
let patches: Record<string, unknown>[];

function setup(printer: Record<string, unknown> = PRINTER) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/usb-cameras', () => {
      listCalls += 1;
      return HttpResponse.json({ cameras });
    }),
    http.patch('/api/v1/printers/7', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      patches.push(body);
      return HttpResponse.json({ ...printer, ...body });
    }),
  );
}

async function openCamera() {
  render(<SettingsPage />);
  await userEvent.setup().click(await screen.findByLabelText('Camera settings: Bench'));
}

const picker = () => screen.findByLabelText('USB device: Bench') as Promise<HTMLSelectElement>;

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=camera');
  setAuthToken(null);
  listCalls = 0;
  cameras = [WEBCAM, SECOND];
  patches = [];
});

describe('USB camera device picker', () => {
  it('lists the server\'s video devices for a USB camera', async () => {
    setup();
    await openCamera();

    const select = await picker();
    await waitFor(() => expect(within(select).getByRole('option', { name: 'HD Pro Webcam C920 (/dev/video0)' })).toBeInTheDocument());
    expect(within(select).getByRole('option', { name: 'USB Camera (/dev/video2)' })).toBeInTheDocument();
    expect(select.value).toBe('/dev/video0');
    expect(screen.queryByPlaceholderText('Device path (/dev/video0)')).not.toBeInTheDocument();
  });

  it('saves the picked device as the camera path', async () => {
    setup();
    await openCamera();

    const select = await picker();
    await screen.findByRole('option', { name: 'USB Camera (/dev/video2)' });
    await userEvent.setup().selectOptions(select, '/dev/video2');

    await waitFor(() => expect(patches).toContainEqual({ external_camera_url: '/dev/video2' }), { timeout: 3000 });
  });

  it('keeps a saved device that is not connected, marked not found', async () => {
    cameras = [SECOND];
    setup();
    await openCamera();

    const select = await picker();
    await screen.findByRole('option', { name: '/dev/video0 (not found)' });
    expect(select.value).toBe('/dev/video0');
    expect(patches).toEqual([]);
  });

  it('says how to pass a camera to Docker when no device is visible', async () => {
    cameras = [];
    setup();
    await openCamera();

    expect(await screen.findByText(/No video devices are visible to Bambuddy/)).toBeInTheDocument();
    expect(screen.getByText('devices: - /dev/video0:/dev/video0')).toBeInTheDocument();
  });

  it('lets the path be typed by hand, and goes back to the list', async () => {
    setup();
    await openCamera();
    await picker();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Enter path manually' }));
    const input = screen.getByPlaceholderText('Device path (/dev/video0)') as HTMLInputElement;
    expect(input.value).toBe('/dev/video0');
    expect(screen.queryByLabelText('USB device: Bench')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Choose a detected device' }));
    expect(await picker()).toBeInTheDocument();
  });

  it('looks for devices again on refresh', async () => {
    setup();
    await openCamera();
    await screen.findByRole('option', { name: 'USB Camera (/dev/video2)' });
    const before = listCalls;

    cameras = [WEBCAM, SECOND, { device: '/dev/video4', name: 'Plugged in later', capabilities: ['capture'] }];
    await userEvent.setup().click(screen.getByRole('button', { name: 'Look for devices again' }));

    expect(await screen.findByRole('option', { name: 'Plugged in later (/dev/video4)' })).toBeInTheDocument();
    expect(listCalls).toBeGreaterThan(before);
  });

  it('does not list devices for a camera that is not USB', async () => {
    setup({ ...PRINTER, external_camera_type: 'mjpeg', external_camera_url: 'http://192.168.1.61/stream' });
    await openCamera();

    expect(await screen.findByPlaceholderText(/Camera URL/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('USB device: Bench')).not.toBeInTheDocument();
    expect(listCalls).toBe(0);
  });
});
