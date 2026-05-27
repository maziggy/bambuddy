/**
 * Tests for the ProjectDetailPage component.
 * Covers: isSlicedFilename conditional print-button logic, linked folder file rendering,
 * and the PrintModal open trigger with projectId.
 */

/// <reference types="@testing-library/jest-dom" />

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ProjectDetailPage } from '../../pages/ProjectDetailPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock useParams so the component receives a fixed project id without a nested Router
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: '1' }),
    useNavigate: () => vi.fn(),
  };
});

const mockProject = {
  id: 1,
  name: 'Test Project',
  description: 'A test project',
  color: '#00ae42',
  status: 'active',
  priority: 'normal',
  due_date: null,
  notes: null,
  parent_id: null,
  archive_count: 0,
  total_print_time_seconds: 0,
  total_filament_grams: 0,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const mockFolder = {
  id: 10,
  name: 'Sliced Files',
  project_id: 1,
  archive_id: null,
  parent_id: null,
  file_count: 3,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

function makeFile(overrides: { id: number; filename: string; file_type?: string }) {
  return {
    id: overrides.id,
    filename: overrides.filename,
    print_name: null,
    file_type: overrides.file_type ?? '3mf',
    folder_id: 10,
    project_id: 1,
    file_hash: null,
    file_size_bytes: 1024,
    thumbnail_path: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    duplicate_count: 0,
  };
}

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/projects/:id', () => {
        return HttpResponse.json(mockProject);
      }),
      http.get('/api/v1/projects/:id/archives', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/projects/:id/bom', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/projects/:id/timeline', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/library/folders/by-project/:id', () => {
        return HttpResponse.json([mockFolder]);
      }),
    );
  });

  describe('isSlicedFilename — conditional print button', () => {
    it('shows print button for .gcode files', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 1, filename: 'benchy.gcode', file_type: 'gcode' })]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByTitle('Print Now')).toBeInTheDocument();
      });
    });

    it('shows print button for .gcode.3mf files', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 2, filename: 'benchy.gcode.3mf', file_type: '3mf' })]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByTitle('Print Now')).toBeInTheDocument();
      });
    });

    it('does NOT show print button for .gcode.bak files (regression for includes bug)', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 3, filename: 'benchy.gcode.bak', file_type: '3mf' })]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('benchy.gcode.bak')).toBeInTheDocument();
      });

      expect(screen.queryByTitle('Print Now')).not.toBeInTheDocument();
    });

    it('does NOT show print button for .stl files', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 4, filename: 'model.stl', file_type: 'stl' })]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('model.stl')).toBeInTheDocument();
      });

      expect(screen.queryByTitle('Print Now')).not.toBeInTheDocument();
    });
  });

  describe('linked folder file rendering', () => {
    it('renders filenames from linked folder', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([
            makeFile({ id: 5, filename: 'part_a.gcode.3mf', file_type: '3mf' }),
            makeFile({ id: 6, filename: 'design.stl', file_type: 'stl' }),
          ]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('part_a.gcode.3mf')).toBeInTheDocument();
        expect(screen.getByText('design.stl')).toBeInTheDocument();
      });
    });

    it('renders the linked folder name', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([]);
        })
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('Sliced Files')).toBeInTheDocument();
      });
    });
  });

  describe('print modal trigger', () => {
    it('opens PrintModal when print button is clicked on a sliced file', async () => {
      const user = userEvent.setup();

      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 7, filename: 'cube.gcode.3mf', file_type: '3mf' })]);
        }),
        http.get('/api/v1/printers/', () => {
          return HttpResponse.json([]);
        }),
        http.get('/api/v1/library/files/:id', () => {
          return HttpResponse.json(makeFile({ id: 7, filename: 'cube.gcode.3mf', file_type: '3mf' }));
        }),
        http.get('/api/v1/library/files/:id/plates', () => {
          return HttpResponse.json({ is_multi_plate: false, plates: [] });
        }),
        http.get('/api/v1/library/files/:id/filament-requirements', () => {
          return HttpResponse.json({ file_id: 7, filename: 'cube.gcode.3mf', filaments: [] });
        }),
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByTitle('Print Now')).toBeInTheDocument();
      });

      await user.click(screen.getByTitle('Print Now'));

      // PrintModal should open — look for the modal heading "Print"
      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Print' })).toBeInTheDocument();
      });
    });
  });
});
