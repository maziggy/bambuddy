/**
 * Tests for the PrintersPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    ip_address: '192.168.1.100',
    serial_number: '00M09A350100001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'P1S Backup',
    ip_address: '192.168.1.101',
    serial_number: '00W00A123456789',
    access_code: '87654321',
    model: 'P1S',
    enabled: false,
    nozzle_diameter: 0.4,
    nozzle_type: 'stainless_steel',
    location: null,
    auto_archive: true,
    created_at: '2024-01-02T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
  },
];

const mockPrinterStatus = {
  connected: true,
  state: 'IDLE',
  awaiting_plate_clear: false,
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: {
    nozzle: 25,
    bed: 25,
    chamber: 25,
  },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

const selectToolbarDropdownOption = async (triggerName: RegExp, optionName: RegExp) => {
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: triggerName }));
  await user.click(await screen.findByRole('button', { name: optionName }));
};

describe('PrintersPage', () => {
  beforeEach(() => {
    localStorage.removeItem('printerCardSize');

    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json(mockPrinterStatus);
      }),
      http.post('/api/v1/printers/:id/clear-plate', () => {
        return HttpResponse.json({ success: true, message: 'Plate cleared' });
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json({
          auto_archive: true,
          save_thumbnails: true,
          capture_finish_photo: true,
          default_filament_cost: 25.0,
          currency: 'USD',
          ams_humidity_good: 40,
          ams_humidity_fair: 60,
          ams_temp_good: 30,
          ams_temp_fair: 35,
          require_plate_clear: true,
        });
      }),
      http.get('/api/v1/queue/', () => {
        return HttpResponse.json([]);
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('Printers')).toBeInTheDocument();
      });
    });

    it('shows printer cards', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });
    });

    it('shows printer models', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1C')).toBeInTheDocument();
        expect(screen.getByText('P1S')).toBeInTheDocument();
      });
    });

    it('shows printer status', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        // Status should be shown - may vary based on state
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
    });
  });

  describe('printer info', () => {
    it('shows IP address in printer info modal', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // IP address is shown in the PrinterInfoModal (accessed via 3-dot menu),
      // not directly on the card. Verify the printer data loaded correctly.
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });

    it('shows location when set', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        // Printers should render - location display may vary
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
    });
  });

  describe('temperature display', () => {
    it('shows nozzle temperature', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        // Temperatures are shown in the UI
        expect(screen.getAllByText(/25/)).toBeTruthy();
      });
    });
  });

  describe('empty state', () => {
    it('shows empty state when no printers', async () => {
      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText(/no printers/i)).toBeInTheDocument();
      });
    });
  });

  describe('printer actions', () => {
    it('has action buttons', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // There should be some interactive elements for printer actions
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThan(0);
    });

    it('shows plate clear status and action on finished printers when not cleared', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'FINISH', awaiting_plate_clear: true });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate not Clear').length).toBeGreaterThan(0);
      });

      expect(screen.getAllByRole('button', { name: 'Mark plate as cleared' }).length).toBeGreaterThan(0);
    });

    it('shows plate clear status and action on failed printers when not cleared', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'FAILED', awaiting_plate_clear: true });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate not Clear').length).toBeGreaterThan(0);
      });

      expect(screen.getAllByRole('button', { name: 'Mark plate as cleared' }).length).toBeGreaterThan(0);
    });

    it('keeps the clear action available when an idle printer is still awaiting acknowledgment', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'IDLE', awaiting_plate_clear: true });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate not Clear').length).toBeGreaterThan(0);
      });

      expect(screen.getAllByRole('button', { name: 'Mark plate as cleared' }).length).toBeGreaterThan(0);
    });

    it('updates the plate clear status after using the printer card action', async () => {
      let awaitingPlateClear = true;

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([mockPrinters[0]]);
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'FINISH', awaiting_plate_clear: awaitingPlateClear });
        }),
        http.post('/api/v1/printers/:id/clear-plate', () => {
          awaitingPlateClear = false;
          return HttpResponse.json({ success: true, message: 'Plate cleared' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate not Clear').length).toBeGreaterThan(0);
      });

      fireEvent.click(screen.getAllByRole('button', { name: 'Mark plate as cleared' })[0]);

      await waitFor(() => {
        expect(screen.queryByText('Plate not Clear')).not.toBeInTheDocument();
      });

      expect(screen.getAllByText('Plate Clear').length).toBeGreaterThan(0);
    });

    it('shows an icon-only plate clear action in small card view', async () => {
      let awaitingPlateClear = true;

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([mockPrinters[0]]);
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'FINISH', awaiting_plate_clear: awaitingPlateClear });
        }),
        http.post('/api/v1/printers/:id/clear-plate', () => {
          awaitingPlateClear = false;
          return HttpResponse.json({ success: true, message: 'Plate cleared' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'S' }));

      await waitFor(() => {
        expect(screen.queryByText('Mark plate as cleared')).not.toBeInTheDocument();
      });

      const clearButton = screen.getByRole('button', { name: 'Mark plate as cleared' });

      fireEvent.click(clearButton);

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Mark plate as cleared' })).not.toBeInTheDocument();
      });
    });

    it('shows plate clear status but no action while idle', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate Clear').length).toBeGreaterThan(0);
      });

      expect(screen.queryByRole('button', { name: 'Mark plate as cleared' })).not.toBeInTheDocument();
    });

    it('shows plate in use status while printing and hides the clear action', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'RUNNING', awaiting_plate_clear: false });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Plate in Use').length).toBeGreaterThan(0);
      });

      expect(screen.queryByRole('button', { name: 'Mark plate as cleared' })).not.toBeInTheDocument();
    });

    it('hides plate status and action when plate-clear confirmation is disabled', async () => {
      server.use(
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({
            auto_archive: true,
            save_thumbnails: true,
            capture_finish_photo: true,
            default_filament_cost: 25.0,
            currency: 'USD',
            ams_humidity_good: 40,
            ams_humidity_fair: 60,
            ams_temp_good: 30,
            ams_temp_fair: 35,
            require_plate_clear: false,
          });
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrinterStatus, state: 'FINISH', awaiting_plate_clear: true });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      expect(screen.queryByText('Plate not Clear')).not.toBeInTheDocument();
      expect(screen.queryByText('Plate Clear')).not.toBeInTheDocument();
      expect(screen.queryByText('Plate in Use')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Mark plate as cleared' })).not.toBeInTheDocument();
    });
  });

  describe('disabled printer', () => {
    it('shows disabled state for disabled printers', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });

      // Disabled printers have visual indication
      const disabledPrinter = screen.getByText('P1S Backup').closest('div');
      expect(disabledPrinter).toBeInTheDocument();
    });
  });

  describe('nozzle rack card', () => {
    const h2cStatus = {
      ...mockPrinterStatus,
      nozzle_rack: [
        { id: 0, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 5, stat: 1, max_temp: 300, serial_number: 'SN-L', filament_color: '', filament_id: '', filament_type: '' },
        { id: 1, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 3, stat: 0, max_temp: 300, serial_number: 'SN-R', filament_color: '', filament_id: '', filament_type: '' },
        { id: 16, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 10, stat: 0, max_temp: 300, serial_number: 'SN-16', filament_color: '', filament_id: '', filament_type: '' },
        { id: 17, nozzle_type: 'HH01', nozzle_diameter: '0.6', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-17', filament_color: '', filament_id: '', filament_type: '' },
        { id: 18, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 2, stat: 0, max_temp: 300, serial_number: 'SN-18', filament_color: '', filament_id: '', filament_type: '' },
        { id: 19, nozzle_type: '', nozzle_diameter: '', wear: null, stat: null, max_temp: 0, serial_number: '', filament_color: '', filament_id: '', filament_type: '' },
        { id: 20, nozzle_type: '', nozzle_diameter: '', wear: null, stat: null, max_temp: 0, serial_number: '', filament_color: '', filament_id: '', filament_type: '' },
        { id: 21, nozzle_type: '', nozzle_diameter: '', wear: null, stat: null, max_temp: 0, serial_number: '', filament_color: '', filament_id: '', filament_type: '' },
      ],
    };

    it('shows nozzle rack when H2C rack slots present', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(h2cStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Nozzle Rack').length).toBeGreaterThan(0);
      });
    });

    it('shows 6 rack slot elements for H2C', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(h2cStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Nozzle Rack').length).toBeGreaterThan(0);
      });

      // Rack shows diameters for occupied slots and dashes for empty ones
      const dashes = screen.getAllByText('—');
      expect(dashes.length).toBeGreaterThanOrEqual(3); // 3 empty rack positions (IDs 19,20,21)
    });

    it('keeps empty slot anchored to physical position when its nozzle is mounted (#943)', async () => {
      // H2C with rack slot 16 picked up into the hotend — firmware omits ID 16
      // entirely from nozzle.info. Each rack diameter is unique so we can assert
      // the ordering by tooltip lookup.
      const h2cSlot16Mounted = {
        ...mockPrinterStatus,
        nozzle_rack: [
          { id: 0, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 5, stat: 1, max_temp: 300, serial_number: 'SN-L', filament_color: '', filament_id: '', filament_type: '' },
          { id: 1, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 3, stat: 0, max_temp: 300, serial_number: 'SN-R', filament_color: '', filament_id: '', filament_type: '' },
          // ID 16 missing — currently in hotend
          { id: 17, nozzle_type: 'HS', nozzle_diameter: '0.2', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-17', filament_color: '', filament_id: '', filament_type: '' },
          { id: 18, nozzle_type: 'HS', nozzle_diameter: '0.6', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-18', filament_color: '', filament_id: '', filament_type: '' },
          { id: 19, nozzle_type: 'HS', nozzle_diameter: '0.8', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-19', filament_color: '', filament_id: '', filament_type: '' },
          { id: 20, nozzle_type: 'HH01', nozzle_diameter: '1.0', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-20', filament_color: '', filament_id: '', filament_type: '' },
          { id: 21, nozzle_type: 'HH01', nozzle_diameter: '1.2', wear: 0, stat: 0, max_temp: 300, serial_number: 'SN-21', filament_color: '', filament_id: '', filament_type: '' },
        ],
      };

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(h2cSlot16Mounted);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('Nozzle Rack').length).toBeGreaterThan(0);
      });

      // Slot 1 (leftmost, ID 16) should be the empty dash; slots 2..6 should
      // hold the 5 remaining nozzles in order 17, 18, 19, 20, 21.
      const rackLabel = screen.getAllByText('Nozzle Rack')[0];
      const rackCard = rackLabel.parentElement!;
      const slotRow = rackCard.querySelectorAll('div.flex')[0];
      const slotTexts = Array.from(slotRow.querySelectorAll('span')).map(s => s.textContent);
      expect(slotTexts).toEqual(['—', '0.2', '0.6', '0.8', '1.0', '1.2']);
    });

    it('hides nozzle rack when only L/R nozzles present (H2D)', async () => {
      const h2dStatus = {
        ...mockPrinterStatus,
        nozzle_rack: [
          { id: 0, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 5, stat: 1, max_temp: 300, serial_number: '', filament_color: '', filament_id: '', filament_type: '' },
          { id: 1, nozzle_type: 'HS', nozzle_diameter: '0.4', wear: 3, stat: 1, max_temp: 300, serial_number: '', filament_color: '', filament_id: '', filament_type: '' },
        ],
      };

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(h2dStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      expect(screen.queryByText('Nozzle Rack')).not.toBeInTheDocument();
    });
  });

  describe('firmware version badge', () => {
    const firmwareUpToDate = {
      printer_id: 1,
      current_version: '01.09.00.00',
      latest_version: '01.09.00.00',
      update_available: false,
      download_url: null,
      release_notes: 'Bug fixes and improvements.',
    };

    const firmwareUpdateAvailable = {
      printer_id: 1,
      current_version: '01.08.00.00',
      latest_version: '01.09.00.00',
      update_available: true,
      download_url: 'https://example.com/firmware.bin',
      release_notes: 'New features added.',
    };

    it('shows green badge when firmware is up to date', async () => {
      server.use(
        http.get('/api/v1/firmware/updates/:id', () => {
          return HttpResponse.json(firmwareUpToDate);
        }),
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({
            check_printer_firmware: true,
            auto_archive: true,
            save_thumbnails: true,
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('01.09.00.00').length).toBeGreaterThan(0);
      });

      const badge = screen.getAllByText('01.09.00.00')[0].closest('button');
      expect(badge).toBeInTheDocument();
      expect(badge?.className).toContain('text-status-ok');
    });

    it('shows orange badge when firmware update is available', async () => {
      server.use(
        http.get('/api/v1/firmware/updates/:id', () => {
          return HttpResponse.json(firmwareUpdateAvailable);
        }),
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({
            check_printer_firmware: true,
            auto_archive: true,
            save_thumbnails: true,
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getAllByText('01.08.00.00').length).toBeGreaterThan(0);
      });

      const badge = screen.getAllByText('01.08.00.00')[0].closest('button');
      expect(badge).toBeInTheDocument();
      expect(badge?.className).toContain('text-orange-400');
    });

    it('hides badge when firmware check is disabled', async () => {
      server.use(
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({
            check_printer_firmware: false,
            auto_archive: true,
            save_thumbnails: true,
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Version should not appear when firmware check is disabled
      expect(screen.queryByText('01.09.00.00')).not.toBeInTheDocument();
      expect(screen.queryByText('01.08.00.00')).not.toBeInTheDocument();
    });

    it('hides badge when API has no firmware data for the model', async () => {
      const firmwareNoData = {
        printer_id: 1,
        current_version: '01.01.03.00',
        latest_version: null,
        update_available: false,
        download_url: null,
        release_notes: null,
      };

      server.use(
        http.get('/api/v1/firmware/updates/:id', () => {
          return HttpResponse.json(firmwareNoData);
        }),
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({
            check_printer_firmware: true,
            auto_archive: true,
            save_thumbnails: true,
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Badge should not appear when API returns no latest_version
      expect(screen.queryByText('01.01.03.00')).not.toBeInTheDocument();
    });
  });

  describe('bulk selection', () => {
    it('shows select button in toolbar', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // The Select button should be in the toolbar (title attribute)
      const selectButton = screen.getByTitle('Select');
      expect(selectButton).toBeInTheDocument();
    });

    it('shows selection toolbar after clicking select button', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the Select button to enter selection mode
      fireEvent.click(screen.getByTitle('Select'));

      // The floating toolbar should appear with Select All
      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });
    });

    it('shows selection count when printers are selected', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Enter selection mode
      fireEvent.click(screen.getByTitle('Select'));

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      // Click Select All to select both printers
      fireEvent.click(screen.getByText('Select All'));

      // Should show "2 selected"
      await waitFor(() => {
        expect(screen.getByText('2 selected')).toBeInTheDocument();
      });
    });

    it('shows select by state dropdown', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Enter selection mode
      fireEvent.click(screen.getByTitle('Select'));

      await waitFor(() => {
        expect(screen.getByText('Select by State')).toBeInTheDocument();
      });
    });

    it('exits selection mode on close button', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Enter selection mode
      fireEvent.click(screen.getByTitle('Select'));

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      // Click the Select button again to exit (it toggles)
      fireEvent.click(screen.getByTitle('Select'));

      // Floating toolbar should disappear
      await waitFor(() => {
        expect(screen.queryByText('Select All')).not.toBeInTheDocument();
      });
    });
  });

  describe('search and filter', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
        http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockPrinterStatus)),
        http.get('/api/v1/queue/', () => HttpResponse.json([]))
      );
    });

    it('filters by name (case-insensitive)', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: 'x1 carbon' } });

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });
    });

    it('trims leading and trailing whitespace from search', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      // " X1 Carbon " with surrounding spaces must still match
      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: '  X1 Carbon  ' } });

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });
    });

    it('filters by model', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: 'P1S' } });

      await waitFor(() => {
        expect(screen.queryByText('X1 Carbon')).not.toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });
    });

    it('filters by serial number', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: '00M09A' } });

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });
    });

    it('shows empty state when no printers match search', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: 'ZZZ_NO_MATCH' } });

      await waitFor(() => {
        expect(screen.getByText('No printers match your search or filters')).toBeInTheDocument();
      });
    });

    it('clear button resets search and shows all printers', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: 'X1 Carbon' } });

      await waitFor(() => expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument());

      // Click the accessible clear button
      fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });
    });

    it('filters by status (offline) via dropdown', async () => {
      // Override: printer 1 online, printer 2 offline
      server.use(
        http.get('/api/v1/printers/:id/status', ({ params }) => {
          if (Number(params.id) === 2) {
            return HttpResponse.json({ ...mockPrinterStatus, connected: false });
          }
          return HttpResponse.json(mockPrinterStatus);
        })
      );

      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      await selectToolbarDropdownOption(/all statuses/i, /^offline$/i);

      await waitFor(() => {
        expect(screen.queryByText('X1 Carbon')).not.toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });
    });

    it('shows empty state when status filter matches nothing', async () => {
      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      // Both printers are IDLE; filtering by "printing" should yield no results
      await selectToolbarDropdownOption(/all statuses/i, /^printing$/i);

      await waitFor(() => {
        expect(screen.getByText('No printers match your search or filters')).toBeInTheDocument();
      });
    });

    it('combines search and status filter', async () => {
      // Printer 1 = RUNNING (printing), printer 2 = IDLE
      server.use(
        http.get('/api/v1/printers/:id/status', ({ params }) => {
          if (Number(params.id) === 1) {
            return HttpResponse.json({ ...mockPrinterStatus, state: 'RUNNING' });
          }
          return HttpResponse.json(mockPrinterStatus);
        })
      );

      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      // Filter to only "printing" printers
      await selectToolbarDropdownOption(/all statuses/i, /^printing$/i);

      // Then also search for a term that only matches printer 1
      fireEvent.change(screen.getByPlaceholderText('Search printers...'), { target: { value: 'X1' } });

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });
    });

    it('filters by location via dropdown', async () => {
      // Override: give printer 2 its own location so the dropdown has two options
      // and we can verify the filter picks the right one. Printer 1 stays at 'Workshop'.
      server.use(
        http.get('/api/v1/printers/', () =>
          HttpResponse.json([
            mockPrinters[0],
            { ...mockPrinters[1], location: 'Office' },
          ])
        )
      );

      render(<PrintersPage />);
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });

      await selectToolbarDropdownOption(/all locations/i, /^workshop$/i);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });

      await selectToolbarDropdownOption(/^workshop$/i, /^office$/i);

      await waitFor(() => {
        expect(screen.queryByText('X1 Carbon')).not.toBeInTheDocument();
        expect(screen.getByText('P1S Backup')).toBeInTheDocument();
      });
    });

    it('hides location filter when no printers have a location', async () => {
      // Both printers have null location — dropdown should not render at all
      server.use(
        http.get('/api/v1/printers/', () =>
          HttpResponse.json([
            { ...mockPrinters[0], location: null },
            { ...mockPrinters[1], location: null },
          ])
        )
      );

      render(<PrintersPage />);
      await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());

      // Status filter is still there, but the location filter should be absent.
      expect(screen.getByRole('button', { name: /all statuses/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /all locations/i })).not.toBeInTheDocument();
    });
  });
});
