/**
 * Tests for the SettingsPage component.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, SIDEBAR_ORDER_KEY } from '../../utils/sidebarLayout';
import { setAuthToken } from '../../api/client';

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
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    vi.mocked(localStorage.removeItem).mockReset();
    vi.mocked(localStorage.clear).mockReset();
    localStorage.clear();
    setAuthToken(null);

    server.use(
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json(mockSettings);
      }),
      http.put('/api/v1/settings/', async ({ request }) => {
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
      }),
      http.get('/api/v1/external-links/', () => {
        return HttpResponse.json([]);
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

    it('shows Administrators group without a duplicate Admin pill', async () => {
      const adminUser = {
        id: 1,
        username: 'alice',
        role: 'admin',
        is_active: true,
        is_admin: true,
        auth_source: 'local',
        groups: [{ id: 1, name: 'Administrators' }],
        permissions: ['users:read', 'groups:read', 'settings:read'],
        created_at: '2026-01-01T00:00:00Z',
      };

      window.history.replaceState({}, '', '/settings?tab=users');
      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () => HttpResponse.json(adminUser)),
        http.get('/api/v1/users/', () => HttpResponse.json([adminUser])),
      );
      setAuthToken('test-token');

      render(<SettingsPage />);

      await screen.findAllByText('Administrators');
      expect(screen.queryByText('Admin')).not.toBeInTheDocument();
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

    it('hides a Bambuddy sidebar page from Sidebar', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await screen.findByRole('heading', { name: 'Sidebar' });
      await screen.findAllByText('Visible in sidebar');

      vi.mocked(localStorage.setItem).mockClear();
      await user.click((await screen.findAllByLabelText('Hide page'))[0]);

      expect(localStorage.setItem).toHaveBeenCalledWith(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, JSON.stringify(['printers']));
      expect(screen.getByText('Hidden from sidebar')).toBeInTheDocument();
    });

    it('shows a previously hidden Bambuddy sidebar page from Sidebar', async () => {
      vi.mocked(localStorage.getItem).mockImplementation((key) => {
        if (key === SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY) return JSON.stringify(['printers']);
        return null;
      });

      const user = userEvent.setup();
      render(<SettingsPage />);

      await screen.findByRole('heading', { name: 'Sidebar' });
      await screen.findByText('Hidden from sidebar');

      vi.mocked(localStorage.setItem).mockClear();
      await user.click(await screen.findByLabelText('Show page'));

      expect(localStorage.setItem).toHaveBeenCalledWith(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, JSON.stringify([]));
      expect(screen.getAllByText('Visible in sidebar').length).toBeGreaterThan(0);
    });

    it('does not allow Settings to be hidden from Sidebar', async () => {
      render(<SettingsPage />);

      await screen.findByRole('heading', { name: 'Sidebar' });
      await screen.findByText('Required in sidebar');

      const settingsVisibilityButton = await screen.findByLabelText('Settings cannot be hidden');
      expect(settingsVisibilityButton).toBeDisabled();
      expect(screen.getByText('Required in sidebar')).toBeInTheDocument();
    });

    it('presents external links and Bambuddy pages in saved sidebar order', async () => {
      vi.mocked(localStorage.getItem).mockImplementation((key) => {
        if (key === SIDEBAR_ORDER_KEY) return JSON.stringify(['ext-7', 'printers', 'settings']);
        return null;
      });
      server.use(
        http.get('/api/v1/external-links/', () =>
          HttpResponse.json([
            {
              id: 7,
              name: 'Docs',
              url: 'https://docs.example.test',
              icon: 'Link',
              open_in_new_tab: true,
              custom_icon: null,
              sort_order: 0,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ]),
        ),
      );

      render(<SettingsPage />);

      await screen.findByRole('heading', { name: 'Sidebar' });
      const docs = await screen.findByText('Docs');
      const printers = screen.getAllByText('Printers').find(element => element.closest('[draggable="true"]'));

      expect(printers).toBeDefined();
      expect(docs.compareDocumentPosition(printers) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('saves mixed Sidebar order when items are dragged', async () => {
      server.use(
        http.get('/api/v1/external-links/', () =>
          HttpResponse.json([
            {
              id: 7,
              name: 'Docs',
              url: 'https://docs.example.test',
              icon: 'Link',
              open_in_new_tab: true,
              custom_icon: null,
              sort_order: 0,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ]),
        ),
      );

      render(<SettingsPage />);

      await screen.findByRole('heading', { name: 'Sidebar' });
      const docsRow = (await screen.findByText('Docs')).closest('[draggable="true"]');
      const printersRow = screen.getAllByText('Printers')
        .find(element => element.closest('[draggable="true"]'))
        ?.closest('[draggable="true"]');

      expect(docsRow).not.toBeNull();
      expect(printersRow).not.toBeNull();

      vi.mocked(localStorage.setItem).mockClear();
      const dataTransfer = {
        effectAllowed: '',
        dropEffect: '',
        setData: vi.fn(),
      };
      fireEvent.dragStart(docsRow!, { dataTransfer });
      fireEvent.dragOver(printersRow!, { dataTransfer });
      fireEvent.drop(printersRow!, { dataTransfer });

      expect(localStorage.setItem).toHaveBeenCalledWith(
        SIDEBAR_ORDER_KEY,
        JSON.stringify(['ext-7', 'printers', 'inventory', 'archives', 'queue', 'projects', 'files', 'makerworld', 'profiles', 'maintenance', 'stats', 'notifications', 'settings']),
      );
    });

    it('resets Sidebar to all pages first and configured links at the bottom', async () => {
      vi.mocked(localStorage.getItem).mockImplementation((key) => {
        if (key === SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY) return JSON.stringify(['printers', 'stats']);
        if (key === SIDEBAR_ORDER_KEY) return JSON.stringify(['ext-7', 'settings', 'printers']);
        return null;
      });
      server.use(
        http.get('/api/v1/external-links/', () =>
          HttpResponse.json([
            {
              id: 7,
              name: 'Docs',
              url: 'https://docs.example.test',
              icon: 'Link',
              open_in_new_tab: true,
              custom_icon: null,
              sort_order: 0,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ]),
        ),
      );

      const user = userEvent.setup();
      render(<SettingsPage />);

      const heading = await screen.findByRole('heading', { name: 'Sidebar' });
      const card = heading.closest('#card-sidebar-links');
      expect(card).not.toBeNull();
      await screen.findByText('Docs');

      vi.mocked(localStorage.setItem).mockClear();
      await user.click(within(card as HTMLElement).getByRole('button', { name: /reset/i }));

      expect(localStorage.setItem).toHaveBeenCalledWith(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, JSON.stringify([]));
      expect(localStorage.setItem).toHaveBeenCalledWith(
        SIDEBAR_ORDER_KEY,
        JSON.stringify(['printers', 'inventory', 'archives', 'queue', 'projects', 'files', 'makerworld', 'profiles', 'maintenance', 'stats', 'notifications', 'settings', 'ext-7']),
      );

      const settingsRow = screen.getAllByText('Settings')
        .find(element => element.closest('[draggable="true"]'))
        ?.closest('[draggable="true"]');
      const docsRow = screen.getByText('Docs').closest('[draggable="true"]');
      expect(settingsRow).not.toBeNull();
      expect(docsRow).not.toBeNull();
      expect(settingsRow!.compareDocumentPosition(docsRow!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.queryByText('Hidden from sidebar')).not.toBeInTheDocument();
    });

    it('sets the current Sidebar order as the backend default for settings admins', async () => {
      let defaultSidebarOrderPayload: string | null = null;
      vi.mocked(localStorage.getItem).mockImplementation((key) => {
        if (key === SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY) return JSON.stringify(['stats']);
        return null;
      });

      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 1,
            username: 'admin',
            role: 'admin',
            is_active: true,
            is_admin: false,
            groups: [{ id: 1, name: 'Administrators' }],
            permissions: ['settings:update'],
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
        http.get('/api/v1/settings/', () =>
          HttpResponse.json({ ...mockSettings, default_sidebar_order: '' }),
        ),
        http.put('/api/v1/settings/', async ({ request }) => {
          const body = await request.json() as { default_sidebar_order?: string };
          defaultSidebarOrderPayload = body.default_sidebar_order ?? null;
          return HttpResponse.json({ ...mockSettings, ...body });
        }),
      );
      setAuthToken('test-token');

      const user = userEvent.setup();
      render(<SettingsPage />);

      const heading = await screen.findByRole('heading', { name: 'Sidebar' });
      const card = heading.closest('#card-sidebar-links');
      expect(card).not.toBeNull();

      await user.click(within(card as HTMLElement).getByRole('switch', { name: 'Set Default' }));

      await waitFor(() => {
        expect(defaultSidebarOrderPayload).not.toBeNull();
      });
      expect(JSON.parse(defaultSidebarOrderPayload!)).toEqual({
        order: [
          'printers',
          'inventory',
          'archives',
          'queue',
          'projects',
          'files',
          'makerworld',
          'profiles',
          'maintenance',
          'stats',
          'notifications',
          'settings',
        ],
        hiddenSystemItemIds: ['stats'],
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

    it('shows the installer-download link for Windows installer installs', async () => {
      const downloadUrl =
        'https://github.com/maziggy/bambuddy/releases/download/v0.2.5/bambuddy-0.2.5-windows-x64-setup.exe';
      await renderWithUpdateCheck({
        update_available: true,
        current_version: '0.2.4',
        latest_version: '0.2.5',
        release_name: '0.2.5',
        release_notes: '',
        release_url: 'https://github.com/maziggy/bambuddy/releases/tag/v0.2.5',
        published_at: '2099-01-01T00:00:00Z',
        is_docker: false,
        is_ha_addon: false,
        is_windows_installer: true,
        update_method: 'windows_installer',
        installer_download_url: downloadUrl,
      });

      const link = await screen.findByRole('link', { name: /download installer for v0\.2\.5/i });
      expect(link).toHaveAttribute('href', downloadUrl);
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
      // The in-app update button must NOT render — the git-fetch path can't
      // work from an installer payload.
      expect(screen.queryByRole('button', { name: /install update/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/Home Assistant Supervisor/i)).not.toBeInTheDocument();
      expect(screen.queryByText('docker compose pull && docker compose up -d')).not.toBeInTheDocument();
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

    it('shows per-filament humidity threshold editor on Workflow tab (#1605)', async () => {
      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Workflow')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Workflow'));

      await waitFor(() => {
        expect(screen.getByText('Humidity Thresholds')).toBeInTheDocument();
        // Default row is unique to the humidity editor (drying presets has no
        // default row), so we can pin it without disambiguating from the
        // adjacent drying-presets table that also lists PLA/ASA/etc.
        expect(screen.getByText('Default (unknown types)')).toBeInTheDocument();
        // Filament rows render in both tables — assert by count instead of
        // a single getByText. 8 default filaments × 2 tables = 16 PLAs etc.
        expect(screen.getAllByText('PLA').length).toBeGreaterThanOrEqual(2);
        expect(screen.getAllByText('ASA').length).toBeGreaterThanOrEqual(2);
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

  describe('API Keys tab — #1182 cloud access + ownership UI', () => {
    // The list now exposes two new bits of information per row:
    //   - "Cloud" badge when can_access_cloud=true
    //   - "Legacy" badge when user_id IS NULL (created before per-user ownership)
    // These tell the operator at a glance which keys can read /cloud/* data
    // and which keys need to be recreated to gain that capability.
    it('renders the Cloud badge for keys with can_access_cloud=true and the Legacy badge for ownerless keys', async () => {
      const keys = [
        {
          id: 1,
          name: 'cloud-reader',
          key_prefix: 'bk_cloud123',
          user_id: 7,
          can_queue: false,
          can_control_printer: false,
          can_read_status: true,
          can_access_cloud: true,
          printer_ids: null,
          enabled: true,
          last_used: null,
          created_at: '2026-04-30T00:00:00Z',
          expires_at: null,
        },
        {
          id: 2,
          name: 'legacy-key',
          key_prefix: 'bk_legacy01',
          user_id: null,
          can_queue: true,
          can_control_printer: false,
          can_read_status: true,
          can_access_cloud: false,
          printer_ids: null,
          enabled: true,
          last_used: null,
          created_at: '2025-01-01T00:00:00Z',
          expires_at: null,
        },
      ];

      server.use(http.get('/api/v1/api-keys/', () => HttpResponse.json(keys)));

      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('API Keys').length).toBeGreaterThan(0);
      });
      const tabButton = screen.getAllByText('API Keys').find((el) => el.tagName === 'BUTTON');
      await user.click(tabButton!);

      await waitFor(() => {
        expect(screen.getByText('cloud-reader')).toBeInTheDocument();
        expect(screen.getByText('legacy-key')).toBeInTheDocument();
      });

      // Cloud-enabled key gets the Cloud badge but NOT the Legacy badge.
      const cloudRow = screen.getByText('cloud-reader').closest('.flex.items-center.justify-between');
      expect(cloudRow).not.toBeNull();
      expect(cloudRow!.textContent).toContain('Cloud');
      expect(cloudRow!.textContent).not.toContain('Legacy');

      // Ownerless key gets Legacy but NOT Cloud (can_access_cloud=false).
      const legacyRow = screen.getByText('legacy-key').closest('.flex.items-center.justify-between');
      expect(legacyRow).not.toBeNull();
      expect(legacyRow!.textContent).toContain('Legacy');
      // Strip the Cloud-flag check by limiting to badge area — the
      // "Allow cloud access" text from the create form isn't visible here.
      expect(legacyRow!.querySelector('.bg-purple-500\\/20')).toBeNull();
    });

    it('passes can_access_cloud through to the create call when the toggle is checked', async () => {
      let posted: { name?: string; can_access_cloud?: boolean } | null = null;

      server.use(
        http.get('/api/v1/api-keys/', () => HttpResponse.json([])),
        http.post('/api/v1/api-keys/', async ({ request }) => {
          posted = (await request.json()) as { name?: string; can_access_cloud?: boolean };
          return HttpResponse.json({
            id: 99,
            key: 'bk_returnedkey',
            name: posted.name,
            key_prefix: 'bk_returne',
            user_id: 1,
            can_queue: true,
            can_control_printer: false,
            can_read_status: true,
            can_access_cloud: posted.can_access_cloud ?? false,
            printer_ids: null,
            enabled: true,
            last_used: null,
            created_at: '2026-05-01T00:00:00Z',
            expires_at: null,
          });
        })
      );

      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('API Keys').length).toBeGreaterThan(0);
      });
      const tabButton = screen.getAllByText('API Keys').find((el) => el.tagName === 'BUTTON');
      await user.click(tabButton!);

      // Open the create form. With an empty key list the empty-state card
      // shows "Create Your First Key" — click that to open the form.
      const openButton = await screen.findByRole('button', { name: /Create Your First Key/i });
      await user.click(openButton);

      // Tick the new "Allow cloud access" checkbox. The label wraps the
      // input AND a sibling description div, so getByLabelText doesn't
      // resolve via implicit-label traversal — locate via text + closest
      // label, then grab the checkbox from the same scope.
      const cloudLabelText = await screen.findByText(/Allow cloud access/i);
      const cloudLabel = cloudLabelText.closest('label');
      expect(cloudLabel).not.toBeNull();
      const cloudCheckbox = cloudLabel!.querySelector('input[type="checkbox"]') as HTMLInputElement;
      expect(cloudCheckbox).not.toBeNull();
      await user.click(cloudCheckbox);

      // Submit. Two "Create Key" buttons exist once the form is open (header
      // CTA + form footer); the form-footer one is the actual submit and
      // calls the mutation — find it by walking up from the cloud checkbox
      // we just clicked, since both share the same form container.
      const submitButtons = screen.getAllByRole('button', { name: /^Create Key$/i });
      // Footer submit is the one inside the same form section as the
      // checkbox. The header CTA is in a separate flex row.
      const formSubmit = submitButtons.find(
        (b) => b.closest('div')?.contains(cloudCheckbox) || cloudLabel?.parentElement?.parentElement?.contains(b),
      );
      await user.click(formSubmit ?? submitButtons[submitButtons.length - 1]);

      await waitFor(() => {
        expect(posted).not.toBeNull();
        expect(posted!.can_access_cloud).toBe(true);
      });
    });
  });

  describe('API Keys tab — #1356 energy-cost write scope', () => {
    /**
     * The narrowly-scoped settings-write toggle. We pin two contracts here:
     *
     *   1. The "Energy" badge renders for keys that have can_update_energy_cost=true.
     *      Without a visible signal, an operator can't tell which key in their
     *      list is the one their HA automation depends on.
     *   2. The create form sends can_update_energy_cost=true to the backend
     *      when the toggle is checked. The whole point of #1356 is that the
     *      flag must actually be persisted — a UI that drops it silently
     *      would put us right back where the bug started.
     */
    it('renders the Energy badge for keys with can_update_energy_cost=true', async () => {
      const keys = [
        {
          id: 1,
          name: 'tariff-pusher',
          key_prefix: 'bk_tariff01',
          user_id: 7,
          can_queue: false,
          can_control_printer: false,
          can_read_status: true,
          can_access_cloud: false,
          can_update_energy_cost: true,
          printer_ids: null,
          enabled: true,
          last_used: null,
          created_at: '2026-05-15T00:00:00Z',
          expires_at: null,
        },
      ];

      server.use(http.get('/api/v1/api-keys/', () => HttpResponse.json(keys)));

      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('API Keys').length).toBeGreaterThan(0);
      });
      const tabButton = screen.getAllByText('API Keys').find((el) => el.tagName === 'BUTTON');
      await user.click(tabButton!);

      await waitFor(() => {
        expect(screen.getByText('tariff-pusher')).toBeInTheDocument();
      });

      const row = screen.getByText('tariff-pusher').closest('.flex.items-center.justify-between');
      expect(row).not.toBeNull();
      expect(row!.textContent).toContain('Energy');
    });

    it('passes can_update_energy_cost through to the create call when the toggle is checked', async () => {
      let posted: { name?: string; can_update_energy_cost?: boolean } | null = null;

      server.use(
        http.get('/api/v1/api-keys/', () => HttpResponse.json([])),
        http.post('/api/v1/api-keys/', async ({ request }) => {
          posted = (await request.json()) as { name?: string; can_update_energy_cost?: boolean };
          return HttpResponse.json({
            id: 99,
            key: 'bk_returnedkey',
            name: posted.name,
            key_prefix: 'bk_returne',
            user_id: 1,
            can_queue: true,
            can_control_printer: false,
            can_read_status: true,
            can_access_cloud: false,
            can_update_energy_cost: posted.can_update_energy_cost ?? false,
            printer_ids: null,
            enabled: true,
            last_used: null,
            created_at: '2026-05-15T00:00:00Z',
            expires_at: null,
          });
        })
      );

      const user = userEvent.setup();
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getAllByText('API Keys').length).toBeGreaterThan(0);
      });
      const tabButton = screen.getAllByText('API Keys').find((el) => el.tagName === 'BUTTON');
      await user.click(tabButton!);

      const openButton = await screen.findByRole('button', { name: /Create Your First Key/i });
      await user.click(openButton);

      const energyLabelText = await screen.findByText(/Update electricity price/i);
      const energyLabel = energyLabelText.closest('label');
      expect(energyLabel).not.toBeNull();
      const energyCheckbox = energyLabel!.querySelector('input[type="checkbox"]') as HTMLInputElement;
      expect(energyCheckbox).not.toBeNull();
      await user.click(energyCheckbox);

      const submitButtons = screen.getAllByRole('button', { name: /^Create Key$/i });
      const formSubmit = submitButtons.find(
        (b) => b.closest('div')?.contains(energyCheckbox) || energyLabel?.parentElement?.parentElement?.contains(b),
      );
      await user.click(formSubmit ?? submitButtons[submitButtons.length - 1]);

      await waitFor(() => {
        expect(posted).not.toBeNull();
        expect(posted!.can_update_energy_cost).toBe(true);
      });
    });
  });

  describe('external camera snapshot URL override (#1177)', () => {
    /**
     * The snapshot URL input only appears for stream camera types where the
     * MJPEG warm-up problem can occur (mjpeg / rtsp / usb). Pure HTTP
     * snapshot sources don't need an override since their stream URL is
     * already a single-frame endpoint.
     */
    const mjpegPrinter = {
      id: 7,
      name: 'go2rtc Cam',
      serial_number: 'TEST123',
      ip_address: '192.168.1.100',
      access_code: 'XXXX',
      model: 'P1S',
      location: null,
      nozzle_count: 1,
      is_active: true,
      auto_archive: true,
      external_camera_url: 'http://192.168.1.61:1984/api/stream.mjpeg?src=printer',
      external_camera_type: 'mjpeg',
      external_camera_enabled: true,
      external_camera_snapshot_url: null,
      camera_rotation: 0,
      plate_detection_enabled: false,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };

    it('renders the snapshot URL input when camera_type is mjpeg', async () => {
      server.use(
        http.get('/api/v1/printers/', () => HttpResponse.json([mjpegPrinter])),
      );

      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/api\/frame\.jpeg\?src=printer/)).toBeInTheDocument();
      });
    });

    it('hides the snapshot URL input when camera_type is snapshot (already a single-frame source)', async () => {
      server.use(
        http.get('/api/v1/printers/', () =>
          HttpResponse.json([{ ...mjpegPrinter, external_camera_type: 'snapshot' }]),
        ),
      );

      render(<SettingsPage />);

      // Wait for the live-stream URL placeholder to render so we know the
      // camera section finished mounting before asserting absence of the
      // snapshot input below.
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/Camera URL/i)).toBeInTheDocument();
      });
      expect(screen.queryByPlaceholderText(/api\/frame\.jpeg\?src=printer/)).not.toBeInTheDocument();
    });

    it(
      'PATCHes the printer with external_camera_snapshot_url when the user types into the input',
      async () => {
        let receivedBody: Record<string, unknown> | null = null;
        server.use(
          http.get('/api/v1/printers/', () => HttpResponse.json([mjpegPrinter])),
          http.patch('/api/v1/printers/7', async ({ request }) => {
            receivedBody = (await request.json()) as Record<string, unknown>;
            return HttpResponse.json({ ...mjpegPrinter, ...receivedBody });
          }),
        );

        render(<SettingsPage />);

        const input = await waitFor(() =>
          screen.getByPlaceholderText(/api\/frame\.jpeg\?src=printer/),
        );

        const user = userEvent.setup();
        await user.type(input, 'http://192.168.1.61:1984/api/frame.jpeg?src=printer');

        // Save is debounced by 800ms; assert the PATCH eventually fires with
        // the typed snapshot URL.
        await waitFor(
          () => {
            expect(receivedBody).not.toBeNull();
            expect(receivedBody!.external_camera_snapshot_url).toBe(
              'http://192.168.1.61:1984/api/frame.jpeg?src=printer',
            );
          },
          { timeout: 5000 },
        );
      },
      // Per-test timeout raised to 15s — `user.type()` of a 49-char URL plus
      // the 800ms save debounce fits in 5s locally (~2.3s typical) but blows
      // past it on slow GitHub Actions runners (5000ms timeout was the failure
      // mode on PR #1263).
      15_000,
    );
  });

  describe('theme mode buttons', () => {
    it('renders Dark, Light, and System buttons', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Dark' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Light' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'System' })).toBeInTheDocument();
      });
    });

    it('highlights the active mode button with green border', async () => {
      render(<SettingsPage />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'System' })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: 'System' }));

      await waitFor(() => {
        const systemBtn = screen.getByRole('button', { name: 'System' });
        expect(systemBtn.className).toContain('border-bambu-green');
      });
    });

    it('clicking a theme button switches mode', async () => {
      localStorage.setItem('theme-mode', 'dark');
      render(<SettingsPage />);
      const user = userEvent.setup();

      await waitFor(() => {
        const darkBtn = screen.getByRole('button', { name: 'Dark' });
        expect(darkBtn.className).toContain('border-bambu-green');
      });

      const lightBtn = screen.getByRole('button', { name: 'Light' });
      await user.click(lightBtn);

      await waitFor(() => {
        expect(lightBtn.className).toContain('border-bambu-green');
      });
    });

    it('shows a toast when theme button is clicked', async () => {
      render(<SettingsPage />);
      const user = userEvent.setup();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'System' })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: 'System' }));

      await waitFor(() => {
        expect(screen.getByText('Settings saved')).toBeInTheDocument();
      });
    });
  });

  // --------------------------------------------------------------------
  // Slicer Pipelines (#1425) — Workflow tab sub-tabs
  // --------------------------------------------------------------------
  describe('workflow sub-tabs (#1425)', () => {
    beforeEach(() => {
      // Endpoints the Pipelines panel calls (#1425).
      server.use(
        http.get('/api/v1/slicer-pipelines/', () => HttpResponse.json({ pipelines: [] })),
        http.get('/api/v1/slicer/presets', () =>
          HttpResponse.json({
            orca_cloud: { printer: [], process: [], filament: [] },
            cloud: { printer: [], process: [], filament: [] },
            local: { printer: [], process: [], filament: [] },
            standard: { printer: [], process: [], filament: [] },
            cloud_status: 'ok',
            orca_cloud_status: 'ok',
          }),
        ),
      );
    });

    it('renders Queue & Dispatch + Pipelines sub-tabs under Workflow', async () => {
      render(<SettingsPage />);
      const user = userEvent.setup();
      await waitFor(() => {
        // Workflow tab in the sidebar — exact match to avoid colliding with
        // "Print Queue" or "Queue Settings" labels elsewhere on the page.
        expect(screen.getByRole('button', { name: 'Workflow' })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: 'Workflow' }));
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Queue & Dispatch/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /^Pipelines$/i })).toBeInTheDocument();
      });
    });

    it('clicking Pipelines sub-tab shows the empty-state hint and updates the URL', async () => {
      render(<SettingsPage />);
      const user = userEvent.setup();
      await waitFor(() => expect(screen.getByRole('button', { name: 'Workflow' })).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: 'Workflow' }));
      await user.click(screen.getByRole('button', { name: /^Pipelines$/i }));

      await waitFor(() => {
        expect(screen.getByText(/No pipelines yet/i)).toBeInTheDocument();
        // Deep-link URL carries both ?tab=queue and ?sub=pipelines
        expect(window.location.search).toContain('tab=queue');
        expect(window.location.search).toContain('sub=pipelines');
      });
    });
  });
});

