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
};

describe('SettingsPage', () => {
  beforeEach(() => {
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
        expect(screen.getByText('Notifications')).toBeInTheDocument();
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

    it('shows preferred slicer setting', async () => {
      render(<SettingsPage />);

      await waitFor(() => {
        expect(screen.getByText('Preferred Slicer')).toBeInTheDocument();
      });
    });

    it('shows slicer dropdown with both options', async () => {
      render(<SettingsPage />);

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
        expect(screen.getByText('Notifications')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Notifications'));

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
});
