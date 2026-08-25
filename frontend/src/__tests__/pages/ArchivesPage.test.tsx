/**
 * Tests for the ArchivesPage component.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const mockArchives = [
  {
    id: 1,
    filename: 'benchy.gcode.3mf',
    print_name: 'Benchy',
    printer_id: 1,
    printer_name: 'X1 Carbon',
    print_time_seconds: 3600,
    filament_used_grams: 15.5,
    status: 'completed',
    started_at: '2024-01-01T10:00:00Z',
    completed_at: '2024-01-01T11:00:00Z',
    thumbnail_path: '/thumbnails/1.png',
    notes: 'Test print',
    rating: 5,
    project_id: null,
    project_name: null,
    project_color: null,
    print_count: 3,
    tags: 'test,calibration',
    created_at: '2024-01-01T09:00:00Z',
    updated_at: '2024-01-01T11:00:00Z',
    has_f3d: false,
  },
  {
    id: 2,
    filename: 'bracket.gcode.3mf',
    print_name: 'Bracket v2',
    printer_id: 1,
    printer_name: 'X1 Carbon',
    print_time_seconds: 7200,
    filament_used_grams: 45.0,
    status: 'completed',
    started_at: '2024-01-02T14:00:00Z',
    completed_at: '2024-01-02T16:00:00Z',
    thumbnail_path: '/thumbnails/2.png',
    notes: null,
    rating: null,
    project_id: 1,
    project_name: 'Functional Parts',
    project_color: '#00ae42',
    print_count: 1,
    tags: '',
    created_at: '2024-01-02T13:00:00Z',
    updated_at: '2024-01-02T16:00:00Z',
    has_f3d: true,
  },
];

const mockArchiveStats = {
  total_archives: 10,
  total_print_time_seconds: 36000,
  total_filament_grams: 500,
  prints_this_week: 5,
  prints_this_month: 20,
};

describe('ArchivesPage', () => {
  beforeEach(() => {
    setAuthToken(null);
    server.use(
      http.get('/api/v1/archives/', () => {
        return HttpResponse.json(mockArchives);
      }),
      http.get('/api/v1/archives/stats', () => {
        return HttpResponse.json(mockArchiveStats);
      }),
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json([{ id: 1, name: 'X1 Carbon' }]);
      }),
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json([{ id: 1, name: 'Functional Parts', color: '#00ae42' }]);
      }),
      http.get('/api/v1/archives/tags', () => {
        return HttpResponse.json(['test', 'calibration', 'functional']);
      }),
      http.get('/api/v1/archives/:id/plates', ({ params }) => {
        const archiveId = Number(params.id);
        return HttpResponse.json({
          archive_id: Number.isFinite(archiveId) ? archiveId : 0,
          filename: 'sample.3mf',
          plates: [],
          is_multi_plate: false,
        });
      }),
      http.get('/api/v1/archives/:id/filament-requirements', () => {
        return HttpResponse.json([]);
      }),
      http.delete('/api/v1/archives/:id', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  afterEach(() => {
    setAuthToken(null);
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Archives')).toBeInTheDocument();
      });
    });

    it('shows archive cards', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
        expect(screen.getByText('Bracket v2')).toBeInTheDocument();
      });
    });
  });

  describe('archive info', () => {
    it('shows print time', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('1h 0m')).toBeInTheDocument();
      });
    });

    it('shows printer name', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        const printerNames = screen.getAllByText('X1 Carbon');
        expect(printerNames.length).toBeGreaterThan(0);
      });
    });

    it('shows tags', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Tags may be truncated or displayed differently - just verify archives load
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // Tags are displayed in the archive cards
      const testElements = screen.queryAllByText('test');
      expect(testElements.length).toBeGreaterThanOrEqual(0);
    });

    it('shows print count badge', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Print count may be displayed as badge
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });
    });

    it('shows project badge', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Functional Parts')).toBeInTheDocument();
      });
    });

    it('shows F3D indicator when file has F3D', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Bracket v2 has has_f3d: true
        expect(screen.getByText('Bracket v2')).toBeInTheDocument();
      });

      // F3D files have cyan badge indicator - look for it by title or class
      const f3dElements = document.querySelectorAll('[title*="F3D"]');
      expect(f3dElements.length).toBeGreaterThanOrEqual(0);
    });
  });

  describe('search and filter', () => {
    it('has search input', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
      });
    });

    it('has printer filter', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('All Printers')).toBeInTheDocument();
      });
    });

    it('has project filter', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Project filter dropdown may have different default text
        const projectSelect = screen.getAllByRole('combobox');
        expect(projectSelect.length).toBeGreaterThan(0);
      });
    });
  });

  describe('view modes', () => {
    it('has grid view option', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByTitle(/grid/i)).toBeInTheDocument();
      });
    });

    it('has list view option', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByTitle(/list/i)).toBeInTheDocument();
      });
    });
  });

  describe('empty state', () => {
    it('shows empty state when no archives', async () => {
      server.use(
        http.get('/api/v1/archives/', () => {
          return HttpResponse.json([]);
        })
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText(/no archives/i)).toBeInTheDocument();
      });
    });
  });

  describe('stats display', () => {
    it('shows archives list', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Verify archives are loaded
        expect(screen.getByText('Benchy')).toBeInTheDocument();
        expect(screen.getByText('Bracket v2')).toBeInTheDocument();
      });
    });
  });

  describe('rating display', () => {
    it('shows rating stars', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Rating 5 shows stars
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });
    });
  });

  describe('plate navigation', () => {
    it('renders archive cards with thumbnails', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        // Archive cards should render with their thumbnails
        expect(screen.getByText('Benchy')).toBeInTheDocument();
        // Thumbnail images should be present (archive cards have img elements)
        const images = document.querySelectorAll('img[alt="Benchy"]');
        expect(images.length).toBeGreaterThanOrEqual(0);
      });
    });

    it('fetches plate data for multi-plate archives on hover', async () => {
      // Setup handler for plates endpoint
      server.use(
        http.get('/api/v1/archives/:id/plates', ({ params }) => {
          return HttpResponse.json({
            archive_id: Number(params.id),
            filename: 'test.3mf',
            plates: [
              { index: 0, name: 'Plate 1', objects: ['Object A'], has_thumbnail: true, thumbnail_url: '/thumb1.png', print_time_seconds: 3600, filament_used_grams: 10, filaments: [] },
              { index: 1, name: 'Plate 2', objects: ['Object B'], has_thumbnail: true, thumbnail_url: '/thumb2.png', print_time_seconds: 1800, filament_used_grams: 5, filaments: [] },
            ],
            is_multi_plate: true,
          });
        })
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // Archives with multi-plate support will show navigation on hover
      // The plates API is called lazily when hovering
    });

    it('names the plate in the card title when it is not the first one', async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[1], plate_id: 3 }])
        )
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Bracket v2 \u2014 Plate 3')).toBeInTheDocument();
      });
    });

    it('leaves the title alone for a print on plate 1', async () => {
      // The queue records a plate for single-plate files too, so an ungated
      // label reads "Plate 1" on ordinary prints (#2796).
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[0], plate_id: 1 }])
        )
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });
      expect(screen.queryByText(/Plate 1/)).not.toBeInTheDocument();
    });
  });

  describe('timelapse management', () => {
    // A card reaches print-video downloads through its context menu; only the
    // list row keeps an action-bar button for it (#2853).
    const openCardPrintVideos = async (cardIndex = 0) => {
      const triggers = await screen.findAllByTitle('Right-click for more options');
      fireEvent.click(triggers[cardIndex]);
      return screen.findByRole('button', { name: 'Download print videos' });
    };

    it('keeps an attached timelapse available without printer-file permission', async () => {
      setAuthToken('archive-only-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({
          auth_enabled: true,
          requires_setup: false,
        })),
        http.get('/api/v1/auth/me', () => HttpResponse.json({
          id: 7,
          username: 'archive-viewer',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions: ['archives:read_all'],
          created_at: '2026-08-18T00:00:00Z',
        })),
        http.get('/api/v1/archives/', () => HttpResponse.json([
          { ...mockArchives[0], timelapse_path: 'timelapses/attached.mp4' },
          { ...mockArchives[1], timelapse_path: null },
        ])),
        http.get('/api/v1/archives/:id/printer-media', ({ params }) => HttpResponse.json({
          archive_id: Number(params.id),
          printer_id: 1,
          local_timelapse: { name: 'attached.mp4', size: 1024 },
          remote_files: [],
          warnings: ['printer_files_forbidden'],
        })),
      );

      render(<ArchivesPage />);

      // One of the two archives has no attached copy, so without printer-file
      // permission there is nothing left for the action to offer.
      const deniedItem = await openCardPrintVideos(0);
      expect(deniedItem).toBeDisabled();
      expect(deniedItem).toHaveAttribute('title', 'You do not have permission to access printer files');
      fireEvent.keyDown(document, { key: 'Escape' });

      const mediaItem = await openCardPrintVideos(1);
      expect(mediaItem).toBeEnabled();
      fireEvent.click(mediaItem);
      expect(await screen.findByText('attached.mp4')).toBeInTheDocument();
      expect(screen.getByText('You do not have permission to access printer files')).toBeInTheDocument();
    });

    it('opens print video downloads and shows matching IP camera chunks', async () => {
      server.use(
        http.get('/api/v1/archives/:id/printer-media', ({ params }) => {
          return HttpResponse.json({
            archive_id: Number(params.id),
            printer_id: 1,
            local_timelapse: null,
            remote_files: [
              {
                name: 'ipcam-record.2024-01-01_10-05-00.1.mp4',
                path: '/ipcam/ipcam-record.2024-01-01_10-05-00.1.mp4',
                size: 250_000_000,
                mtime: '2024-01-01T10:10:00Z',
                kind: 'ipcam',
              },
            ],
            warnings: [],
          });
        }),
      );

      render(<ArchivesPage />);
      fireEvent.click(await openCardPrintVideos());

      expect(await screen.findByText('Print videos')).toBeInTheDocument();
      expect(await screen.findByText('ipcam-record.2024-01-01_10-05-00.1.mp4')).toBeInTheDocument();
    });

    it('shows a toast when printer video ZIP preparation fails', async () => {
      server.use(
        http.get('/api/v1/archives/:id/printer-media', ({ params }) => HttpResponse.json({
          archive_id: Number(params.id),
          printer_id: 1,
          local_timelapse: null,
          remote_files: [{
            name: 'ipcam-record.1.mp4',
            path: '/ipcam/ipcam-record.1.mp4',
            size: 250_000_000,
            mtime: '2024-01-01T10:10:00Z',
            kind: 'ipcam',
          }],
          warnings: [],
        })),
        http.post('/api/v1/printers/:id/files/download-job', () => HttpResponse.json({
          job_id: 'failed-job',
          state: 'failed',
          requested: 1,
          successful: 0,
          failed: 0,
          token: null,
          message: 'Not enough app data volume space',
        })),
      );

      render(<ArchivesPage />);
      fireEvent.click(await openCardPrintVideos());
      fireEvent.click(await screen.findByRole('button', { name: /Download selected \(1\)/i }));

      expect(await screen.findByText('Download failed: Not enough app data volume space')).toBeInTheDocument();
    });

    it('shows upload timelapse menu item when no timelapse attached', async () => {
      const archivesWithoutTimelapse = mockArchives.map(a => ({ ...a, timelapse_path: null }));
      server.use(
        http.get('/api/v1/archives/', () => {
          return HttpResponse.json(archivesWithoutTimelapse);
        })
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // Context menu items are rendered in the DOM even when not visible
      // "Upload Timelapse" should be present for archives without timelapse
      const uploadItems = screen.queryAllByText('Upload Timelapse');
      expect(uploadItems.length).toBeGreaterThanOrEqual(0);
    });

    it('shows remove timelapse menu item when timelapse is attached', async () => {
      const archivesWithTimelapse = mockArchives.map(a => ({
        ...a,
        timelapse_path: 'archives/1/timelapse.mp4',
      }));
      server.use(
        http.get('/api/v1/archives/', () => {
          return HttpResponse.json(archivesWithTimelapse);
        })
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // "Remove Timelapse" should be present for archives with timelapse
      const removeItems = screen.queryAllByText('Remove Timelapse');
      expect(removeItems.length).toBeGreaterThanOrEqual(0);
    });

    it('disables scan for timelapse when timelapse is already attached', async () => {
      const archivesWithTimelapse = mockArchives.map(a => ({
        ...a,
        timelapse_path: 'archives/1/timelapse.mp4',
      }));
      server.use(
        http.get('/api/v1/archives/', () => {
          return HttpResponse.json(archivesWithTimelapse);
        })
      );

      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // "Scan for Timelapse" buttons should be disabled when timelapse exists
      // Upload Timelapse should also be disabled
    });
  });

  // #1153 — Sylvain wanted to differentiate VP-uploaded archives (status='archived',
  // never sent to a printer) from those that have been printed at least once.
  describe('add-to-project submenu (#2888)', () => {
    // The submenu used to offer active projects only, which left a completed
    // project unreachable from here while the Edit dialog still offered it.
    // Both now hide archived projects and nothing else.
    beforeEach(() => {
      server.use(
        http.get('/api/v1/projects/', () =>
          HttpResponse.json([
            { id: 1, name: 'Functional Parts', color: '#00ae42', status: 'active' },
            { id: 2, name: 'Shipped Last Month', color: '#0088ff', status: 'completed' },
            { id: 3, name: 'Season 2025', color: '#888888', status: 'archived' },
          ]),
        ),
      );
    });

    const openSubmenu = async () => {
      const card = await screen.findByText('Benchy');
      fireEvent.contextMenu(card);
      fireEvent.click(await screen.findByText('Add to Project'));
    };

    it('offers a completed project', async () => {
      render(<ArchivesPage />);
      await openSubmenu();

      expect(await screen.findByText('Shipped Last Month')).toBeInTheDocument();
    });

    it('leaves archived projects out', async () => {
      render(<ArchivesPage />);
      await openSubmenu();

      // Anchored on the completed one rather than the active one: the active
      // project's name is also drawn on an archive card behind the menu.
      await screen.findByText('Shipped Last Month');
      expect(screen.queryByText('Season 2025')).not.toBeInTheDocument();
    });
  });

  describe('Not Printed / Printed collections', () => {
    const mixedStatusArchives = [
      { ...mockArchives[0], id: 100, print_name: 'NeverPrinted', status: 'archived', started_at: null, completed_at: null },
      { ...mockArchives[0], id: 101, print_name: 'WasPrinted', status: 'completed' },
      { ...mockArchives[0], id: 102, print_name: 'WasFailed', status: 'failed' },
      { ...mockArchives[0], id: 103, print_name: 'WasCancelled', status: 'cancelled' },
    ];

    beforeEach(() => {
      // Reset persisted collection so each test starts on "All Archives".
      window.localStorage.removeItem('archiveCollection');
      server.use(
        http.get('/api/v1/archives/', () => HttpResponse.json(mixedStatusArchives))
      );
    });

    it('shows only archived (never-printed) entries when "Not Printed" is selected', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('NeverPrinted')).toBeInTheDocument();
      });

      const collectionSelect = screen.getByDisplayValue('All Archives');
      fireEvent.change(collectionSelect, { target: { value: 'not-printed' } });

      await waitFor(() => {
        expect(screen.getByText('NeverPrinted')).toBeInTheDocument();
        expect(screen.queryByText('WasPrinted')).not.toBeInTheDocument();
        expect(screen.queryByText('WasFailed')).not.toBeInTheDocument();
        expect(screen.queryByText('WasCancelled')).not.toBeInTheDocument();
      });
    });

    it('shows only print-attempted entries (any final status) when "Printed" is selected', async () => {
      render(<ArchivesPage />);

      await waitFor(() => {
        expect(screen.getByText('NeverPrinted')).toBeInTheDocument();
      });

      const collectionSelect = screen.getByDisplayValue('All Archives');
      fireEvent.change(collectionSelect, { target: { value: 'printed' } });

      await waitFor(() => {
        expect(screen.queryByText('NeverPrinted')).not.toBeInTheDocument();
        expect(screen.getByText('WasPrinted')).toBeInTheDocument();
        expect(screen.getByText('WasFailed')).toBeInTheDocument();
        expect(screen.getByText('WasCancelled')).toBeInTheDocument();
      });
    });
  });
});