/**
 * Sponsor banner on Settings -> General.
 *
 * Below the fleet threshold it makes the community/donation ask; at or above it
 * the same slot makes the commercial ask and points at business.html. A print
 * farm asked to chip in $5 is a wasted impression, and a hobbyist pitched a
 * support contract is an annoyed user — so both directions are pinned.
 */
describe('SettingsPage — sponsor banner audience', () => {
  beforeEach(() => {
    // BrowserRouter shares window.location across tests and the banner only
    // renders on the General tab — without this reset a prior test's ?tab=queue
    // leaks in and the banner never mounts.
    window.history.replaceState({}, '', '/');
  });

  const fleet = (count: number) =>
    server.use(
      http.get('/api/v1/printers/', () =>
        HttpResponse.json(
          Array.from({ length: count }, (_, i) => ({
            id: i + 1,
            name: `Printer ${i + 1}`,
            serial_number: `SN${i + 1}`,
            ip_address: '192.168.1.10',
            model: 'X1C',
            is_active: true,
          })),
        ),
      ),
    );

  it('shows the community ask for a small fleet', async () => {
    fleet(2);
    render(<SettingsPage />);

    const banner = await screen.findByRole('link', { name: /Independent & community-funded/i });
    expect(banner).toHaveAttribute('href', 'https://bambuddy.cool/sponsors.html?from=app-settings');
    expect(screen.queryByText(/Bambuddy for business/i)).not.toBeInTheDocument();
  });

  it('shows the commercial ask for a business-sized fleet', async () => {
    fleet(6);
    render(<SettingsPage />);

    const banner = await screen.findByRole('link', { name: /Bambuddy for business/i });
    expect(banner).toHaveAttribute('href', 'https://bambuddy.cool/business.html?from=app-settings');
    // The donation copy is replaced, not merely supplemented.
    expect(screen.queryByText(/Independent & community-funded/i)).not.toBeInTheDocument();
    // The ask names the fleet back to them.
    expect(screen.getByText(/6 printers/i)).toBeInTheDocument();
  });
});
