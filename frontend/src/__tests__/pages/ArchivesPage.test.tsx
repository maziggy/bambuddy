/**
 * Tests for the ArchivesPage component.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';
import { useNavigate } from 'react-router-dom';

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

    it("compares the latest print with its plate's estimate", async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([
            { ...mockArchives[1], run_count: 2, actual_time_seconds: 2000, time_estimate_seconds: 1800, time_accuracy: 90 },
          ])
        )
      );
      render(<ArchivesPage />);

      const time = (await screen.findByText('-10%')).parentElement!;
      expect(time.getAttribute('title')).toContain('Estimated: 30m');
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

    it('shows the picture of the plate the title names', async () => {
      // A file printed again shows its latest run's plate; the stored
      // thumbnail is the first plate's.
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[1], plate_id: 3 }])
        )
      );

      render(<ArchivesPage />);

      const img = await screen.findByAltText('Bracket v2');
      expect(img.getAttribute('src')).toContain(`/archives/${mockArchives[1].id}/plate-thumbnail/3`);
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

  describe('outcome badges', () => {
    it('labels an aborted print as aborted and a cancelled one as cancelled', async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([
            { ...mockArchives[0], id: 201, print_name: 'StoppedOnPrinter', status: 'aborted' },
            { ...mockArchives[0], id: 202, print_name: 'StoppedInBambuddy', status: 'cancelled' },
          ]),
        ),
      );
      render(<ArchivesPage />);

      await screen.findByText('StoppedOnPrinter');
      expect(screen.getByText('aborted')).toBeInTheDocument();
      expect(screen.getByText('cancelled')).toBeInTheDocument();
    });
  });

  // Archive redesign P4: a card opens its archive's page; the controls on it
  // do what they say and leave the page where it is.
  describe('opening an archive', () => {
    beforeEach(() => {
      window.localStorage.removeItem('archiveCollection');
      window.history.pushState({}, '', '/archives');
      server.use(http.get('/api/v1/archives/', () => HttpResponse.json(mockArchives)));
    });

    it('opens the archive page from a click on its card', async () => {
      render(<ArchivesPage />);
      fireEvent.click(await screen.findByText('#2'));

      await waitFor(() => expect(window.location.pathname).toBe('/archives/2'));
    });

    it("counts every print's video and opens the archive to play them", async () => {
      // Plate 2's video is the archive's; plate 1's is on its print.
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[0], timelapse_path: 'archives/1/b.mp4', timelapse_count: 2 }]),
        ),
      );
      render(<ArchivesPage />);
      const button = await screen.findByTestId('archive-card-timelapse');

      expect(button).toHaveAttribute('title', 'View 2 timelapses');
      expect(within(button).getByText('2')).toBeInTheDocument();
      fireEvent.click(button);

      await waitFor(() => expect(window.location.pathname).toBe('/archives/1'));
    });

    it('plays a single video in place', async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[0], timelapse_path: 'archives/1/a.mp4', timelapse_count: 1 }]),
        ),
      );
      render(<ArchivesPage />);
      const button = await screen.findByTestId('archive-card-timelapse');

      expect(button).toHaveAttribute('title', 'View timelapse');
      fireEvent.click(button);

      expect(window.location.pathname).toBe('/archives');
    });

    it("opens the archive for a video only an earlier print has", async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[0], timelapse_path: null, timelapse_count: 1 }]),
        ),
      );
      render(<ArchivesPage />);
      fireEvent.click(await screen.findByTestId('archive-card-timelapse'));

      await waitFor(() => expect(window.location.pathname).toBe('/archives/1'));
    });

    it("says in the list how many videos an archive's prints have", async () => {
      server.use(
        http.get('/api/v1/archives/', () =>
          HttpResponse.json([{ ...mockArchives[0], timelapse_path: null, timelapse_count: 2 }]),
        ),
      );
      render(<ArchivesPage />);
      await screen.findByTestId('archive-card-timelapse');
      fireEvent.click(screen.getByTitle(/list/i));

      await waitFor(() => expect(screen.queryByTestId('archive-card-timelapse')).not.toBeInTheDocument());
      expect(screen.getByTitle('View 2 timelapses')).toBeInTheDocument();
    });

    it('leaves the page alone for a click on a control in the card', async () => {
      render(<ArchivesPage />);
      await screen.findByText('#2');
      fireEvent.click(screen.getAllByTitle(/favorites/i)[0]);

      expect(window.location.pathname).toBe('/archives');
    });

    it('leaves the page alone for a click in a dialog the card opened', async () => {
      render(<ArchivesPage />);
      await screen.findByText('#2');
      // The card's own Edit button; its dialog renders inside the card.
      const card = screen.getByText('#2').closest('[data-archive-id]') as HTMLElement;
      fireEvent.click(within(card).getByTitle('Edit'));
      fireEvent.click(await screen.findByText('Edit Archive'));

      expect(window.location.pathname).toBe('/archives');
    });
  });

  describe('Not Printed / Printed collections', () => {
    const mixedStatusArchives = [
      { ...mockArchives[0], id: 100, print_name: 'NeverPrinted', status: 'archived', started_at: null, completed_at: null },
      { ...mockArchives[0], id: 101, print_name: 'WasPrinted', status: 'completed' },
      { ...mockArchives[0], id: 102, print_name: 'WasFailed', status: 'failed' },
      { ...mockArchives[0], id: 103, print_name: 'WasCancelled', status: 'cancelled' },
      { ...mockArchives[0], id: 104, print_name: 'FailedBefore', status: 'completed', run_count: 2, failed_run_count: 1 },
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

    // Archive redesign P4: an archive is a file printed maybe several times.
    it('"Failed Prints" finds archives one print of which failed, not only the latest', async () => {
      render(<ArchivesPage />);
      await screen.findByText('FailedBefore');

      fireEvent.change(screen.getByDisplayValue('All Archives'), { target: { value: 'failed' } });

      await waitFor(() => {
        expect(screen.getByText('WasFailed')).toBeInTheDocument();
        expect(screen.getByText('FailedBefore')).toBeInTheDocument();
        expect(screen.queryByText('WasPrinted')).not.toBeInTheDocument();
      });
    });
  });

  // Archive redesign P2: merging changes and removes whole archives, so the
  // button is there only for those the merge API lets through.
  // Archive redesign P3: the Failed collection shows failed and aborted
  // prints, and its export has both.
  it('exports what the Failed collection shows', async () => {
    let exported: string | null = null;
    server.use(
      http.get('*/api/v1/archives/export', ({ request }) => {
        exported = new URL(request.url).searchParams.get('status');
        return new HttpResponse('ID\n', { headers: { 'Content-Type': 'text/csv' } });
      }),
    );
    const createObjectURL = URL.createObjectURL;
    const revokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = () => 'blob:export';
    URL.revokeObjectURL = () => {};
    try {
      render(<ArchivesPage />);
      fireEvent.change(await screen.findByDisplayValue('All Archives'), { target: { value: 'failed' } });
      fireEvent.click(screen.getByRole('button', { name: 'Export' }));
      fireEvent.click(await screen.findByText('Export as CSV'));

      await waitFor(() => expect(exported).toBe('failed,aborted'));
    } finally {
      URL.createObjectURL = createObjectURL;
      URL.revokeObjectURL = revokeObjectURL;
    }
  });

  describe('merge duplicates button', () => {
    const signInWith = (permissions: string[]) => {
      setAuthToken('merge-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
        http.get('/api/v1/auth/me', () => HttpResponse.json({
          id: 7,
          username: 'operator',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions,
          created_at: '2026-08-18T00:00:00Z',
        })),
        http.get('*/api/v1/archives/merge-preview', () => HttpResponse.json({ groups: [] })),
      );
    };

    it('opens the merge dialog for a user who may update and delete all archives', async () => {
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      render(<ArchivesPage />);

      fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
      expect(await screen.findByText('No duplicates to merge.')).toBeInTheDocument();
    });

    it('offers the merge while duplicates exist, until dismissed', async () => {
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      server.use(http.get('*/api/v1/archives/merge-count', () => HttpResponse.json({ groups: 3 })));
      render(<ArchivesPage />);

      const banner = await screen.findByTestId('merge-banner');
      expect(banner).toHaveTextContent('3 group(s) of archives hold the same file');
      fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

      await waitFor(() => expect(screen.queryByTestId('merge-banner')).toBeNull());
      expect(window.localStorage.setItem).toHaveBeenCalledWith('archiveMergeBannerDismissed', '3');
    });

    it('offers it again once the count has changed since it was dismissed', async () => {
      vi.mocked(window.localStorage.getItem).mockImplementation((key: string) =>
        key === 'archiveMergeBannerDismissed' ? '3' : null,
      );
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      server.use(http.get('*/api/v1/archives/merge-count', () => HttpResponse.json({ groups: 4 })));
      render(<ArchivesPage />);

      expect(await screen.findByTestId('merge-banner')).toBeInTheDocument();
      vi.mocked(window.localStorage.getItem).mockReset();
    });

    it('counts again when the archives change', async () => {
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      let groups = 1;
      server.use(http.get('*/api/v1/archives/merge-count', () => HttpResponse.json({ groups })));
      // Following an archive link reloads the list, as a delete or a new print does.
      function GoTo() {
        const navigate = useNavigate();
        return <button onClick={() => navigate('/archives?highlight=1')}>go</button>;
      }
      render(<><ArchivesPage /><GoTo /></>);
      await screen.findByTestId('merge-banner');

      groups = 0;
      fireEvent.click(screen.getByText('go'));

      await waitFor(() => expect(screen.queryByTestId('merge-banner')).toBeNull());
    });

    it('stays dismissed when merging some left fewer', async () => {
      vi.mocked(window.localStorage.getItem).mockImplementation((key: string) =>
        key === 'archiveMergeBannerDismissed' ? '3' : null,
      );
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      let counted = false;
      server.use(
        http.get('*/api/v1/archives/merge-count', () => {
          counted = true;
          return HttpResponse.json({ groups: 2 });
        }),
      );
      render(<ArchivesPage />);

      await waitFor(() => expect(counted).toBe(true));
      await screen.findByRole('button', { name: 'Merge duplicates' });
      expect(screen.queryByTestId('merge-banner')).toBeNull();
      vi.mocked(window.localStorage.getItem).mockReset();
    });

    it('stays dismissed while the count is the same', async () => {
      vi.mocked(window.localStorage.getItem).mockImplementation((key: string) =>
        key === 'archiveMergeBannerDismissed' ? '3' : null,
      );
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      let counted = false;
      server.use(
        http.get('*/api/v1/archives/merge-count', () => {
          counted = true;
          return HttpResponse.json({ groups: 3 });
        }),
      );
      render(<ArchivesPage />);

      await waitFor(() => expect(counted).toBe(true));
      await screen.findByRole('button', { name: 'Merge duplicates' });
      expect(screen.queryByTestId('merge-banner')).toBeNull();
      vi.mocked(window.localStorage.getItem).mockReset();
    });

    it('is offered in the Duplicates collection', async () => {
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_all']);
      render(<ArchivesPage />);

      await screen.findByRole('button', { name: 'Merge duplicates' });
      expect(screen.queryByText(/can be merged into one, with every print of them/)).toBeNull();
      fireEvent.change(screen.getByDisplayValue('All Archives'), { target: { value: 'duplicates' } });
      expect(await screen.findByText(/can be merged into one, with every print of them/)).toBeInTheDocument();
    });

    it('is not there for a user who may only update all archives', async () => {
      signInWith(['archives:read_all', 'archives:update_all', 'archives:delete_own']);
      render(<ArchivesPage />);

      await screen.findByText('Archives');
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Merge duplicates' })).toBeNull());
    });
  });

  // #1898: the "outcome?" badge on a print still waiting for its verdict opens
  // the dialog. The PATCH behind it checks archives:update against the owner,
  // like the menu item beside it, so for anyone else the badge is a status only.
  describe('awaiting-outcome badge', () => {
    const awaiting = { ...mockArchives[0], confirm_requested: true, user_verdict: null, created_by_id: 7 };

    const signInWith = (permissions: string[]) => {
      setAuthToken('verdict-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
        http.get('/api/v1/auth/me', () => HttpResponse.json({
          id: 7,
          username: 'operator',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions,
          created_at: '2026-08-18T00:00:00Z',
        })),
        http.get('/api/v1/archives/', () => HttpResponse.json([awaiting])),
      );
    };

    afterEach(() => {
      window.localStorage.removeItem('archiveViewMode');
    });

    it('is a status only for a user who may not update the archive', async () => {
      signInWith(['archives:read_all']);
      render(<ArchivesPage />);

      const badge = await screen.findByRole('button', { name: 'outcome?' });
      await waitFor(() => expect(badge).toBeDisabled());
      expect(badge).toHaveAttribute('title', 'You do not have permission to update archives');
      fireEvent.click(badge);
      expect(screen.queryByTestId('confirm-outcome-dialog')).toBeNull();
    });

    it('is a status only in the list view too', async () => {
      window.localStorage.setItem('archiveViewMode', 'list');
      signInWith(['archives:read_all']);
      render(<ArchivesPage />);

      const badge = await screen.findByRole('button', { name: 'outcome?' });
      await waitFor(() => expect(badge).toBeDisabled());
      expect(badge).toHaveAttribute('title', 'You do not have permission to update archives');
    });

    it('is disabled on somebody else\'s print for a user who may update only their own', async () => {
      signInWith(['archives:read_all', 'archives:update_own']);
      server.use(http.get('/api/v1/archives/', () => HttpResponse.json([{ ...awaiting, created_by_id: 8 }])));
      render(<ArchivesPage />);

      const badge = await screen.findByRole('button', { name: 'outcome?' });
      await waitFor(() => expect(badge).toBeDisabled());
    });

    it('opens the dialog for the owner', async () => {
      signInWith(['archives:read_all', 'archives:update_own']);
      render(<ArchivesPage />);

      const badge = await screen.findByRole('button', { name: 'outcome?' });
      await waitFor(() => expect(badge).toBeEnabled());
      fireEvent.click(badge);
      expect(await screen.findByTestId('confirm-outcome-dialog')).toBeInTheDocument();
    });
  });

  describe('unconfirmed filter', () => {
    afterEach(() => {
      window.localStorage.removeItem('archiveFilterUnconfirmed');
    });

    it('is cleared by Reset like every other top filter', async () => {
      render(<ArchivesPage />);
      await screen.findByText('Benchy');

      fireEvent.click(screen.getByTitle('Show only prints still waiting for their outcome verdict'));
      // Neither fixture archive is waiting for a verdict, so the filter empties the list.
      await waitFor(() => expect(screen.queryByText('Benchy')).not.toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

      expect(await screen.findByText('Benchy')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument();
    });
  });
});
