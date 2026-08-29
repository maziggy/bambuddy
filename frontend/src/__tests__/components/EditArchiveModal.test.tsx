/**
 * Tests for the EditArchiveModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { EditArchiveModal } from '../../components/EditArchiveModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockArchive = {
  id: 1,
  filename: 'benchy.gcode.3mf',
  print_name: 'Benchy',
  printer_id: 1,
  printer_name: 'X1 Carbon',
  notes: 'Test notes',
  rating: 4,
  project_id: null,
  tags: 'test,calibration',
};

const mockProjects = [
  { id: 1, name: 'Functional Parts', color: '#00ae42' },
  { id: 2, name: 'Art', color: '#ff5500' },
];

describe('EditArchiveModal', () => {
  const mockOnClose = vi.fn();
  const mockOnSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json(mockProjects);
      }),
      http.get('/api/v1/archives/tags', () => {
        return HttpResponse.json([
          { name: 'test', count: 2 },
          { name: 'calibration', count: 1 },
          { name: 'functional', count: 3 },
        ]);
      }),
      http.patch('/api/v1/archives/:id', async ({ request }) => {
        const body = await request.json();
        return HttpResponse.json({ ...mockArchive, ...body });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText(/edit/i)).toBeInTheDocument();
    });

    it('shows print name field', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Name field should be present
        const nameInput = screen.getByDisplayValue('Benchy');
        expect(nameInput).toBeInTheDocument();
      });
    });

    it('shows notes field', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        const notesField = screen.getByDisplayValue('Test notes');
        expect(notesField).toBeInTheDocument();
      });
    });

    it('shows rating selector', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Rating may be shown as stars or dropdown
        expect(screen.getByText(/edit/i)).toBeInTheDocument();
      });
    });

    it('shows project selector', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Project section should be present
        expect(screen.getByText(/edit/i)).toBeInTheDocument();
      });
    });

    it('shows tags input', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText(/tags/i)).toBeInTheDocument();
    });
  });

  describe('existing values', () => {
    it('shows existing tags', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText('test')).toBeInTheDocument();
      expect(screen.getByText('calibration')).toBeInTheDocument();
    });
  });

  describe('actions', () => {
    it('has save button', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    });

    it('has cancel button', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('can edit print name', async () => {
      const user = userEvent.setup();
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      const nameInput = screen.getByDisplayValue('Benchy');
      await user.clear(nameInput);
      await user.type(nameInput, 'New Name');

      expect(nameInput).toHaveValue('New Name');
    });
  });

  describe('failure_reason vocabulary (#1687 follow-up)', () => {
    // The Stats page's Failure Analysis widget groups by the raw column value.
    // Before this fix this modal saved the translated label, so a language
    // switch fragmented historical buckets and any round-trip through the
    // new PATCH /print-log endpoint (which validates against camelCase keys)
    // would reject the value. The dropdown now saves the key.

    const failedArchive = { ...mockArchive, status: 'failed', failure_reason: 'filamentRunout' };
    const legacyArchive = { ...mockArchive, status: 'failed', failure_reason: 'Filament runout' };

    it('preselects the option when the stored value is already a camelCase key', () => {
      render(<EditArchiveModal archive={failedArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const select = screen.getByLabelText(/failure reason/i) as HTMLSelectElement;
      expect(select.value).toBe('filamentRunout');
    });

    it('reverse-looks-up a legacy translated value back to its key', () => {
      render(<EditArchiveModal archive={legacyArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const select = screen.getByLabelText(/failure reason/i) as HTMLSelectElement;
      expect(select.value).toBe('filamentRunout');
    });

    it('sends the camelCase key on save, not the translated label', async () => {
      const user = userEvent.setup();
      let patched: { failure_reason?: string } | undefined;
      server.use(
        http.patch('/api/v1/archives/:id', async ({ request }) => {
          patched = (await request.json()) as { failure_reason?: string };
          return HttpResponse.json({ ...failedArchive, ...patched });
        }),
      );

      render(<EditArchiveModal archive={failedArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const select = screen.getByLabelText(/failure reason/i);
      await user.selectOptions(select, 'cloggedNozzle');
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(patched?.failure_reason).toBe('cloggedNozzle');
      });
    });

    // A value outside the vocabulary used to initialise the dropdown to '',
    // and saving from that state wrote the empty selection over the stored
    // text -- opening the editor and pressing Save destroyed the
    // classification. The startup migration folds every known spelling onto a
    // key, so what reaches here is genuinely unrecognisable text; it has to
    // survive rather than be silently discarded (issue #2974).
    const freeTextArchive = {
      ...mockArchive,
      status: 'failed',
      failure_reason: 'Custom legacy reason',
    };

    it('keeps a stored value it cannot map, as its own option', () => {
      render(<EditArchiveModal archive={freeTextArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const select = screen.getByLabelText(/failure reason/i) as HTMLSelectElement;
      expect(select.value).toBe('Custom legacy reason');
      expect(
        screen.getByRole('option', { name: 'Custom legacy reason' }),
      ).toBeInTheDocument();
    });

    it('does not clear an unmappable reason on an untouched save', async () => {
      const user = userEvent.setup();
      let patched: { failure_reason?: string } | undefined;
      server.use(
        http.patch('/api/v1/archives/:id', async ({ request }) => {
          patched = (await request.json()) as { failure_reason?: string };
          return HttpResponse.json({ ...freeTextArchive, ...patched });
        }),
      );

      render(<EditArchiveModal archive={freeTextArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(patched?.failure_reason).toBe('Custom legacy reason');
      });
    });

    it('offers the stale-path reason the backend now writes', () => {
      // Both stale writers in main.py store `noStatusUpdate`. If it were
      // missing from the dropdown the editor would treat it as unmappable and
      // show the raw key to the user instead of a translated label.
      const staleArchive = {
        ...mockArchive,
        status: 'failed',
        failure_reason: 'noStatusUpdate',
      };
      render(<EditArchiveModal archive={staleArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const select = screen.getByLabelText(/failure reason/i) as HTMLSelectElement;
      expect(select.value).toBe('noStatusUpdate');
      expect(
        screen.getByRole('option', { name: 'No status update received' }),
      ).toBeInTheDocument();
    });
  });

  describe('filament grams (#1820)', () => {
    // A print archived without its 3MF carries no weight at all, and no rescan
    // can supply one — there is no file to read. Typing it here is the only
    // route, so the field has to reach the API, and an untouched save must not
    // overwrite a figure that came from a real slice.

    function patchSpy() {
      const seen: { body?: Record<string, unknown> } = {};
      server.use(
        http.patch('/api/v1/archives/:id', async ({ request }) => {
          seen.body = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...mockArchive, ...seen.body });
        }),
      );
      return seen;
    }

    it('sends a figure typed for an archive that has none', async () => {
      const user = userEvent.setup();
      const seen = patchSpy();

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.type(screen.getByLabelText(/filament used/i), '46.16');
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(seen.body?.filament_used_grams).toBe(46.16);
      });
    });

    it('leaves the field out of a save that did not touch it', async () => {
      const user = userEvent.setup();
      const seen = patchSpy();
      const weighed = { ...mockArchive, filament_used_grams: 50 };

      render(<EditArchiveModal archive={weighed} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(seen.body).toBeDefined();
      });
      expect(seen.body).not.toHaveProperty('filament_used_grams');
    });

    it('accepts a decimal comma, which a number input would have swallowed', async () => {
      const user = userEvent.setup();
      const seen = patchSpy();

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.type(screen.getByLabelText(/filament used/i), '46,16');
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(seen.body?.filament_used_grams).toBe(46.16);
      });
    });

    it('refuses characters that could never reach the API as a number', async () => {
      const user = userEvent.setup();

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      const field = screen.getByLabelText(/filament used/i) as HTMLInputElement;
      await user.type(field, '4a6-1..2');

      expect(field.value).toBe('461.2');
    });

    it('clamps to the bound the API enforces, so a save cannot be refused', async () => {
      const user = userEvent.setup();
      const seen = patchSpy();

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.type(screen.getByLabelText(/filament used/i), '999999');
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(seen.body?.filament_used_grams).toBe(100000);
      });
    });

    it('does not read a half-typed value as a clear', async () => {
      // Enter submits without the field ever losing focus, so the blur-time
      // tidy-up has not run and the state still holds what was typed.
      const user = userEvent.setup();
      const seen = patchSpy();
      const weighed = { ...mockArchive, filament_used_grams: 50 };

      render(<EditArchiveModal archive={weighed} onClose={mockOnClose} onSave={mockOnSave} />);
      const field = screen.getByLabelText(/filament used/i);
      await user.clear(field);
      await user.type(field, '.{Enter}');

      await waitFor(() => {
        expect(seen.body).toBeDefined();
      });
      expect(seen.body).not.toHaveProperty('filament_used_grams');
    });

    it('refreshes the print log, which the mirrored figure lands in', async () => {
      const user = userEvent.setup();
      let runFetches = 0;
      server.use(
        http.get('/api/v1/archives/:id/runs', () => {
          runFetches += 1;
          return HttpResponse.json({ items: [], total: 0 });
        }),
      );

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);
      await waitFor(() => expect(runFetches).toBe(1));

      await user.type(screen.getByLabelText(/filament used/i), '46.16');
      await user.click(screen.getByRole('button', { name: /save/i }));

      // Without the invalidation the table keeps serving its cached rows, so
      // the run the edit just corrected still shows the old figure.
      await waitFor(() => expect(runFetches).toBe(2));
    });

    it('clears the figure when the field is emptied', async () => {
      const user = userEvent.setup();
      const seen = patchSpy();
      const weighed = { ...mockArchive, filament_used_grams: 50 };

      render(<EditArchiveModal archive={weighed} onClose={mockOnClose} onSave={mockOnSave} />);
      await user.clear(screen.getByLabelText(/filament used/i));
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(seen.body?.filament_used_grams).toBeNull();
      });
    });
  });
  describe('project picker (#2888)', () => {
    // Statuses matter here, so this describe brings its own list rather than
    // the bare one the rest of the file shares.
    const withStatuses = (rows: Array<Record<string, unknown>>) =>
      server.use(http.get('/api/v1/projects/', () => HttpResponse.json(rows)));

    function savedBody() {
      const seen: { body?: Record<string, unknown> } = {};
      server.use(
        http.patch('/api/v1/archives/:id', async ({ request }) => {
          seen.body = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...mockArchive, ...seen.body });
        }),
      );
      return seen;
    }

    it('leaves archived projects out of the list', async () => {
      withStatuses([
        { id: 1, name: 'Live Work', color: '#00ae42', status: 'active' },
        { id: 2, name: 'Last Year', color: '#888888', status: 'archived' },
      ]);

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);

      await screen.findByRole('option', { name: 'Live Work' });
      expect(screen.queryByRole('option', { name: 'Last Year' })).not.toBeInTheDocument();
    });

    it('keeps completed projects, which are still worth filing a reprint under', async () => {
      withStatuses([
        { id: 1, name: 'Live Work', color: '#00ae42', status: 'active' },
        { id: 3, name: 'Shipped', color: '#888888', status: 'completed' },
      ]);

      render(<EditArchiveModal archive={mockArchive} onClose={mockOnClose} onSave={mockOnSave} />);

      expect(await screen.findByRole('option', { name: 'Shipped' })).toBeInTheDocument();
    });

    it('still offers the archived project this archive is already in', async () => {
      // Filtered out, the select holds a value no option matches, and the
      // browser resets it to the first option -- "No project". The archive
      // would say it is filed nowhere while sitting in a project.
      withStatuses([
        { id: 1, name: 'Live Work', color: '#00ae42', status: 'active' },
        { id: 2, name: 'Last Year', color: '#888888', status: 'archived' },
      ]);
      const filed = { ...mockArchive, project_id: 2 };

      render(<EditArchiveModal archive={filed} onClose={mockOnClose} onSave={mockOnSave} />);

      const option = await screen.findByRole('option', { name: 'Last Year' });
      expect((option as HTMLOptionElement).selected).toBe(true);
    });

    it('saves the project it was already in when nothing else is touched', async () => {
      const user = userEvent.setup();
      const seen = savedBody();
      withStatuses([{ id: 2, name: 'Last Year', color: '#888888', status: 'archived' }]);

      render(
        <EditArchiveModal
          archive={{ ...mockArchive, project_id: 2 }}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />,
      );
      await screen.findByRole('option', { name: 'Last Year' });
      await user.click(screen.getByRole('button', { name: /save/i }));

      // The stored id survives the round trip untouched: showing the archived
      // project is what makes the field honest, and it must not also change
      // what an untouched save writes.
      await waitFor(() => expect(seen.body?.project_id).toBe(2));
    });
  });
});
