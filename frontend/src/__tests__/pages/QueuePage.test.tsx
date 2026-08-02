/**
 * Tests for the QueuePage component.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { QueuePage } from '../../pages/QueuePage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock queue data
const mockQueueItems = [
  {
    id: 1,
    printer_id: 1,
    archive_id: 1,
    position: 1,
    status: 'pending',
    scheduled_time: null,
    require_previous_success: false,
    auto_off_after: false,
    manual_start: false,
    ams_mapping: null,
    plate_id: null,
    bed_levelling: 'on',
    flow_cali: 'off',
    vibration_cali: true,
    layer_inspect: false,
    timelapse: false,
    use_ams: true,
    started_at: null,
    completed_at: null,
    error_message: null,
    created_at: '2024-01-01T00:00:00Z',
    archive_name: 'Test Print 1',
    archive_thumbnail: '/thumb1.png',
    printer_name: 'Test Printer',
    print_time_seconds: 3600,
  },
  {
    id: 2,
    printer_id: 1,
    archive_id: 2,
    position: 2,
    status: 'printing',
    scheduled_time: null,
    require_previous_success: false,
    auto_off_after: true,
    manual_start: false,
    ams_mapping: null,
    plate_id: null,
    bed_levelling: 'on',
    flow_cali: 'off',
    vibration_cali: true,
    layer_inspect: false,
    timelapse: false,
    use_ams: true,
    started_at: '2024-01-01T10:00:00Z',
    completed_at: null,
    error_message: null,
    created_at: '2024-01-01T00:00:00Z',
    archive_name: 'Active Print',
    archive_thumbnail: '/thumb2.png',
    printer_name: 'Test Printer',
    print_time_seconds: 7200,
  },
  {
    id: 3,
    printer_id: 1,
    archive_id: 3,
    position: 3,
    status: 'completed',
    scheduled_time: null,
    require_previous_success: false,
    auto_off_after: false,
    manual_start: false,
    ams_mapping: null,
    plate_id: null,
    bed_levelling: 'on',
    flow_cali: 'off',
    vibration_cali: true,
    layer_inspect: false,
    timelapse: false,
    use_ams: true,
    started_at: '2024-01-01T08:00:00Z',
    completed_at: '2024-01-01T09:00:00Z',
    error_message: null,
    created_at: '2024-01-01T00:00:00Z',
    archive_name: 'Completed Print',
    archive_thumbnail: '/thumb3.png',
    printer_name: 'Test Printer',
    print_time_seconds: 1800,
  },
];

const mockPrinters = [
  {
    id: 1,
    name: 'Test Printer',
    ip_address: '192.168.1.100',
    serial_number: 'TESTSERIAL0001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    created_at: '2024-01-01T00:00:00Z',
  },
];

describe('QueuePage', () => {
  beforeEach(() => {
    // Mock localStorage.getItem to return expected defaults for queue page
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => {
      if (key === 'queue.historyCollapsed') return 'false'; // expanded
      if (key === 'queue.viewMode') return 'list';
      return null;
    });

    // Setup MSW handlers for this test
    server.use(
      http.get('/api/v1/queue/', () => {
        return HttpResponse.json(mockQueueItems);
      }),
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.delete('/api/v1/queue/:id', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/queue/:id/cancel', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/queue/:id/start', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/queue/:id/stop', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/queue/reorder', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Print Queue')).toBeInTheDocument();
      });
    });

    it('renders the page description', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Schedule and manage your print jobs')).toBeInTheDocument();
      });
    });

    it('shows summary cards', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        // Check for the page title (Print Queue is the h1)
        expect(screen.getByText('Print Queue')).toBeInTheDocument();
      });
    });

    it('shows filter dropdowns', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('All Printers')).toBeInTheDocument();
        expect(screen.getByText('All Status')).toBeInTheDocument();
      });
    });
  });

  describe('queue items display', () => {
    it('shows pending queue items', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Test Print 1')).toBeInTheDocument();
      });
    });

    it('shows active printing items', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Active Print')).toBeInTheDocument();
        expect(screen.getByText('Currently Printing')).toBeInTheDocument();
      });
    });

    it('shows one if-started-now ETA for an eligible pending item', async () => {
      // Printer 1 is free: nothing is printing on it and nothing is queued ahead.
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([mockQueueItems[0]]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Test Print 1');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      const etaEl = within(row as HTMLElement).getAllByTestId('queue-item-eta');
      expect(etaEl).toHaveLength(1);
      // The tooltip must say what the number actually means, not reuse the
      // printers-page "Estimated completion time" wording (which this is not).
      expect(etaEl[0]).toHaveAttribute(
        'title',
        'Completion time if this job started now',
      );
    });

    // The scheduler only writes waiting_reason on the model-based assignment
    // path, so an item pinned to a specific printer carries no marker at all
    // while it sits behind a running job. Without the printer-busy check every
    // one of these quoted the same wrong "starts now" time.
    it('does not show an ETA for an item pinned behind a running print', async () => {
      render(<QueuePage />);

      // mockQueueItems[1] ("Active Print") is printing on printer 1, and
      // "Test Print 1" is pending on the same printer with waiting_reason null.
      const name = await screen.findByText('Test Print 1');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
    });

    it('shows the ETA only on the next item up when several share an idle printer', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            { ...mockQueueItems[0], id: 10, position: 1, archive_name: 'First up' },
            { ...mockQueueItems[0], id: 11, position: 2, archive_name: 'Second up' },
            { ...mockQueueItems[0], id: 12, position: 3, archive_name: 'Third up' },
          ]);
        }),
      );

      render(<QueuePage />);

      await screen.findByText('Third up');

      const etaRowNames = screen
        .queryAllByTestId('queue-item-eta')
        .map((el) => el.closest('.group')?.textContent);

      expect(etaRowNames).toHaveLength(1);
      expect(etaRowNames[0]).toContain('First up');
    });

    it('shows an ETA for a staged item queued behind others on an idle printer', async () => {
      // The scheduler skips manual-start items without claiming the printer, so
      // a staged job is startable whenever its printer is free — queue order
      // does not gate it.
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            { ...mockQueueItems[0], id: 20, position: 1, archive_name: 'Auto first' },
            {
              ...mockQueueItems[0],
              id: 21,
              position: 2,
              archive_name: 'Staged second',
              manual_start: true,
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Staged second');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).getAllByTestId('queue-item-eta'),
      ).toHaveLength(1);
    });

    it('does not show an ETA for an item conditional on a previous print', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              archive_name: 'Conditional Print',
              require_previous_success: true,
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Conditional Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
    });

    it('advances the ETA as time passes', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.setSystemTime(new Date('2026-08-02T10:00:00Z'));

      try {
        server.use(
          http.get('/api/v1/queue/', () => {
            return HttpResponse.json([mockQueueItems[0]]);
          }),
        );

        render(<QueuePage />);

        const name = await screen.findByText('Test Print 1');
        const row = name.closest('.group') as HTMLElement;
        const before = within(row).getByTestId('queue-item-eta').textContent;

        // The queue payload never changes, so react-query hands back the same
        // object and nothing here re-renders on its own. Only the page's own
        // clock can move this value.
        await vi.advanceTimersByTimeAsync(45 * 60 * 1000);

        await waitFor(() => {
          expect(
            within(row).getByTestId('queue-item-eta').textContent,
          ).not.toBe(before);
        });
      } finally {
        vi.useRealTimers();
      }
    });

    it('shows one if-started-now ETA for a staged item', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              archive_name: 'Staged Print',
              manual_start: true,
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Staged Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).getAllByTestId('queue-item-eta'),
      ).toHaveLength(1);
    });

    it('shows exactly one live ETA for a printing item', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', ({ params }) => {
          return HttpResponse.json({
            id: Number(params.id),
            name: 'Test Printer',
            connected: true,
            state: 'RUNNING',
            progress: 50,
            remaining_time: 60,
            layer_num: 50,
            total_layers: 100,
            filename: 'active.3mf',
          });
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Active Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();

      await waitFor(() => {
        expect(
          within(row as HTMLElement).getAllByText(/^ETA\s/),
        ).toHaveLength(1);
      });

      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
    });

    it('does not show an ETA for a waiting item', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              archive_name: 'Waiting Print',
              waiting_reason: 'Waiting for matching printer',
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Waiting Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
    });

    it('does not show an if-started-now ETA for a scheduled item', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              archive_name: 'Scheduled Print',
              scheduled_time: new Date(
                Date.now() + 5 * 60 * 60 * 1000,
              ).toISOString(),
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Scheduled Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
    });

    it('does not render a dangling ETA for an invalid duration', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              archive_name: 'Invalid Duration Print',
              print_time_seconds: -60,
            },
          ]);
        }),
      );

      render(<QueuePage />);

      const name = await screen.findByText('Invalid Duration Print');
      const row = name.closest('.group');

      expect(row).not.toBeNull();
      expect(
        within(row as HTMLElement).queryByTestId('queue-item-eta'),
      ).not.toBeInTheDocument();
      expect(
        within(row as HTMLElement).queryByText(/^ETA(?:\s|$)/),
      ).not.toBeInTheDocument();
    });

    it('shows completed items in history', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      // The History tab now owns the completed/cancelled/failed list.
      await user.click(await screen.findByRole('button', { name: /^History/ }));

      await waitFor(() => {
        expect(screen.getByText('Completed Print')).toBeInTheDocument();
      });
    });

    it('shows status badges', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        // Queue items should be visible with status indicators
        expect(screen.getByText('Test Print 1')).toBeInTheDocument();
      });
    });

    it('shows printer names', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        const printerElements = screen.getAllByText('Test Printer');
        expect(printerElements.length).toBeGreaterThan(0);
      });
    });

    it('renders queue items with plate_id correctly', async () => {
      // Override with queue items that have plate_id set
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              plate_id: 2,
              archive_name: 'Multi-plate Print',
            },
          ]);
        })
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Multi-plate Print')).toBeInTheDocument();
      });
    });
  });

  describe('history pagination', () => {
    // #2682: History rendered the full count in the header but only ever drew
    // the first 50 rows, with no way to reach the rest. It now paginates with
    // a "Show more" control.
    const manyHistory = Array.from({ length: 60 }, (_, i) => ({
      ...mockQueueItems[2],
      id: 100 + i,
      batch_id: null,
      archive_name: `History Item ${String(i).padStart(2, '0')}`,
      // Descending completed_at so index 0 is newest and sorts first; the
      // default History sort is by date, newest first.
      completed_at: new Date(Date.UTC(2024, 0, 1, 0, 0, 0) - i * 60000).toISOString(),
    }));

    beforeEach(() => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json(manyHistory);
        })
      );
    });

    it('caps the History list at one page and reveals the rest on Show more', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      await user.click(await screen.findByRole('button', { name: /^History/ }));

      // First page is drawn; an item past the 50-row cap is not.
      await waitFor(() => {
        expect(screen.getByText('History Item 00')).toBeInTheDocument();
      });
      expect(screen.queryByText('History Item 59')).not.toBeInTheDocument();
      expect(screen.getByText('Showing 50 of 60')).toBeInTheDocument();

      // Show more reveals the remainder and then disappears (nothing left).
      await user.click(screen.getByRole('button', { name: /show more/i }));

      await waitFor(() => {
        expect(screen.getByText('History Item 59')).toBeInTheDocument();
      });
      expect(screen.queryByRole('button', { name: /show more/i })).not.toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('shows empty state when no queue items', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([]);
        })
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('No prints scheduled')).toBeInTheDocument();
      });
    });
  });

  describe('filtering', () => {
    it('has printer filter options', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('All Printers')).toBeInTheDocument();
      });

      const printerSelect = screen.getByDisplayValue('All Printers');
      await user.click(printerSelect);

      expect(screen.getByText('Unassigned')).toBeInTheDocument();
    });

    it('has status filter options', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('All Status')).toBeInTheDocument();
      });

      const statusSelect = screen.getByDisplayValue('All Status');
      await user.click(statusSelect);

      expect(screen.getByRole('option', { name: 'Pending' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'Printing' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'Completed' })).toBeInTheDocument();
    });
  });

  describe('queue actions', () => {
    it('shows edit button for pending items', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Test Print 1')).toBeInTheDocument();
      });

      // Find the edit button (Pencil icon)
      const editButtons = screen.getAllByTitle('Edit');
      expect(editButtons.length).toBeGreaterThan(0);
    });

    it('shows cancel button for pending items', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Test Print 1')).toBeInTheDocument();
      });

      const cancelButtons = screen.getAllByTitle('Cancel');
      expect(cancelButtons.length).toBeGreaterThan(0);
    });

    it('shows stop button for printing items', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Active Print')).toBeInTheDocument();
      });

      const stopButtons = screen.getAllByTitle('Stop Print');
      expect(stopButtons.length).toBeGreaterThan(0);
    });

    it('shows re-queue button for history items', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      await user.click(await screen.findByRole('button', { name: /^History/ }));

      await waitFor(() => {
        expect(screen.getByText('Completed Print')).toBeInTheDocument();
      });

      const requeueButtons = screen.getAllByTitle('Re-queue');
      expect(requeueButtons.length).toBeGreaterThan(0);
    });
  });

  describe('clear history', () => {
    it('shows clear history button when history exists', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      // Clear History only renders inside the History tab now.
      await user.click(await screen.findByRole('button', { name: /^History/ }));

      await waitFor(() => {
        expect(screen.getByText('Clear History')).toBeInTheDocument();
      });
    });

    it('opens confirm modal when clicking clear history', async () => {
      const user = userEvent.setup();
      render(<QueuePage />);

      await user.click(await screen.findByRole('button', { name: /^History/ }));

      await waitFor(() => {
        expect(screen.getByText('Clear History')).toBeInTheDocument();
      });

      const clearButton = screen.getByRole('button', { name: /clear history/i });
      await user.click(clearButton);

      await waitFor(() => {
        expect(screen.getByText(/Are you sure you want to remove all/i)).toBeInTheDocument();
      });
    });
  });

  describe('staged items', () => {
    it('shows staged badge for manual_start items', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              manual_start: true,
            },
          ]);
        })
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Staged')).toBeInTheDocument();
      });
    });

    it('shows start button for staged items', async () => {
      server.use(
        http.get('/api/v1/queue/', () => {
          return HttpResponse.json([
            {
              ...mockQueueItems[0],
              manual_start: true,
            },
          ]);
        })
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByTitle('Start Print')).toBeInTheDocument();
      });
    });
  });

  describe('auto power off badge', () => {
    it('shows power off badge when auto_off_after is true', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Auto power off')).toBeInTheDocument();
      });
    });
  });

  describe('gcode injection badge', () => {
    it('shows G-code badge when gcode_injection is true', async () => {
      const itemsWithGcode = mockQueueItems.map((item, i) =>
        i === 0 ? { ...item, gcode_injection: true } : item
      );
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(itemsWithGcode)),
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('G-code')).toBeInTheDocument();
      });
    });

    it('does not show G-code badge when gcode_injection is false', async () => {
      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText('Test Print 1')).toBeInTheDocument();
      });

      expect(screen.queryByText('G-code')).not.toBeInTheDocument();
    });
  });

  describe('filament-short ▶ flow (#1496)', () => {
    /**
     * The dispatch pre-flight flags a queue item as filament_short. The user
     * clicks ▶, the backend re-checks live and either dispatches (no deficit
     * anymore — clear flag) or returns 409 with the per-slot deficit so the
     * frontend can render the "Print Anyway" confirm modal.
     */
    const shortItem = {
      ...mockQueueItems[0],
      manual_start: true,
      filament_short: true,
    };

    it('renders the filament-short badge on a flagged pending row', async () => {
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json([shortItem])),
      );

      render(<QueuePage />);

      await waitFor(() => {
        expect(screen.getByText(/Insufficient filament for the assigned spool/i)).toBeInTheDocument();
      });
    });

    it('opens the Print Anyway modal when ▶ returns 409 and retries with skip_filament_check', async () => {
      let secondCallSkippedCheck: boolean | null = null;
      let attempts = 0;
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json([shortItem])),
        http.post('/api/v1/queue/:id/start', ({ request }) => {
          attempts += 1;
          const url = new URL(request.url);
          const skip = url.searchParams.get('skip_filament_check') === 'true';
          if (attempts === 1) {
            return HttpResponse.json(
              {
                detail: {
                  code: 'insufficient_filament',
                  deficit: [
                    {
                      slot_id: 1,
                      ams_id: 0,
                      tray_id: 0,
                      filament_type: 'PLA',
                      required_grams: 270,
                      remaining_grams: 200,
                    },
                  ],
                },
              },
              { status: 409 },
            );
          }
          secondCallSkippedCheck = skip;
          return HttpResponse.json({ ...shortItem, manual_start: false, filament_short: false });
        }),
      );

      render(<QueuePage />);

      const playButton = await screen.findByTitle(/Start Print|do not have permission to start prints/i);
      await userEvent.click(playButton);

      // Wait for the start endpoint to be hit (the 409 path returns to onError).
      await waitFor(() => expect(attempts).toBe(1));
      // Modal shows the deficit detail
      await screen.findByRole('button', { name: /Print Anyway/i });
      expect(
        screen.getByText(/Slot 1: needs 270 g, 200 g remaining/i),
      ).toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: /Print Anyway/i }));

      await waitFor(() => expect(secondCallSkippedCheck).toBe(true));
      expect(attempts).toBe(2);
    });
  });

  // #2667: mobile can't drag-reorder the queue, so pending rows get up/down
  // arrows. They persist via the same POST /queue/reorder as drag. The
  // buttons live in the DOM at every width (Tailwind `sm:hidden` is CSS-only),
  // so they're clickable in jsdom.
  describe('mobile reorder arrows (#2667)', () => {
    const threePending = [1, 2, 3].map((n) => ({
      ...mockQueueItems[0],
      id: n,
      archive_id: n,
      position: n,
      status: 'pending',
      archive_name: `Pending ${n}`,
    }));

    it('renders Move Up / Move Down controls for pending items', async () => {
      server.use(http.get('/api/v1/queue/', () => HttpResponse.json(threePending)));
      render(<QueuePage />);

      await waitFor(() => expect(screen.getByText('Pending 1')).toBeInTheDocument());

      // One pair per pending row.
      expect(screen.getAllByTitle('Move Up')).toHaveLength(3);
      expect(screen.getAllByTitle('Move Down')).toHaveLength(3);
    });

    it('moving the first item down persists the swapped order', async () => {
      let reorderBody: { items: { id: number; position: number }[] } | null = null;
      server.use(
        http.get('/api/v1/queue/', () => HttpResponse.json(threePending)),
        http.post('/api/v1/queue/reorder', async ({ request }) => {
          reorderBody = (await request.json()) as typeof reorderBody;
          return HttpResponse.json({ message: 'ok' });
        }),
      );
      render(<QueuePage />);

      await waitFor(() => expect(screen.getByText('Pending 1')).toBeInTheDocument());

      // First row's "Move Down": item 1 drops below item 2 → [2, 1, 3].
      await userEvent.click(screen.getAllByTitle('Move Down')[0]);

      await waitFor(() => expect(reorderBody).not.toBeNull());
      expect(reorderBody!.items).toEqual([
        { id: 2, position: 1 },
        { id: 1, position: 2 },
        { id: 3, position: 3 },
      ]);
    });

    it('disables Move Up on the first row and Move Down on the last', async () => {
      server.use(http.get('/api/v1/queue/', () => HttpResponse.json(threePending)));
      render(<QueuePage />);

      await waitFor(() => expect(screen.getByText('Pending 1')).toBeInTheDocument());

      // Rows render top-to-bottom in position order.
      expect(screen.getAllByTitle('Move Up')[0]).toBeDisabled();
      expect(screen.getAllByTitle('Move Down')[2]).toBeDisabled();
    });
  });
});
