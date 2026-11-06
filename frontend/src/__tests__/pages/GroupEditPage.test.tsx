/**
 * Tests for the GroupEditPage component.
 *
 * Covers create mode, edit mode, permission search/filtering,
 * select all / clear all, and category-level toggles.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { GroupEditPage } from '../../pages/GroupEditPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPermissions = {
  categories: [
    {
      name: 'Printers',
      permissions: [
        { value: 'printers:read', label: 'Read Printers' },
        { value: 'printers:control', label: 'Control Printers' },
        { value: 'printers:clear_plate', label: 'Clear Plate' },
      ],
    },
    {
      name: 'Archives',
      permissions: [
        { value: 'archives:read', label: 'Read Archives' },
        { value: 'archives:create', label: 'Create Archives' },
      ],
    },
  ],
  all_permissions: [
    'printers:read',
    'printers:control',
    'printers:clear_plate',
    'archives:read',
    'archives:create',
  ],
};

const mockGroup = {
  id: 2,
  name: 'Operators',
  description: 'Control printers and manage content',
  permissions: ['printers:read', 'printers:control', 'printers:clear_plate'],
  is_system: true,
  user_count: 3,
  users: [{ id: 1, username: 'admin', is_active: true }],
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

describe('GroupEditPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/groups/permissions', () => {
        return HttpResponse.json(mockPermissions);
      }),
      http.get('/api/v1/groups/:id', () => {
        return HttpResponse.json(mockGroup);
      }),
      http.post('/api/v1/groups/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: 10,
          ...body,
          is_system: false,
          user_count: 0,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      }),
      http.patch('/api/v1/groups/:id', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...mockGroup,
          ...body,
        });
      })
    );
  });

  describe('create mode', () => {
    it('renders create title when no id param', async () => {
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Create Group')).toBeInTheDocument();
      });
    });

    it('shows permission categories', async () => {
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Printers')).toBeInTheDocument();
      });
      expect(screen.getByText('Archives')).toBeInTheDocument();
    });

    it('shows individual permissions', async () => {
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Read Printers')).toBeInTheDocument();
      });
      expect(screen.getByText('Control Printers')).toBeInTheDocument();
      expect(screen.getByText('Clear Plate')).toBeInTheDocument();
      expect(screen.getByText('Read Archives')).toBeInTheDocument();
      expect(screen.getByText('Create Archives')).toBeInTheDocument();
    });

    it('shows 0 selected initially', async () => {
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText(/0 selected/)).toBeInTheDocument();
      });
    });

    it('shows save and cancel buttons', async () => {
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Save')).toBeInTheDocument();
      });
      expect(screen.getByText('Cancel')).toBeInTheDocument();
    });
  });

  describe('permission interactions', () => {
    it('toggles individual permission on click', async () => {
      const user = userEvent.setup();
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Read Printers')).toBeInTheDocument();
      });

      const checkbox = screen.getByText('Read Printers').closest('label')!.querySelector('input')!;
      await user.click(checkbox);

      await waitFor(() => {
        expect(screen.getByText(/1 selected/)).toBeInTheDocument();
      });
    });

    it('select all selects all permissions', async () => {
      const user = userEvent.setup();
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select All'));

      await waitFor(() => {
        expect(screen.getByText(/5 selected/)).toBeInTheDocument();
      });
    });

    it('clear all deselects all permissions', async () => {
      const user = userEvent.setup();
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Select All')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Select All'));
      await waitFor(() => {
        expect(screen.getByText(/5 selected/)).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clear All'));
      await waitFor(() => {
        expect(screen.getByText(/0 selected/)).toBeInTheDocument();
      });
    });

    it('filters permissions by search', async () => {
      const user = userEvent.setup();
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Read Printers')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText('Search permissions...');
      await user.type(searchInput, 'Clear');

      await waitFor(() => {
        expect(screen.getByText('Clear Plate')).toBeInTheDocument();
        expect(screen.queryByText('Read Printers')).not.toBeInTheDocument();
        expect(screen.queryByText('Archives')).not.toBeInTheDocument();
      });
    });

    it('shows no results message for empty search', async () => {
      const user = userEvent.setup();
      render(<GroupEditPage />);

      await waitFor(() => {
        expect(screen.getByText('Read Printers')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText('Search permissions...');
      await user.type(searchInput, 'zzzznonexistent');

      await waitFor(() => {
        expect(screen.getByText('No permissions match your search')).toBeInTheDocument();
      });
    });
  });

  describe('cache invalidation after save (#1083)', () => {
    it('primes the single-group detail cache with the update response body', async () => {
      // Regression for #1083: before the fix, onSuccess only invalidated the
      // ['groups'] list query. The ['group', id] detail cache stayed stale
      // under the global 60s staleTime, so reopening the editor showed the
      // pre-update snapshot. The fix invalidates the detail key AND primes the
      // cache with the server response so a re-mount sees fresh data.
      const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
      const { MemoryRouter, Routes, Route } = await import('react-router-dom');
      const { AuthProvider } = await import('../../contexts/AuthContext');
      const { ToastProvider } = await import('../../contexts/ToastContext');
      const { ThemeProvider } = await import('../../contexts/ThemeContext');
      const { render: rtlRender } = await import('@testing-library/react');

      const queryClient = new QueryClient({
        defaultOptions: { queries: { staleTime: 60_000, retry: false } },
      });
      const user = userEvent.setup();

      const wrapper = (
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>
                <MemoryRouter initialEntries={['/groups/2/edit']}>
                  <Routes>
                    <Route path="/groups/:id/edit" element={<GroupEditPage />} />
                    <Route path="/settings" element={<div>Settings</div>} />
                  </Routes>
                </MemoryRouter>
              </ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </QueryClientProvider>
      );
      rtlRender(wrapper);

      // Wait for the group to load
      await waitFor(() => {
        expect(screen.getByDisplayValue('Operators')).toBeInTheDocument();
      });

      // Change permissions then save
      await waitFor(() => {
        expect(screen.getByText('Read Archives')).toBeInTheDocument();
      });
      const archivesCheckbox = screen.getByText('Read Archives').closest('label')!.querySelector('input')!;
      await user.click(archivesCheckbox);

      await user.click(screen.getByText('Save'));

      // Wait for navigation (redirect to /settings)
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });

      // After save, the detail cache must have been primed with the server
      // response (mocked PATCH returns mockGroup + body). The next mount
      // should read the cached body, not the stale pre-update payload.
      const cached = queryClient.getQueryData(['group', '2']) as { permissions: string[] } | undefined;
      expect(cached).toBeDefined();
      expect(cached!.permissions).toContain('archives:read');
    });
  });
});

describe('GroupEditPage printer access (#1727)', () => {
  // Printer access is edited on its own page; the editor only summarises it
  let posted: Record<string, unknown> | null;
  let patched: Record<string, unknown> | null;

  const setup = (group: Record<string, unknown>) => {
    posted = null;
    patched = null;
    server.use(
      http.get('/api/v1/groups/permissions', () => HttpResponse.json(mockPermissions)),
      http.get('/api/v1/groups/:id', () => HttpResponse.json(group)),
      http.post('/api/v1/groups/', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...group, ...posted, id: 10 });
      }),
      http.patch('/api/v1/groups/:id', async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...group, ...patched });
      })
    );
  };

  const renderEdit = async () => {
    const { MemoryRouter, Routes, Route } = await import('react-router-dom');
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
    const { AuthProvider } = await import('../../contexts/AuthContext');
    const { ToastProvider } = await import('../../contexts/ToastContext');
    const { ThemeProvider } = await import('../../contexts/ThemeContext');
    const { render: rtlRender } = await import('@testing-library/react');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              <MemoryRouter initialEntries={['/groups/2/edit']}>
                <Routes>
                  <Route path="/groups/:id/edit" element={<GroupEditPage />} />
                  <Route path="/settings" element={<div>Settings</div>} />
                </Routes>
              </MemoryRouter>
            </ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </QueryClientProvider>
    );
  };

  it('summarises a limited group and links to its printer access', async () => {
    setup({ ...mockGroup, restrict_printers: true, printer_ids: [2, 3], locations: ['Lab A'] });
    await renderEdit();

    await waitFor(() => expect(screen.getByDisplayValue('Operators')).toBeInTheDocument());
    expect(screen.getByText('Limited: 2 printers picked, 1 locations')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage printer access' })).toHaveAttribute(
      'href',
      '/settings?tab=users&sub=printer-access&group=2'
    );
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });

  it('never sends printer access, so saving here cannot undo the access page', async () => {
    setup({ ...mockGroup, restrict_printers: true, printer_ids: [2], locations: ['Lab A'] });
    const user = userEvent.setup();
    await renderEdit();

    await waitFor(() => expect(screen.getByDisplayValue('Operators')).toBeInTheDocument());
    await user.click(screen.getByText('Save'));

    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).not.toHaveProperty('restrict_printers');
    expect(patched).not.toHaveProperty('printer_ids');
    expect(patched).not.toHaveProperty('locations');
    // System group: unchanged permissions aren't resent either
    expect(patched).not.toHaveProperty('permissions');
  });

  it('points a new group to the access page', async () => {
    setup({});
    const user = userEvent.setup();
    render(<GroupEditPage />);

    await waitFor(() => expect(screen.getByText('Printer access')).toBeInTheDocument());
    expect(screen.getByText(/Once the group is created/)).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(/group name/i), 'Team A');
    await user.click(screen.getByText('Save'));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).not.toHaveProperty('restrict_printers');
  });

  it('explains that Administrators always see every printer', async () => {
    setup({ ...mockGroup, id: 1, name: 'Administrators', restrict_printers: false, printer_ids: [], locations: [] });
    await renderEdit();

    await waitFor(() => expect(screen.getByDisplayValue('Administrators')).toBeInTheDocument());
    expect(screen.getByText('Administrators always see every printer.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Manage printer access' })).not.toBeInTheDocument();
  });
});
