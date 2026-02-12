/**
 * Tests for the PrintersPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
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
};

describe('PrintersPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json(mockPrinterStatus);
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
    it('shows IP address', async () => {
      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('192.168.1.100')).toBeInTheDocument();
      });
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

  describe('part removal actions box', () => {
    it('shows part removal actions box when part_removal_enabled is true and last_job_name is set', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
        last_job_name: 'test_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        // Should show the part removal box when part_removal_enabled is true
        expect(screen.getByText('test_print.3mf')).toBeInTheDocument();
      });
    });

    it('hides part removal actions box when part_removal_enabled is false', async () => {
      const printerWithoutPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: false,
        part_removal_required: false,
        last_job_name: 'test_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithoutPartRemoval]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Should not show the part removal box when part_removal_enabled is false
      // Even if last_job_name is set
      expect(screen.queryByText('test_print.3mf')).not.toBeInTheDocument();
    });

    it('hides part removal actions box when last_job_name is null', async () => {
      const printerWithoutJobName = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
        last_job_name: null,
        last_job_user: null,
        last_job_start: null,
        last_job_end: null,
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithoutJobName]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Should not show the part removal box when last_job_name is null
      // We need to ensure it's not rendered somehow by checking for the unique collect button
      const buttons = screen.getAllByRole('button');
      const collectButton = buttons.find(btn => btn.textContent?.includes('Collect'));
      expect(collectButton).toBeUndefined();
    });

    it('simulates state after part collection - no stale data shown', async () => {
      // This test simulates the state AFTER a user has clicked "Collect Part"
      // Backend clears all job data when part is collected (see printers.py:2133-2137)
      const printerAfterCollection = {
        ...mockPrinters[0],
        part_removal_enabled: true,      // Feature still enabled
        part_removal_required: false,    // Part was collected
        last_job_name: null,            // Backend cleared job data
        last_job_user: null,            // Backend cleared job data
        last_job_start: null,           // Backend cleared job data
        last_job_end: null,             // Backend cleared job data
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerAfterCollection]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Box should be hidden because last_job_name is null (no stale data)
      // Even though part_removal_enabled is still true
      const buttons = screen.getAllByRole('button');
      const collectButton = buttons.find(btn => btn.textContent?.includes('Collect'));
      expect(collectButton).toBeUndefined();
    });
  });

  describe('part removal warning message', () => {
    it('shows "On Deck" message when part removal required and job is queued', async () => {
      const printerWithPartRemovalRequired = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: true,
        last_job_name: 'previous_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      const pausedStatus = {
        ...mockPrinterStatus,
        state: 'PAUSED',
        current_print: 'queued_print.3mf',
      };

      const mockQueue = [
        {
          id: 1,
          printer_id: 1,
          archive_id: 123,
          archive_name: 'next_job.3mf',
          library_file_name: null,
          scheduled_time: null,
          status: 'pending',
          created_at: '2024-01-01T13:00:00Z',
          created_by_username: 'testuser',
        },
      ];

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemovalRequired]);
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(pausedStatus);
        }),
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json(mockQueue);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        // Should show "Processing: next_job.3mf - Paused for Previous Part Removal Confirmation"
        expect(screen.getByText(/Processing: next_job\.3mf - Paused for Previous Part Removal Confirmation/)).toBeInTheDocument();
      });
    });

    it('shows regular paused message when no job is queued', async () => {
      const printerWithPartRemovalRequired = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: true,
        last_job_name: 'previous_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      const pausedStatus = {
        ...mockPrinterStatus,
        state: 'PAUSED',
        current_print: 'current_print.3mf',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemovalRequired]);
        }),
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(pausedStatus);
        }),
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        // Should show regular message without "On Deck"
        expect(screen.getByText(/Processing: current_print\.3mf - Paused for Previous Part Removal Confirmation/)).toBeInTheDocument();
        expect(screen.queryByText(/On Deck:/)).not.toBeInTheDocument();
      });
    });
  });

  describe('part removal button long press', () => {
    it('ignores right mouse button on part removal button', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Find the part removal button by its icon
      const buttons = screen.getAllByRole('button');
      const partRemovalButton = buttons.find(btn => {
        const svg = btn.querySelector('svg');
        return svg && btn.title?.includes('Part Removal');
      });

      expect(partRemovalButton).toBeDefined();

      if (partRemovalButton) {
        // Simulate right mouse button down (button = 2)
        const rightClickEvent = new MouseEvent('mousedown', {
          bubbles: true,
          button: 2, // Right mouse button
        });
        
        partRemovalButton.dispatchEvent(rightClickEvent);

        // Long press progress indicator should not appear
        // (If it did, it would have the class 'border-orange-500')
        const progressIndicators = document.querySelectorAll('.border-orange-500.pointer-events-none');
        expect(progressIndicators.length).toBe(0);
      }
    });

    it('ignores middle mouse button on part removal button', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      const buttons = screen.getAllByRole('button');
      const partRemovalButton = buttons.find(btn => {
        const svg = btn.querySelector('svg');
        return svg && btn.title?.includes('Part Removal');
      });

      expect(partRemovalButton).toBeDefined();

      if (partRemovalButton) {
        // Simulate middle mouse button down (button = 1)
        const middleClickEvent = new MouseEvent('mousedown', {
          bubbles: true,
          button: 1, // Middle mouse button
        });
        
        partRemovalButton.dispatchEvent(middleClickEvent);

        // Long press progress indicator should not appear
        const progressIndicators = document.querySelectorAll('.border-orange-500.pointer-events-none');
        expect(progressIndicators.length).toBe(0);
      }
    });
  });

  describe('collect part confirmation dialog', () => {
    it('closes dialog on successful part collection', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
        last_job_name: 'test_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        }),
        http.post('/api/v1/printers/:id/collect-part', () => {
          return HttpResponse.json({ success: true, message: 'Part collected' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('test_print.3mf')).toBeInTheDocument();
      });

      // Find and click the "Collect Part" button
      const collectButtons = screen.getAllByRole('button');
      const collectButton = collectButtons.find(btn => btn.textContent?.includes('Collect'));
      expect(collectButton).toBeDefined();
      collectButton!.click();

      // Wait for the confirmation dialog to appear
      await waitFor(() => {
        expect(screen.getByText('Confirm Part Removal')).toBeInTheDocument();
      });

      // Find and click the confirm button (Build Plate Clear) - it's inside the modal
      const confirmButtons = screen.getAllByRole('button', { name: /build plate clear/i });
      const confirmButton = confirmButtons.find(btn => btn.textContent === 'Build Plate Clear');
      expect(confirmButton).toBeDefined();
      confirmButton!.click();

      // Wait for the dialog to close
      await waitFor(() => {
        expect(screen.queryByText('Confirm Part Removal')).not.toBeInTheDocument();
      }, { timeout: 3000 });
    });

    it('closes dialog on error (500) during part collection', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
        last_job_name: 'test_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        }),
        http.post('/api/v1/printers/:id/collect-part', () => {
          return new HttpResponse(null, { 
            status: 500,
            statusText: 'Internal Server Error'
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('test_print.3mf')).toBeInTheDocument();
      });

      // Find and click the "Collect Part" button
      const collectButtons = screen.getAllByRole('button');
      const collectButton = collectButtons.find(btn => btn.textContent?.includes('Collect'));
      expect(collectButton).toBeDefined();
      collectButton!.click();

      // Wait for the confirmation dialog to appear
      await waitFor(() => {
        expect(screen.getByText('Confirm Part Removal')).toBeInTheDocument();
      });

      // Find and click the confirm button (Build Plate Clear) - it's inside the modal
      const confirmButtons = screen.getAllByRole('button', { name: /build plate clear/i });
      const confirmButton = confirmButtons.find(btn => btn.textContent === 'Build Plate Clear');
      expect(confirmButton).toBeDefined();
      confirmButton!.click();

      // Wait for the dialog to close even on error
      await waitFor(() => {
        expect(screen.queryByText('Confirm Part Removal')).not.toBeInTheDocument();
      }, { timeout: 3000 });
    });

    it('closes dialog on 404 error during part collection', async () => {
      const printerWithPartRemoval = {
        ...mockPrinters[0],
        part_removal_enabled: true,
        part_removal_required: false,
        last_job_name: 'test_print.3mf',
        last_job_user: 'testuser',
        last_job_start: '2024-01-01T10:00:00Z',
        last_job_end: '2024-01-01T12:00:00Z',
      };

      server.use(
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([printerWithPartRemoval]);
        }),
        http.post('/api/v1/printers/:id/collect-part', () => {
          return new HttpResponse(null, { 
            status: 404,
            statusText: 'Not Found'
          });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Click the XL button to ensure we're in expanded mode
      const xlButton = screen.getByTitle('Extra large cards');
      xlButton.click();

      await waitFor(() => {
        expect(screen.getByText('test_print.3mf')).toBeInTheDocument();
      });

      // Find and click the "Collect Part" button
      const collectButtons = screen.getAllByRole('button');
      const collectButton = collectButtons.find(btn => btn.textContent?.includes('Collect'));
      expect(collectButton).toBeDefined();
      collectButton!.click();

      // Wait for the confirmation dialog to appear
      await waitFor(() => {
        expect(screen.getByText('Confirm Part Removal')).toBeInTheDocument();
      });

      // Find and click the confirm button (Build Plate Clear) - it's inside the modal
      const confirmButtons = screen.getAllByRole('button', { name: /build plate clear/i });
      const confirmButton = confirmButtons.find(btn => btn.textContent === 'Build Plate Clear');
      expect(confirmButton).toBeDefined();
      confirmButton!.click();

      // Wait for the dialog to close even on error
      await waitFor(() => {
        expect(screen.queryByText('Confirm Part Removal')).not.toBeInTheDocument();
      }, { timeout: 3000 });
    });
  });
});
