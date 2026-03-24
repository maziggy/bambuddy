/**
 * Tests for External Folder functionality in FileManagerPage.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock data with external folder
const mockFoldersWithExternal = [
  {
    id: 1,
    name: 'Regular Folder',
    parent_id: null,
    file_count: 3,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    external_path: null,
    external_readonly: false,
    children: [],
  },
  {
    id: 2,
    name: 'NAS Prints',
    parent_id: null,
    file_count: 5,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: true,
    external_path: '/mnt/nas/prints',
    external_readonly: true,
    children: [],
  },
  {
    id: 3,
    name: 'USB Drive',
    parent_id: null,
    file_count: 2,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: true,
    external_path: '/mnt/usb',
    external_readonly: false,
    children: [],
  },
];

const mockFiles = [
  {
    id: 1,
    filename: 'benchy.3mf',
    file_path: '/mnt/nas/prints/benchy.3mf',
    file_size: 1048576,
    file_type: '3mf',
    folder_id: 2,
    is_external: true,
    thumbnail_path: null,
    print_name: 'Benchy',
    print_time_seconds: 3600,
    print_count: 0,
    duplicate_count: 0,
    created_at: '2024-01-01T00:00:00Z',
  },
];

const mockStats = {
  total_files: 10,
  total_folders: 3,
  total_size_bytes: 104857600,
  disk_free_bytes: 10737418240,
  disk_total_bytes: 107374182400,
};

describe('FileManagerPage - External Folders', () => {
  beforeEach(() => {
    localStorage.clear();

    server.use(
      http.get('/api/v1/library/folders', () => {
        return HttpResponse.json(mockFoldersWithExternal);
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
          check_printer_firmware: false,
          library_disk_warning_gb: 5,
        });
      }),
      http.post('/api/v1/library/folders/external', async ({ request }) => {
        const body = await request.json() as { name: string; external_path: string };
        return HttpResponse.json({
          id: 10,
          name: body.name,
          parent_id: null,
          is_external: true,
          external_path: body.external_path,
          external_readonly: true,
          external_show_hidden: false,
          file_count: 0,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      }),
      http.post('/api/v1/library/folders/:id/scan', () => {
        return HttpResponse.json({ status: 'success', added: 3, removed: 0 });
      }),
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/archives/', () => {
        return HttpResponse.json([]);
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
    );
  });

  describe('rendering', () => {
    it('shows Link External button', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Link External')).toBeInTheDocument();
      });
    });

    it('shows external folder in sidebar', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('NAS Prints')).toBeInTheDocument();
        expect(screen.getByText('USB Drive')).toBeInTheDocument();
      });
    });

    it('shows regular folder alongside external', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Regular Folder')).toBeInTheDocument();
        expect(screen.getByText('NAS Prints')).toBeInTheDocument();
      });
    });

    it('shows read-only indicator for readonly external folders', async () => {
      render(<FileManagerPage />);

      await waitFor(() => {
        // NAS Prints is readonly, should have a lock icon title
        const lockIcons = document.querySelectorAll('[title="Read Only"]');
        expect(lockIcons.length).toBeGreaterThan(0);
      });
    });
  });

  describe('external folder modal', () => {
    it('opens modal when Link External clicked', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Link External')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Link External'));

      await waitFor(() => {
        expect(screen.getByText('Link External Folder')).toBeInTheDocument();
      });
    });

    it('modal has name and path fields', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Link External')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Link External'));

      await waitFor(() => {
        expect(screen.getByPlaceholderText('e.g., NAS Prints')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('/mnt/nas/3d-prints')).toBeInTheDocument();
      });
    });

    it('modal has readonly checkbox checked by default', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Link External')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Link External'));

      await waitFor(() => {
        const readonlyCheckbox = screen.getByText('Read Only').previousElementSibling as HTMLInputElement;
        expect(readonlyCheckbox).toBeChecked();
      });
    });

    it('modal can be closed', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Link External')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Link External'));

      await waitFor(() => {
        expect(screen.getByText('Link External Folder')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Cancel'));

      await waitFor(() => {
        expect(screen.queryByText('Link External Folder')).not.toBeInTheDocument();
      });
    });
  });

  describe('external folder info bar', () => {
    it('shows info bar when external folder selected', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('NAS Prints')).toBeInTheDocument();
      });

      // Click on NAS Prints folder - there are multiple elements, get the one in the sidebar
      const folderElements = screen.getAllByText('NAS Prints');
      await user.click(folderElements[0]);

      await waitFor(() => {
        expect(screen.getByText('External Folder')).toBeInTheDocument();
        expect(screen.getByText('/mnt/nas/prints')).toBeInTheDocument();
      });
    });

    it('shows scan button for external folders', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('NAS Prints')).toBeInTheDocument();
      });

      const folderElements = screen.getAllByText('NAS Prints');
      await user.click(folderElements[0]);

      await waitFor(() => {
        expect(screen.getByText('Scan')).toBeInTheDocument();
      });
    });

    it('does not show info bar for regular folders', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => {
        expect(screen.getByText('Regular Folder')).toBeInTheDocument();
      });

      const folderElements = screen.getAllByText('Regular Folder');
      await user.click(folderElements[0]);

      // External Folder label should NOT appear
      await waitFor(() => {
        expect(screen.queryByText('External Folder')).not.toBeInTheDocument();
      });
    });
  });
});
