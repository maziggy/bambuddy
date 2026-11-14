import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SettingsPage } from '../../pages/SettingsPage';
import { setAuthToken } from '../../api/client';

const baseSettings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: true,
  check_printer_firmware: false,
  ftp_retry_enabled: true,
  ftp_retry_count: 3,
  ftp_retry_delay: 2,
  ftp_timeout: 30,
  printer_rediscovery_enabled: true,
  printer_rediscovery_subnet_scan: true,
  printer_rediscovery_scan_interval: 10,
};

describe('SettingsPage Printer Rediscovery under Network tab', () => {
  let saves: Record<string, unknown>[];

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    saves = [];
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(baseSettings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...baseSettings, ...body });
      }),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
  });

  it('renders under Settings -> Network and saves rediscovery settings', async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    await user.click(screen.getAllByText('Network')[0]);
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(await screen.findByText('Printer Rediscovery')).toBeInTheDocument();

    const intervalInput = await screen.findByLabelText('Minimum Scan Interval (minutes)');
    fireEvent.change(intervalInput, { target: { value: '25' } });

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ printer_rediscovery_scan_interval: 25 }), {
      timeout: 3000,
    });

    const subnetCheckbox = document.getElementById('printer-rediscovery-subnet-scan') as HTMLInputElement;
    await user.click(subnetCheckbox);

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ printer_rediscovery_subnet_scan: false }), {
      timeout: 3000,
    });
  });
});

