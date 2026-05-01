/**
 * Tests for the SettingsPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockSettings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  ams_humidity_good: 40,
  ams_humidity_fair: 60,
  ams_temp_good: 30,
  ams_temp_fair: 35,
  time_format: 'system',
  date_format: 'system',
  mqtt_enabled: false,
  mqtt_host: '',
  mqtt_port: 1883,
  spoolman_enabled: false,
  spoolman_url: '',
  ha_enabled: false,
  ha_url: '',
  ha_token: '',
  check_updates: false,
  check_printer_firmware: false,
  bed_cooled_threshold: 35,
};

describe('SettingsPage', () => {
  beforeEach(() => {
    // BrowserRouter shares window.location across tests; reset it so a tab
    // switch in one test (e.g. clicking "Workflow") doesn't carry into
    // sibling tests that expect to land on the default General tab.
    window.history.replaceState({}, '', '/');

    server.use(
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json(mockSettings);
      }),
      http.patch('/api/v1/settings/', async ({ request }) => {
        const body = await request.json();
        return HttpResponse.json({ ...mockSettings, ...body });
      }),
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/smart-plugs/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/notifications/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/api-keys/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/mqtt/status', () => {
        return HttpResponse.json({ enabled: false });
      }),
      http.get('/api/v1/virtual-printer/status', () => {
        return HttpResponse.json({ running: false });
      }),
      http.get('/api/v1/auth/status', () => {
        return HttpResponse.json({ auth_enabled: false, requires_setup: false });
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        // Use role-based query to avoid conflicts with dropdown options
        expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
      });
    });

    it('shows settings tabs', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        // Use getAllByText since "General" appears both as tab and section heading
        expect(screen.getAllByText('General').length).toBeGreaterThan(0);
        expect(screen.getByText('Smart Plugs')).toBeInTheDocument();
        expect(screen.getAllByText('Notifications').length).toBeGreaterThan(0);
        expect(screen.getAllByText('Filament').length).toBeGreaterThan(0);
        expect(screen.getByText('Network')).toBeInTheDocument();
        expect(screen.getByText('API Keys')).toBeInTheDocument();
      });
    });
  });

  describe('general settings', () => {
    it('shows date format setting', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Date Format')).toBeInTheDocument();
      });
    });

    it('shows time format setting', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Time Format')).toBeInTheDocument();
      });
    });

    it('shows default printer setting', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Default Printer')).toBeInTheDocument();
      });
    });

    it('shows preferred slicer setting on Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Preferred Slicer')).toBeInTheDocument();
      });
    });

    it('shows slicer dropdown with both options on Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        const slicerSelect = screen.getAllByDisplayValue('Bambu Studio');
        expect(slicerSelect.length).toBeGreaterThan(0);
      });
    });

    it('shows appearance section', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Appearance')).toBeInTheDocument();
      });
    });

    it('shows updates section with firmware toggle', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Updates')).toBeInTheDocument();
        expect(screen.getByText('Check for updates')).toBeInTheDocument();
        expect(screen.getByText('Check printer firmware')).toBeInTheDocument();
      });
    });
  });

  describe('update CTA per deployment shape', () => {
    // The update card branches on the deployment shape returned by
    // /updates/check. Each branch is mutually exclusive — verify the right
    // one wins so HA addon users never see the docker-compose snippet
    // (which they can't run from inside an HA addon container) and Docker
    // users never see the in-app Install button (which would no-op).
    const renderWithUpdateCheck = async (
      checkBody: Record<string, unknown>,
    ) => {
      server.use(
        http.get('/api/v1/settings/', () =>
          HttpResponse.json({ ...mockSettings, check_updates: true }),
        ),
        http.get('/api/v1/updates/check', () => HttpResponse.json(checkBody)),
      );
      render(<SettingsPage />);
      await waitFor(() => {
        expect(screen.getByText('Updates')).toBeInTheDocument();
      });
    };

    it('shows the HA Supervisor message when running as an HA addon', async () => {
      await renderWithUpdateCheck({
        update_available: true,
        current_version: '0.2.4',
        latest_version: '0.2.5',
        release_name: '0.2.5',
        release_notes: '',
        release_url: 'https://example.invalid/r',
        published_at: '2099-01-01T00:00:00Z',
        is_docker: true,
        is_ha_addon: true,
        update_method: 'ha_addon',
      });

      await waitFor(() => {
        expect(
          screen.getByText(/Home Assistant Supervisor/i),
        ).toBeInTheDocument();
      });
      // Docker hint must NOT render — HA branch wins.
      expect(screen.queryByText('docker compose pull && docker compose up -d')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /install update/i })).not.toBeInTheDocument();
    });

    it('shows the docker-compose snippet for Docker (non-HA) deployments', async () => {
      await renderWithUpdateCheck({
        update_available: true,
        current_version: '0.2.4',
        latest_version: '0.2.5',
        release_name: '0.2.5',
        release_notes: '',
        release_url: 'https://example.invalid/r',
        published_at: '2099-01-01T00:00:00Z',
        is_docker: true,
        is_ha_addon: false,
        update_method: 'docker',
      });

      await waitFor(() => {
        expect(screen.getByText('docker compose pull && docker compose up -d')).toBeInTheDocument();
      });
      expect(screen.queryByText(/Home Assistant Supervisor/i)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /install update/i })).not.toBeInTheDocument();
    });
  });

  describe('tabs navigation', () => {
    it('can switch to Network tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      // Wait for settings to load first
      await waitFor(() => {
        expect(screen.getByText('Date Format')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Network'));

      await waitFor(() => {
        // Network tab contains MQTT Publishing section
        expect(screen.getByText('MQTT Publishing')).toBeInTheDocument();
      });
    });

    it('can switch to Smart Plugs tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Smart Plugs')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Smart Plugs'));

      await waitFor(() => {
        expect(screen.getByText('Add Smart Plug')).toBeInTheDocument();
      });
    });

    it('can switch to Notifications tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Notifications').length).toBeGreaterThan(0);
      });

      // Click the tab button (not the mobile dropdown option)
      const notificationButtons = screen.getAllByText('Notifications');
      const tabButton = notificationButtons.find(el => el.tagName === 'BUTTON') || notificationButtons[0];
      await user.click(tabButton);

      await waitFor(() => {
        expect(screen.getByText('Add Provider')).toBeInTheDocument();
      });
    });

    it('can switch to Filament tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Filament').length).toBeGreaterThan(0);
      });

      await user.click(screen.getAllByText('Filament')[0]);

      await waitFor(() => {
        expect(screen.getByText('AMS Display Thresholds')).toBeInTheDocument();
      });
    });
  });

  describe('Workflow tab', () => {
    it('can switch to Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Staggered Start')).toBeInTheDocument();
      });
    });

    it('shows stagger settings on Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Staggered Start')).toBeInTheDocument();
        expect(screen.getByText('Group size')).toBeInTheDocument();
        expect(screen.getByText('Interval (minutes)')).toBeInTheDocument();
      });
    });

    it('shows auto-drying settings on Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Queue Auto-Drying')).toBeInTheDocument();
      });
    });

    it('shows default print options on Workflow tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Default Print Options')).toBeInTheDocument();
        expect(screen.getByText('Bed Levelling')).toBeInTheDocument();
        expect(screen.getByText('Flow Calibration')).toBeInTheDocument();
        expect(screen.getByText('Vibration Calibration')).toBeInTheDocument();
        expect(screen.getByText('First Layer Inspection')).toBeInTheDocument();
        expect(screen.getByText('Timelapse')).toBeInTheDocument();
      });
    });

    it('shows default print options description', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText(/overridden per print in the print dialog/)).toBeInTheDocument();
      });
    });
  });

  describe('API Keys tab', () => {
    it('can switch to API Keys tab', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('API Keys')).toBeInTheDocument();
      });

      await user.click(screen.getByText('API Keys'));

      await waitFor(() => {
        // Button text is "Create Key"
        expect(screen.getByText('Create Key')).toBeInTheDocument();
      });
    });
  });

  describe('SpoolBuddy tab badge', () => {
    const baseDevice = {
      id: 1,
      device_id: 'sb-0001',
      hostname: 'sb-kitchen',
      ip_address: '10.0.0.1',
      backend_url: null,
      firmware_version: '1.0.0',
      has_nfc: true,
      has_scale: true,
      tare_offset: 0,
      calibration_factor: 1.0,
      nfc_reader_type: null,
      nfc_connection: null,
      display_brightness: 100,
      display_blank_timeout: 0,
      has_backlight: false,
      last_calibrated_at: null,
      last_seen: new Date().toISOString(),
      pending_command: null,
      nfc_ok: true,
      scale_ok: true,
      uptime_s: 100,
      update_status: null,
      update_message: null,
      system_stats: null,
      online: true,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    };

    it('shows device count and green bullet when at least one device is online', async () => {
      server.use(
        http.get('/api/v1/spoolbuddy/devices', () => {
          return HttpResponse.json([
            { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'sb-kitchen', online: true },
            { ...baseDevice, id: 2, device_id: 'sb-0002', hostname: 'sb-ghost', online: false },
          ]);
        })
      );
      render(<SettingsPage />);

      // Find the tab button (not the header) — it's the <button> containing the SpoolBuddy text
      const tabButton = await waitFor(() => {
        const buttons = screen.getAllByRole('button').filter((b) => b.textContent?.includes('SpoolBuddy'));
        expect(buttons.length).toBeGreaterThan(0);
        return buttons[0];
      });

      // Count pill rendered
      await waitFor(() => {
        expect(tabButton.textContent).toContain('2');
      });

      // Green status bullet (at least one device online)
      await waitFor(() => {
        expect(tabButton.querySelector('.bg-green-400')).not.toBeNull();
      });
    });

    it('shows gray bullet when all devices are offline', async () => {
      server.use(
        http.get('/api/v1/spoolbuddy/devices', () => {
          return HttpResponse.json([{ ...baseDevice, online: false }]);
        })
      );
      render(<SettingsPage />);

      const tabButton = await waitFor(() => {
        const buttons = screen.getAllByRole('button').filter((b) => b.textContent?.includes('SpoolBuddy'));
        expect(buttons.length).toBeGreaterThan(0);
        return buttons[0];
      });

      await waitFor(() => {
        expect(tabButton.querySelector('.bg-gray-500')).not.toBeNull();
        expect(tabButton.querySelector('.bg-green-400')).toBeNull();
      });
    });

    it('hides the count pill when no devices are registered', async () => {
      server.use(
        http.get('/api/v1/spoolbuddy/devices', () => HttpResponse.json([]))
      );
      render(<SettingsPage />);

      const tabButton = await waitFor(() => {
        const buttons = screen.getAllByRole('button').filter((b) => b.textContent?.includes('SpoolBuddy'));
        expect(buttons.length).toBeGreaterThan(0);
        return buttons[0];
      });

      // The only numeric content should NOT be present — tab label only
      await waitFor(() => {
        expect(tabButton.textContent).toBe('SpoolBuddy');
      });
    });
  });

  describe('API Keys tab — delete flow', () => {
    // Without setQueryData on success the deleted row stayed visible until a
    // manual reload — invalidateQueries didn't reliably trigger a UI swap on
    // every browser. Pin the synchronous-removal contract here.
    it('removes a deleted key from the list without a page reload', async () => {
      const initialKeys = [
        {
          id: 42,
          name: 'CI deploy key',
          key_prefix: 'bk_abcd1234',
          can_queue: true,
          can_control_printer: false,
          can_read_status: true,
          printer_ids: null,
          enabled: true,
          last_used: null,
          created_at: '2026-01-01T00:00:00Z',
          expires_at: null,
        },
      ];

      let deleteCallCount = 0;
      server.use(
        http.get('/api/v1/api-keys/', () => HttpResponse.json(initialKeys)),
        http.delete('/api/v1/api-keys/:id', ({ params }) => {
          deleteCallCount += 1;
          expect(params.id).toBe('42');
          return HttpResponse.json({ message: 'API key deleted' });
        })
      );

      const user = userEvent.setup();
      render(<SettingsPage />);

      // Switch to API Keys tab. Both desktop tab + mobile dropdown render
      // the label, so just grab the button form.
      await waitFor(() => {
        expect(screen.getAllByText('API Keys').length).toBeGreaterThan(0);
      });
      const tabButton = screen.getAllByText('API Keys').find((el) => el.tagName === 'BUTTON');
      expect(tabButton).toBeDefined();
      await user.click(tabButton!);

      // Key is listed
      await waitFor(() => {
        expect(screen.getByText('CI deploy key')).toBeInTheDocument();
      });

      // Click the trash button on the row
      const cards = screen.getByText('CI deploy key').closest('.flex.items-center.justify-between');
      expect(cards).not.toBeNull();
      const trashButton = cards!.querySelectorAll('button');
      await user.click(trashButton[trashButton.length - 1]);

      // Confirm the deletion in the modal
      const confirmButton = await screen.findByRole('button', { name: /delete/i });
      await user.click(confirmButton);

      // The deleted key disappears from the list immediately — no manual
      // reload required. setQueryData drops it before any refetch could fire.
      await waitFor(() => {
        expect(screen.queryByText('CI deploy key')).not.toBeInTheDocument();
      });

      expect(deleteCallCount).toBe(1);
    });
  });
});
