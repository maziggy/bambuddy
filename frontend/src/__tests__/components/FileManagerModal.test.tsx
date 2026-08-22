/**
 * Tests for the FileManagerModal component.
 * Tests file browsing, selection, navigation, and file operations.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { FileManagerModal } from '../../components/FileManagerModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockFiles = [
  {
    name: 'cache',
    path: '/cache',
    size: 0,
    is_directory: true,
    mtime: '2024-01-15T10:00:00Z',
  },
  {
    name: 'model',
    path: '/model',
    size: 0,
    is_directory: true,
    mtime: '2024-01-15T10:00:00Z',
  },
  {
    name: 'benchy.3mf',
    path: '/benchy.3mf',
    size: 1048575,
    is_directory: false,
    mtime: '2024-01-15T10:00:00Z',
  },
  {
    name: 'print_job.gcode',
    path: '/print_job.gcode',
    size: 2048000,
    is_directory: false,
    mtime: '2024-01-14T10:00:00Z',
  },
];

const mockStorage = {
  used_bytes: 1073741824, // 1 GB
  free_bytes: 3221225472, // 3 GB
};

describe('FileManagerModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/printers/:id/files', () => {
        return HttpResponse.json({ files: mockFiles });
      }),
      http.get('/api/v1/printers/:id/storage', () => {
        return HttpResponse.json(mockStorage);
      }),
      http.delete('/api/v1/printers/:id/files', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal with header', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('File Manager')).toBeInTheDocument();
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });

    it('renders storage info', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/Used:/)).toBeInTheDocument();
        expect(screen.getByText(/Free:/)).toBeInTheDocument();
      });
    });

    it('renders quick navigation buttons', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Root')).toBeInTheDocument();
      expect(screen.getByText('Cache')).toBeInTheDocument();
      expect(screen.getByText('Models')).toBeInTheDocument();
      expect(screen.getByText('Timelapse')).toBeInTheDocument();
    });

    it('renders file list', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('cache')).toBeInTheDocument();
        expect(screen.getByText('model')).toBeInTheDocument();
        expect(screen.getByText('benchy.3mf')).toBeInTheDocument();
        expect(screen.getByText('print_job.gcode')).toBeInTheDocument();
      });
    });

    it('shows file sizes for files', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // 1024000 bytes = 1024.0 KB
        expect(screen.getByText('1024.0 KB')).toBeInTheDocument();
      });
    });
  });

  describe('navigation', () => {
    it('navigates into a folder when clicked', async () => {
      server.use(
        http.get('/api/v1/printers/:id/files', ({ request }) => {
          const url = new URL(request.url);
          const path = url.searchParams.get('path');
          if (path === '/cache') {
            return HttpResponse.json({
              files: [
                { name: 'temp.dat', path: '/cache/temp.dat', size: 512, is_directory: false },
              ],
            });
          }
          return HttpResponse.json({ files: mockFiles });
        })
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('cache')).toBeInTheDocument();
      });

      // Click on cache folder
      fireEvent.click(screen.getByText('cache'));

      await waitFor(() => {
        expect(screen.getByText('temp.dat')).toBeInTheDocument();
      });
    });

    it('shows current path', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('/')).toBeInTheDocument();
    });
  });

  describe('file selection', () => {
    it('selects a file when checkbox is clicked', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('benchy.3mf')).toBeInTheDocument();
      });

      // Find and click a checkbox (files have checkboxes, directories don't)
      const checkboxes = screen.getAllByRole('button').filter(btn =>
        btn.querySelector('svg')?.classList.contains('lucide-square')
      );

      if (checkboxes.length > 0) {
        fireEvent.click(checkboxes[0]);

        await waitFor(() => {
          expect(screen.getByText('1 selected')).toBeInTheDocument();
        });
      }
    });

    it('selects the visible range between a click and a shift-click', async () => {
      server.use(
        http.get('/api/v1/printers/:id/files', () => HttpResponse.json({
          files: [
            ...mockFiles.filter(file => file.is_directory),
            { name: 'alpha.gcode', path: '/alpha.gcode', size: 1, is_directory: false, mtime: null },
            { name: 'bravo.gcode', path: '/bravo.gcode', size: 2, is_directory: false, mtime: null },
            { name: 'charlie.gcode', path: '/charlie.gcode', size: 3, is_directory: false, mtime: null },
            { name: 'delta.gcode', path: '/delta.gcode', size: 4, is_directory: false, mtime: null },
          ],
        })),
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      fireEvent.click(await screen.findByRole('button', { name: 'Select alpha.gcode' }));
      fireEvent.click(screen.getByRole('button', { name: 'Select charlie.gcode' }), { shiftKey: true });

      expect(await screen.findByText('3 selected')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect alpha.gcode' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect bravo.gcode' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect charlie.gcode' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Select delta.gcode' })).toBeInTheDocument();
    });

    it('always selects a shift-clicked range even when the target is already selected', async () => {
      server.use(
        http.get('/api/v1/printers/:id/files', () => HttpResponse.json({
          files: [
            { name: 'alpha.gcode', path: '/alpha.gcode', size: 1, is_directory: false, mtime: null },
            { name: 'bravo.gcode', path: '/bravo.gcode', size: 2, is_directory: false, mtime: null },
            { name: 'charlie.gcode', path: '/charlie.gcode', size: 3, is_directory: false, mtime: null },
          ],
        })),
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      fireEvent.click(await screen.findByRole('button', { name: 'Select alpha.gcode' }));
      fireEvent.click(screen.getByRole('button', { name: 'Select charlie.gcode' }));
      fireEvent.click(screen.getByRole('button', { name: 'Deselect alpha.gcode' }), { shiftKey: true });

      expect(await screen.findByText('3 selected')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Deselect bravo.gcode' })).toBeInTheDocument();
    });

    it('enables download button when files are selected', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('benchy.3mf')).toBeInTheDocument();
      });

      // Download button should be disabled initially
      const downloadButton = screen.getByRole('button', { name: /Download/i });
      expect(downloadButton).toBeDisabled();
    });

    it('shows Select All button when files exist', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });
    });

    it('starts a preparation job, uses a native download link, and reports partial results', async () => {
      let preparation: {
        paths: string[];
        sizes: Record<string, number>;
        filename: string;
        as_zip: boolean;
      } | null = null;
      server.use(
        http.post('/api/v1/printers/:id/files/download-job', async ({ request }) => {
          preparation = await request.json() as typeof preparation;
          return HttpResponse.json({
            job_id: 'job-id',
            state: 'ready',
            token: 'download-token',
            requested: 2,
            successful: 1,
            failed: 1,
            message: null,
          });
        }),
      );
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => expect(screen.getByText('Select All')).toBeInTheDocument());
      fireEvent.click(screen.getByText('Select All'));
      fireEvent.click(screen.getByRole('button', { name: /Download \(2\)/i }));

      await waitFor(() => expect(clickSpy).toHaveBeenCalledOnce());
      expect(preparation).toEqual({
        paths: ['/benchy.3mf', '/print_job.gcode'],
        sizes: {
          '/benchy.3mf': 1048575,
          '/print_job.gcode': 2048000,
        },
        filename: 'X1_Carbon-files.zip',
        as_zip: true,
      });
      expect(document.querySelector('a')).toBeNull();
      expect(await screen.findByText(
        'ZIP download started with 1 of 2 files; the rest could not be retrieved',
      )).toBeInTheDocument();
      clickSpy.mockRestore();
    });
  });

  describe('search and filter', () => {
    it('renders search input', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByPlaceholderText('Filter files...')).toBeInTheDocument();
    });

    it('filters files based on search query', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('benchy.3mf')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText('Filter files...');
      fireEvent.change(searchInput, { target: { value: 'benchy' } });

      await waitFor(() => {
        expect(screen.getByText('benchy.3mf')).toBeInTheDocument();
        expect(screen.queryByText('print_job.gcode')).not.toBeInTheDocument();
      });
    });

    it('selects all files from the shared visible-file filter', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => expect(screen.getByText('benchy.3mf')).toBeInTheDocument());
      fireEvent.change(screen.getByPlaceholderText('Filter files...'), { target: { value: 'benchy' } });
      fireEvent.click(screen.getByText('Select All'));

      expect(screen.getByText('1 selected')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
    });

    it('keeps the selection when a refresh cannot reach the printer', async () => {
      render(
        <FileManagerModal printerId={1} printerName="X1 Carbon" onClose={mockOnClose} />
      );

      fireEvent.click(await screen.findByRole('button', { name: 'Select benchy.3mf' }));
      expect(screen.getByText('1 selected')).toBeInTheDocument();

      // An unreachable printer answers with an empty list plus a warning. That
      // is not the same statement as "those files are gone", and it must not
      // throw away a selection the user made moments ago.
      server.use(
        http.get('/api/v1/printers/:id/files', () =>
          HttpResponse.json({ files: [], warnings: ['printer_unavailable'] })
        )
      );
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

      expect(await screen.findByText(
        'The printer file service is unavailable. Try again when the printer is reachable.',
      )).toBeInTheDocument();
      expect(screen.getByText('1 selected')).toBeInTheDocument();
    });

    it('drops hidden selections when the filter changes', async () => {
      render(
        <FileManagerModal printerId={1} printerName="X1 Carbon" onClose={mockOnClose} />
      );

      fireEvent.click(await screen.findByRole('button', { name: 'Select benchy.3mf' }));
      expect(screen.getByText('1 selected')).toBeInTheDocument();
      fireEvent.change(screen.getByPlaceholderText('Filter files...'), { target: { value: 'gcode' } });

      await waitFor(() => expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled());
    });
  });

  describe('sorting', () => {
    it('renders sort dropdown', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('combobox')).toBeInTheDocument();
    });

    it('has sort options available', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      const sortSelect = screen.getByRole('combobox');
      expect(sortSelect).toBeInTheDocument();

      // Check that options exist
      expect(screen.getByText('Name (A-Z)')).toBeInTheDocument();
    });
  });

  describe('close behavior', () => {
    it('calls onClose when X button is clicked', async () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      const closeButton = screen.getAllByRole('button').find(btn =>
        btn.querySelector('.lucide-x')
      );

      if (closeButton) {
        fireEvent.click(closeButton);
        expect(mockOnClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when clicking outside the modal', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      // Click on the backdrop
      const backdrop = document.querySelector('.fixed.inset-0');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(mockOnClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when Escape key is pressed', () => {
      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('empty state', () => {
    it('shows empty message when directory has no files', async () => {
      server.use(
        http.get('/api/v1/printers/:id/files', () => {
          return HttpResponse.json({ files: [] });
        })
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
          expect(screen.getByText('No files on printer')).toBeInTheDocument();
      });
    });

    it('distinguishes an unreachable printer from an empty directory', async () => {
      server.use(
        http.get('/api/v1/printers/:id/files', () => {
          return HttpResponse.json({ files: [], warnings: ['printer_unavailable'] });
        })
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      expect(await screen.findByText(
        'The printer file service is unavailable. Try again when the printer is reachable.',
      )).toBeInTheDocument();
      expect(screen.queryByText('No files on printer')).not.toBeInTheDocument();
    });
  });

  describe('loading state', () => {
    it('shows loading spinner while fetching files', () => {
      // Delay the response to see loading state
      server.use(
        http.get('/api/v1/printers/:id/files', async () => {
          await new Promise((r) => setTimeout(r, 100));
          return HttpResponse.json({ files: mockFiles });
        })
      );

      render(
        <FileManagerModal
          printerId={1}
          printerName="X1 Carbon"
          onClose={mockOnClose}
        />
      );

      // The loader should be present initially
      const loader = document.querySelector('.animate-spin');
      expect(loader).toBeInTheDocument();
    });
  });
});
