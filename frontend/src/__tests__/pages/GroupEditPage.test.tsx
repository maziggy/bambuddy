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
          <ThemeProvider>
            <ToastProvider>
              <AuthProvider>
                <MemoryRouter initialEntries={['/groups/2/edit']}>
                  <Routes>
                    <Route path="/groups/:id/edit" element={<GroupEditPage />} />
                    <Route path="/settings" element={<div>Settings</div>} />
                  </Routes>
                </MemoryRouter>
              </AuthProvider>
            </ToastProvider>
          </ThemeProvider>
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
