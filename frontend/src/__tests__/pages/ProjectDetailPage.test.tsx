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
        expect(screen.getByTitle('Print')).toBeInTheDocument();
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
        expect(screen.getByTitle('Print')).toBeInTheDocument();
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

      expect(screen.queryByTitle('Print')).not.toBeInTheDocument();
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

      expect(screen.queryByTitle('Print')).not.toBeInTheDocument();
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
        expect(screen.getByTitle('Print')).toBeInTheDocument();
      });

      await user.click(screen.getByTitle('Print'));

      // PrintModal should open — look for the modal heading "Print"
      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Print' })).toBeInTheDocument();
      });
    });
  });
  describe('per-file print progress (#1897)', () => {
    it('shows X / N badges and the Complete Sets bar when target_sets is set', async () => {
      server.use(
        http.get('/api/v1/projects/:id', () => {
          return HttpResponse.json({ ...mockProject, target_sets: 10 });
        }),
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([
            makeFile({ id: 5, filename: 'plate_1.gcode.3mf', file_type: '3mf' }),
            makeFile({ id: 6, filename: 'plate_2.gcode.3mf', file_type: '3mf' }),
          ]);
        }),
        http.get('/api/v1/projects/:id/file-progress', () => {
          return HttpResponse.json([{ file_id: 5, completed_count: 3 }]);
        }),
      );

      render(<ProjectDetailPage />);

      // Per-file badges: 3 / 10 for plate_1, 0 / 10 for the never-printed plate_2
      await waitFor(() => {
        expect(screen.getByTitle('3 of 10 completed prints')).toBeInTheDocument();
      });
      expect(screen.getByTitle('0 of 10 completed prints')).toBeInTheDocument();

      // Complete sets = min across printable files = 0
      expect(screen.getByText('Complete Sets')).toBeInTheDocument();
      expect(
        screen.getByText((_, element) => element?.tagName === 'SPAN' && element.textContent === '0 / 10 sets')
      ).toBeInTheDocument();
    });

    it('counts a complete set once every printable file reached the target', async () => {
      server.use(
        http.get('/api/v1/projects/:id', () => {
          return HttpResponse.json({ ...mockProject, target_sets: 2 });
        }),
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([
            makeFile({ id: 5, filename: 'plate_1.gcode.3mf', file_type: '3mf' }),
            // STL is not printable and must not drag the set count to 0
            makeFile({ id: 8, filename: 'source.stl', file_type: 'stl' }),
            makeFile({ id: 6, filename: 'plate_2.gcode.3mf', file_type: '3mf' }),
          ]);
        }),
        http.get('/api/v1/projects/:id/file-progress', () => {
          // plate_1 overshot the target; capped at 2 for the set count
          return HttpResponse.json([
            { file_id: 5, completed_count: 3 },
            { file_id: 6, completed_count: 2 },
          ]);
        }),
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(
          screen.getByText((_, element) => element?.tagName === 'SPAN' && element.textContent === '2 / 2 sets')
        ).toBeInTheDocument();
      });
    });

    it('shows a plain printed-count badge when no target_sets is set', async () => {
      server.use(
        http.get('/api/v1/library/files', () => {
          return HttpResponse.json([makeFile({ id: 5, filename: 'plate_1.gcode.3mf', file_type: '3mf' })]);
        }),
        http.get('/api/v1/projects/:id/file-progress', () => {
          return HttpResponse.json([{ file_id: 5, completed_count: 4 }]);
        }),
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('4\u00d7')).toBeInTheDocument();
      });
      // No sets bar without a target
      expect(screen.queryByText('Complete Sets')).not.toBeInTheDocument();
    });
  });
  describe('sub-project roll-up (#1264)', () => {
    const withChildren = {
      ...mockProject,
      // Supplying `stats` opens the cost card, which reads `budget` — the API
      // always sends the key, so the mock has to as well.
      budget: null,
      descendant_count: 2,
      children: [
        {
          id: 2,
          name: 'Wing',
          color: '#ff0000',
          status: 'active',
          progress_percent: 50,
          descendant_count: 1,
          total_archives: 4,
          completed_prints: 4,
          total_print_time_hours: 6,
          total_filament_grams: 250,
          total_cost: 12.5,
        },
      ],
      stats: {
        total_archives: 1,
        total_items: 1,
        completed_prints: 1,
        failed_prints: 0,
        queued_prints: 0,
        in_progress_prints: 0,
        total_print_time_hours: 2,
        total_filament_grams: 100,
        progress_percent: null,
        parts_progress_percent: null,
        estimated_cost: 5,
        total_energy_kwh: 0,
        total_energy_cost: 0,
        remaining_prints: null,
        remaining_parts: null,
        bom_total_items: 0,
        bom_completed_items: 0,
        bom_cost: 0,
      },
      rollup_stats: {
        total_archives: 5,
        total_items: 5,
        completed_prints: 5,
        failed_prints: 0,
        queued_prints: 0,
        in_progress_prints: 0,
        total_print_time_hours: 8,
        total_filament_grams: 350,
        progress_percent: 40,
        parts_progress_percent: null,
        estimated_cost: 17.5,
        total_energy_kwh: 0,
        total_energy_cost: 0,
        remaining_prints: 3,
        remaining_parts: null,
        bom_total_items: 0,
        bom_completed_items: 0,
        bom_cost: 0,
      },
    };

    it('shows the whole programme alongside the project own numbers', async () => {
      server.use(http.get('/api/v1/projects/:id', () => HttpResponse.json(withChildren)));

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('Including 2 sub-projects')).toBeInTheDocument();
      });
      // Both sets are on screen at once, which is the point — the roll-up must
      // not quietly replace what the project itself printed.
      expect(screen.getByText('350g')).toBeInTheDocument();
      expect(screen.getByText('100g')).toBeInTheDocument();
    });

    it('stays quiet when the project has no sub-projects', async () => {
      // Reversion-proof: if the roll-up were computed unconditionally it would
      // print a second, identical set of figures under every ordinary project.
      server.use(
        http.get('/api/v1/projects/:id', () =>
          HttpResponse.json({ ...withChildren, children: [], descendant_count: 0, rollup_stats: null })
        )
      );

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('100g')).toBeInTheDocument();
      });
      expect(screen.queryByText(/Including .* sub-projects/)).not.toBeInTheDocument();
    });

    it('gives each listed sub-project its own branch total', async () => {
      server.use(http.get('/api/v1/projects/:id', () => HttpResponse.json(withChildren)));

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('Wing')).toBeInTheDocument();
      });
      expect(screen.getByText('4 jobs')).toBeInTheDocument();
      expect(screen.getByText('250g')).toBeInTheDocument();
      expect(screen.getByText('50%')).toBeInTheDocument();
    });

    it('marks a sub-project that is itself a parent', async () => {
      // Otherwise its figures look inflated for a single project rather than
      // covering the branch under it.
      server.use(http.get('/api/v1/projects/:id', () => HttpResponse.json(withChildren)));

      render(<ProjectDetailPage />);

      await waitFor(() => {
        expect(screen.getByText('Wing')).toBeInTheDocument();
      });
      const row = screen.getByText('Wing').closest('a');
      expect(row).not.toBeNull();
      expect(row!.textContent).toContain('1');
    });
  });
});
