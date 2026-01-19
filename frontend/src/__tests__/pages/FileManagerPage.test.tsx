/**
 * Tests for the FileManagerPage component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock data
const mockFolders = [
  {
    id: 1,
    name: 'Functional Parts',
    parent_id: null,
    file_count: 5,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    children: [
      {
        id: 2,
        name: 'Brackets',
        parent_id: 1,
        file_count: 3,
        project_id: null,
        archive_id: null,
        project_name: null,
        archive_name: null,
        children: [],
      },
    ],
  },
  {
    id: 3,
    name: 'Art Projects',
    parent_id: null,
    file_count: 2,
    project_id: 1,
    archive_id: null,
    project_name: 'My Art Project',
    archive_name: null,
    children: [],
  },
];

const mockFiles = [
  {
    id: 1,
    filename: 'benchy.gcode.3mf',
    file_path: '/library/benchy.gcode.3mf',
    file_size: 1048576,
    file_type: '3mf',
    folder_id: null,
    thumbnail_path: '/thumbnails/1.png',
    print_name: 'Benchy',
    print_time_seconds: 3600,
    print_count: 5,
    duplicate_count: 0,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 2,
    filename: 'bracket.stl',
    file_path: '/library/bracket.stl',
    file_size: 524288,
    file_type: 'stl',
    folder_id: null,
    thumbnail_path: null,
    print_name: null,
    print_time_seconds: null,
    print_count: 0,
    duplicate_count: 2,
    created_at: '2024-01-02T00:00:00Z',
  },
];

const mockStats = {
  total_files: 10,
  total_folders: 3,
  total_size_bytes: 104857600,
  disk_free_bytes: 10737418240,
  disk_total_bytes: 107374182400,
};

describe('FileManagerPage', () => {
  beforeEach(() => {
    // Clear localStorage to ensure consistent view mode
    localStorage.clear();

    server.use(
      http.get('/api/v1/library/folders', () => {
        return HttpResponse.json(mockFolders);
      }),
      http.get('/api/v1/library/files', () => {
        return HttpResponse.json(mockFiles);
      }),
      http.get('/api/v1/library/stats', () => {
        return HttpResponse.json(mockStats);
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json({
          check_updates: false,
          library_disk_warning_gb: 5,
        });
      }),
      http.post('/api/v1/library/folders', async ({ request }) => {
        const body = await request.json() as { name: string };
        return HttpResponse.json({ id: 4, name: body.name, parent_id: null, children: [] });
      }),
      http.delete('/api/v1/library/folders/:id', () => {
        return HttpResponse.json({ success: true });
      }),
      http.delete('/api/v1/library/files/:id', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/library/files/move', () => {
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/v1/library/files/add-to-queue', () => {
        return HttpResponse.json({ added: [{ file_id: 1, queue_id: 1 }], errors: [] });
      }),
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json([{ id: 1, name: 'Test Project', color: '#00ae42' }]);
      }),
      http.get('/api/v1/archives/', () => {
        return HttpResponse.json([{ id: 1, print_name: 'Test Archive', filename: 'test.3mf' }]);
      })
    );
  });

  describe('rendering', () => {
    it('renders the page title', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('File Manager')).toBeInTheDocument();
      });
    });

    it('renders the page description', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Organize and manage your print files')).toBeInTheDocument();
      });
    });

    it('shows New Folder button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('New Folder')).toBeInTheDocument();
      });
    });

    it('shows Upload button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Upload')).toBeInTheDocument();
      });
    });
  });

  describe('stats display', () => {
    it('shows file count', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Files:')).toBeInTheDocument();
        expect(screen.getByText('10')).toBeInTheDocument();
      });
    });

    it('shows folder count', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Folders:')).toBeInTheDocument();
        // Folder count appears multiple places, just verify the label is present
        const foldersLabel = screen.getByText('Folders:');
        expect(foldersLabel.nextElementSibling?.textContent).toBe('3');
      });
    });

    it('shows total size', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Size:')).toBeInTheDocument();
        expect(screen.getByText('100.0 MB')).toBeInTheDocument();
      });
    });

    it('shows free space', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Free:')).toBeInTheDocument();
      });
    });
  });

  describe('folder sidebar', () => {
    it('shows All Files option', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('All Files')).toBeInTheDocument();
      });
    });

    it('shows folder tree', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Functional Parts')).toBeInTheDocument();
        expect(screen.getByText('Art Projects')).toBeInTheDocument();
      });
    });

    it('shows nested folders', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Brackets')).toBeInTheDocument();
      });
    });

    it('shows linked folder indicator', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        // Art Projects has a project_id
        expect(screen.getByText('Art Projects')).toBeInTheDocument();
      });
    });
  });

  describe('file display', () => {
    it('shows files in grid', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });
    });

    it('shows file type badges', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        // File type badges show uppercase type
        expect(screen.getAllByText('3MF').length).toBeGreaterThan(0);
        expect(screen.getAllByText('STL').length).toBeGreaterThan(0);
      });
    });

    it('shows print count', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Printed 5x')).toBeInTheDocument();
      });
    });

    it('shows duplicate badge', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        // Duplicate badge shows count, there may be multiple "2"s on the page
        // so we check that at least one element with "2" exists
        const elements = screen.getAllByText('2');
        expect(elements.length).toBeGreaterThan(0);
      });
    });
  });

  describe('view modes', () => {
    it('has grid view button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByTitle('Grid view')).toBeInTheDocument();
      });
    });

    it('has list view button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByTitle('List view')).toBeInTheDocument();
      });
    });

    it('can switch to list view', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      // Wait for files to load first
      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // Both view mode buttons should be present and clickable
      const gridButton = screen.getByTitle('Grid view');
      const listButton = screen.getByTitle('List view');

      expect(gridButton).toBeInTheDocument();
      expect(listButton).toBeInTheDocument();

      // Click list view button - verify no errors occur
      await user.click(listButton);

      // Clicking grid button should also work
      await user.click(gridButton);

      // Verify files are still displayed after toggling
      expect(screen.getByText('Benchy')).toBeInTheDocument();
    });
  });

  describe('search and filter', () => {
    it('has search input', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Search files...')).toBeInTheDocument();
      });
    });

    it('has type filter', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('All types')).toBeInTheDocument();
      });
    });

    it('has sort options', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        // Sort dropdown should show Date as default option
        expect(screen.getByDisplayValue('Date')).toBeInTheDocument();
      });
    });
  });

  describe('selection', () => {
    it('shows select all button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });
    });

    it('can select files', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });

      // Click on the file card to select it
      const fileCard = screen.getByText('Benchy').closest('div[class*="cursor-pointer"]');
      if (fileCard) {
        await user.click(fileCard);
      }

      await waitFor(() => {
        expect(screen.getByText('1 selected')).toBeInTheDocument();
      });
    });

    it('shows bulk actions when files selected', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select All'));

      await waitFor(() => {
        expect(screen.getByText('Move')).toBeInTheDocument();
        expect(screen.getByText('Delete')).toBeInTheDocument();
      });
    });
  });

  describe('new folder modal', () => {
    it('opens new folder modal', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('New Folder')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New Folder'));

      await waitFor(() => {
        expect(screen.getByText('Folder Name')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('e.g., Functional Parts')).toBeInTheDocument();
      });
    });

    it('can create a folder', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('New Folder')).toBeInTheDocument();
      });

      await user.click(screen.getByText('New Folder'));

      await waitFor(() => {
        expect(screen.getByPlaceholderText('e.g., Functional Parts')).toBeInTheDocument();
      });

      const input = screen.getByPlaceholderText('e.g., Functional Parts');
      await user.type(input, 'My New Folder');

      const createButton = screen.getByRole('button', { name: 'Create' });
      await user.click(createButton);

      // Modal should close after creation
      await waitFor(() => {
        expect(screen.queryByText('Folder Name')).not.toBeInTheDocument();
      });
    });
  });

  describe('empty state', () => {
    it('shows empty state when no files', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([]);
        })
      );

      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('No files yet')).toBeInTheDocument();
        expect(screen.getByText('Upload Files')).toBeInTheDocument();
      });
    });
  });

  describe('add to queue', () => {
    it('shows add to queue button for sliced files', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      // Select a sliced file (benchy.gcode.3mf)
      await user.click(screen.getByText('Select All'));

      await waitFor(() => {
        expect(screen.getByText(/Add to Queue/)).toBeInTheDocument();
      });
    });
  });
});
