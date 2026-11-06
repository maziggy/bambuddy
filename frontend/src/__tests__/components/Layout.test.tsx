/**
 * Tests for the Layout component.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import { Layout } from '../../components/Layout';
import { getAuthToken, setAuthToken } from '../../api/client';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, SIDEBAR_ORDER_KEY } from '../../utils/sidebarLayout';

describe('Layout', () => {
  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    vi.mocked(localStorage.removeItem).mockReset();
    vi.mocked(localStorage.clear).mockReset();
    localStorage.clear();
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json([
          { id: 1, name: 'X1 Carbon', model: 'X1C', enabled: true },
        ]);
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json({
          connected: true,
          state: 'IDLE',
        });
      }),
      http.get('/api/v1/version', () => {
        return HttpResponse.json({ version: '0.1.6', build: 'test' });
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json({
          check_updates: false,
          check_printer_firmware: false,
          auto_archive: true,
        });
      }),
      // What the sidebar actually gates on. Layout used to read these from
      // /settings/, which a non-admin cannot fetch (#3023).
      http.get('/api/v1/settings/ui-flags', () => {
        return HttpResponse.json({
          check_updates: false,
          billing_enabled: false,
          user_notifications_enabled: true,
          currency: 'EUR',
        });
      }),
      http.get('/api/v1/external-links/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/smart-plugs/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/support/debug-logging', () => {
        return HttpResponse.json({ enabled: false });
      }),
      http.get('/api/v1/queue/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/pending-uploads/count', () => {
        return HttpResponse.json({ count: 0 });
      }),
      http.get('/api/v1/updates/check', () => {
        return HttpResponse.json({ update_available: false });
      }),
      http.get('/api/v1/auth/status', () => {
        return HttpResponse.json({ auth_enabled: false, requires_setup: false });
      }),
      http.get('/api/v1/printers/developer-mode-warnings', () => {
        return HttpResponse.json([]);
      })
    );
  });

  describe('rendering', () => {
    it('renders the sidebar', async () => {
      render(<Layout />);

      // Layout renders as a flex container with sidebar
      await waitFor(() => {
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
      });
    });

    it('renders navigation links', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Navigation links should be present
        const links = document.querySelectorAll('a');
        expect(links.length).toBeGreaterThan(0);
      });
    });
  });

  describe('external link count', () => {
    // A connected app (Bambuddy Orders) reports its To do count for the link
    // pointing at it; the sidebar shows it like the queue's badge.
    const link = (id: number, name: string, badge_count: number | null, open_in_new_tab = false) => ({
      id,
      name,
      url: `http://app${id}.local:8090`,
      icon: 'Package',
      open_in_new_tab,
      custom_icon: null,
      sort_order: id,
      created_at: '2026-10-21T00:00:00Z',
      updated_at: '2026-10-21T00:00:00Z',
      badge_count,
    });

    it('shows the count an app reports, capped at 99+', async () => {
      server.use(
        http.get('/api/v1/external-links/', () =>
          HttpResponse.json([
            link(1, 'Orders', 7),
            link(2, 'Shop', 150, true),
            link(3, 'Wiki', 0),
            link(4, 'Docs', null),
          ]),
        ),
      );
      render(<Layout />);

      const orders = await screen.findByTestId('external-link-badge-1');
      expect(orders).toHaveTextContent('7');
      expect(screen.getByTestId('external-link-badge-2')).toHaveTextContent('99+');
      expect(screen.queryByTestId('external-link-badge-3')).not.toBeInTheDocument();
      expect(screen.queryByTestId('external-link-badge-4')).not.toBeInTheDocument();
    });
  });

  describe('navigation', () => {
    it('has navigation items', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Should have multiple navigation links
        const navLinks = document.querySelectorAll('a[href]');
        expect(navLinks.length).toBeGreaterThan(0);
      });
    });

    it('includes settings link', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Settings link should exist (route /settings)
        const settingsLink = document.querySelector('a[href="/settings"]');
        expect(settingsLink).toBeInTheDocument();
      });
    });

    it('hides system nav items stored in sidebar layout preferences', async () => {
      vi.mocked(localStorage.getItem).mockImplementation((key) => {
        if (key === SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY) return JSON.stringify(['printers']);
        return null;
      });

      render(<Layout />);

      await waitFor(() => {
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
        expect(sidebar?.querySelector('a[href="/inventory"]')).toBeInTheDocument();
      });

      expect(document.querySelector('aside a[href="/"]')).toBeNull();
    });

    it('applies admin default sidebar hidden state with the default order', async () => {
      const storage: Record<string, string> = {};
      vi.mocked(localStorage.getItem).mockImplementation((key) => storage[key] ?? null);
      vi.mocked(localStorage.setItem).mockImplementation((key, value) => {
        storage[key] = value;
      });
      server.use(
        http.get('/api/v1/settings/default-sidebar-order', () =>
          HttpResponse.json({
            default_sidebar_order: JSON.stringify({
              order: ['inventory', 'printers', 'settings'],
              hiddenSystemItemIds: ['printers'],
            }),
          }),
        ),
      );

      render(<Layout />);

      await waitFor(() => {
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
        expect(sidebar?.querySelector('a[href="/inventory"]')).toBeInTheDocument();
      });

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/"]')).toBeNull();
        expect(localStorage.setItem).toHaveBeenCalledWith(SIDEBAR_ORDER_KEY, JSON.stringify(['inventory', 'printers', 'settings']));
        expect(localStorage.setItem).toHaveBeenCalledWith(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, JSON.stringify(['printers']));
      });
    });
  });

  describe('finance nav item', () => {
    it('stays out of the sidebar while billing is off', async () => {
      // billing_enabled defaults to false and the Finance page has nothing to
      // show without it, so the entry must not be there at all.
      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/stats"]')).toBeInTheDocument();
      });
      expect(document.querySelector('aside a[href="/finance"]')).toBeNull();
    });

    it('appears between Statistics and Settings once billing is on', async () => {
      server.use(
        http.get('/api/v1/settings/ui-flags', () =>
          HttpResponse.json({
            check_updates: false,
            billing_enabled: true,
            user_notifications_enabled: true,
            currency: 'EUR',
          }),
        ),
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/finance"]')).toBeInTheDocument();
      });

      const sidebar = document.querySelector('aside');
      const hrefs = Array.from(sidebar?.querySelectorAll('a[href]') ?? []).map((a) => a.getAttribute('href'));
      expect(hrefs.indexOf('/finance')).toBeGreaterThan(hrefs.indexOf('/stats'));
      expect(hrefs.indexOf('/finance')).toBeLessThan(hrefs.indexOf('/settings'));
    });
  });

  describe('Sidebar gates survive a user who cannot read /settings (#3023)', () => {
    // Every gate below used to be fed by GET /settings, which requires
    // settings:read. A non-admin gets 403 there, so the value arrived
    // undefined and each gate silently took its fallback -- in opposite
    // directions, which is why only one of the two was ever reported.
    let priorToken: string | null = null;

    const asNonAdmin = (permissions: string[]) => {
      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 2,
            username: 'operator',
            role: 'user',
            is_active: true,
            is_admin: false,
            groups: [{ id: 2, name: 'Operators' }],
            permissions,
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
        // The 403 that started it. Layout must not need this call at all.
        http.get('/api/v1/settings/', () =>
          HttpResponse.json({ detail: 'Not enough permissions' }, { status: 403 }),
        ),
      );
      // localStorage is a no-op mock in setup.ts, so writing the key there
      // authenticates nobody. Set the client's token directly.
      priorToken = getAuthToken();
      setAuthToken('test-token', 'session');
    };

    afterEach(() => {
      setAuthToken(priorToken, 'session');
      priorToken = null;
    });

    it('shows Finance to a user with cost_centers:read_own and no settings:read', async () => {
      asNonAdmin(['cost_centers:read_own']);
      server.use(
        http.get('/api/v1/settings/ui-flags', () =>
          HttpResponse.json({ billing_enabled: true, user_notifications_enabled: true }),
        ),
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/finance"]')).toBeInTheDocument();
      });
    });

    it('still hides Finance from that user when billing is off', async () => {
      // Waits on Notifications appearing rather than on the sidebar existing.
      // Asserting absence the moment <aside> renders passes before the flags
      // query has even resolved, which makes the assertion prove nothing.
      asNonAdmin(['cost_centers:read_own', 'notifications:user_email']);
      server.use(
        http.get('/api/v1/auth/advanced-auth/status', () =>
          HttpResponse.json({ advanced_auth_enabled: true }),
        ),
        http.get('/api/v1/settings/ui-flags', () =>
          HttpResponse.json({ billing_enabled: false, user_notifications_enabled: true }),
        ),
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/notifications"]')).toBeInTheDocument();
      });
      expect(document.querySelector('aside a[href="/finance"]')).toBeNull();
    });

    it('hides Notifications from that user when user notifications are off', async () => {
      // The same 403, landing the other way up: this gate tests `=== false`,
      // which undefined never satisfies, so an administrator who switched user
      // notifications off still left the entry showing to the non-admins it
      // governs. Unreported, and invisible to an admin testing it.
      asNonAdmin(['notifications:user_email', 'cost_centers:read_own']);
      server.use(
        http.get('/api/v1/auth/advanced-auth/status', () =>
          HttpResponse.json({ advanced_auth_enabled: true }),
        ),
        http.get('/api/v1/settings/ui-flags', () =>
          HttpResponse.json({ billing_enabled: true, user_notifications_enabled: false }),
        ),
      );

      render(<Layout />);

      // Finance appearing is the proof that the flags arrived; only then does
      // the absence of Notifications mean anything.
      await waitFor(() => {
        expect(document.querySelector('aside a[href="/finance"]')).toBeInTheDocument();
      });
      expect(document.querySelector('aside a[href="/notifications"]')).toBeNull();
    });

    it('shows Notifications to that user when they are on', async () => {
      asNonAdmin(['notifications:user_email']);
      server.use(
        http.get('/api/v1/auth/advanced-auth/status', () =>
          HttpResponse.json({ advanced_auth_enabled: true }),
        ),
        http.get('/api/v1/settings/ui-flags', () =>
          HttpResponse.json({ billing_enabled: false, user_notifications_enabled: true }),
        ),
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside a[href="/notifications"]')).toBeInTheDocument();
      });
    });
  });

  describe('version display', () => {
    it('shows version info', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Version info is displayed in sidebar
        expect(document.body).toBeInTheDocument();
      });
    });
  });

  describe('camera launcher (#2737)', () => {
    const originalMatchMedia = window.matchMedia;
    const originalWidth = window.innerWidth;
    /** Below 1144px the sidebar is a drawer and the compact header shows. */
    function compactViewport() {
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
      Object.defineProperty(window, 'matchMedia', {
        writable: true,
        configurable: true,
        value: (query: string) => ({ ...originalMatchMedia(query), matches: /max-width/.test(query) }),
      });
    }

    beforeEach(() => {
      server.use(
        http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
        http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
      );
    });

    afterEach(() => {
      Object.defineProperty(window, 'matchMedia', { writable: true, configurable: true, value: originalMatchMedia });
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: originalWidth });
    });

    it('is on the Printers row of the sidebar, not in its footer', async () => {
      render(<Layout />);

      await waitFor(() => expect(document.querySelector('header')).toBeNull());
      const aside = document.querySelector('aside')!;
      const button = within(aside).getByRole('button', { name: 'Cameras' });
      expect(button.closest('li')?.querySelector('a[href="/"]')).not.toBeNull();
      expect(within(aside).getAllByRole('button', { name: 'Cameras' })).toHaveLength(1);
    });

    it('opens the camera list with the C key', async () => {
      render(<Layout />);
      await waitFor(() => expect(document.querySelector('aside')).not.toBeNull());

      fireEvent.keyDown(document, { key: 'c' });
      expect(await screen.findByRole('button', { name: /X1 Carbon/ })).toBeInTheDocument();

      fireEvent.keyDown(document, { key: 'c' });
      await waitFor(() => expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull());
    });

    it('leaves the C key alone while typing', async () => {
      render(
        <>
          <input aria-label="search" />
          <Layout />
        </>,
      );
      await waitFor(() => expect(document.querySelector('aside')).not.toBeNull());

      fireEvent.keyDown(screen.getByRole('textbox', { name: 'search' }), { key: 'c' });

      expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull();
    });

    it('opens the header\'s list with the C key in the compact layout', async () => {
      compactViewport();
      render(<Layout />);
      await waitFor(() => expect(document.querySelector('header')).not.toBeNull());

      fireEvent.keyDown(document, { key: 'c' });

      expect(await screen.findByRole('region', { name: 'Cameras' })).toBeInTheDocument();
      // One list, the header's, not also the one in the closed drawer.
      expect(screen.getAllByRole('region', { name: 'Cameras' })).toHaveLength(1);
      expect(within(document.querySelector('header')!).getByRole('button', { name: 'Cameras' })).toHaveAttribute(
        'aria-expanded',
        'true',
      );
    });

    it('is in the compact header, beside the bug report', async () => {
      compactViewport();
      render(<Layout />);

      await waitFor(() => expect(document.querySelector('header')).not.toBeNull());
      expect(within(document.querySelector('header')!).getByRole('button', { name: 'Cameras' })).toBeInTheDocument();
    });

    it('opens a camera overlay on a page other than Printers', async () => {
      compactViewport();
      render(<Layout />);
      await waitFor(() => expect(document.querySelector('header')).not.toBeNull());

      fireEvent.click(within(document.querySelector('header')!).getByRole('button', { name: 'Cameras' }));
      fireEvent.click(await screen.findByRole('button', { name: /X1 Carbon/ }));

      expect(await screen.findByTitle('Refresh stream')).toBeInTheDocument();
    });
  });

  describe('theme toggle', () => {
    it('has theme toggle button', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Theme toggle should be present
        const buttons = document.querySelectorAll('button');
        expect(buttons.length).toBeGreaterThan(0);
      });
    });

    it('cycles through dark → light → system → dark', async () => {
      localStorage.setItem('theme-mode', 'dark');
      render(<Layout />);

      await waitFor(() => {
        // In dark mode, title should say "Switch to light mode"
        const btn = document.querySelector('button[title="Switch to light mode"]');
        expect(btn).toBeInTheDocument();
      });

      // Click to go from dark → light
      const lightBtn = document.querySelector('button[title="Switch to light mode"]')!;
      lightBtn.click();

      await waitFor(() => {
        // In light mode, title should say "Switch to system mode"
        const btn = document.querySelector('button[title="Switch to system mode"]');
        expect(btn).toBeInTheDocument();
      });

      // Click to go from light → system
      const systemBtn = document.querySelector('button[title="Switch to system mode"]')!;
      systemBtn.click();

      await waitFor(() => {
        // In system mode, title should say "Switch to dark mode"
        const btn = document.querySelector('button[title="Switch to dark mode"]');
        expect(btn).toBeInTheDocument();
      });

      // Click to go from system → dark
      const darkBtn = document.querySelector('button[title="Switch to dark mode"]')!;
      darkBtn.click();

      await waitFor(() => {
        // Back to dark mode
        const btn = document.querySelector('button[title="Switch to light mode"]');
        expect(btn).toBeInTheDocument();
      });
    });
  });

  describe('plate detection alert modal', () => {
    it('shows modal when plate-not-empty event is dispatched', async () => {
      render(<Layout />);

      // Dispatch the plate-not-empty event
      window.dispatchEvent(
        new CustomEvent('plate-not-empty', {
          detail: {
            printer_id: 1,
            printer_name: 'Test Printer',
            message: 'Objects detected on build plate',
          },
        })
      );

      await waitFor(() => {
        // Modal should appear with "Print Paused!" text
        expect(document.body.textContent).toContain('Print Paused!');
        expect(document.body.textContent).toContain('Test Printer');
      });
    });

    it('warns when a plate check could not run, naming why', async () => {
      render(<Layout />);

      window.dispatchEvent(
        new CustomEvent('plate-check-failed', {
          detail: { printer_id: 1, printer_name: 'Test Printer', reason: 'no_camera_frame' },
        })
      );

      await waitFor(() => {
        expect(document.body.textContent).toContain(
          'Plate check could not run on Test Printer (no camera image). The print continues.'
        );
      });
    });

    it('closes modal when I Understand button is clicked', async () => {
      render(<Layout />);

      // Dispatch the plate-not-empty event
      window.dispatchEvent(
        new CustomEvent('plate-not-empty', {
          detail: {
            printer_id: 1,
            printer_name: 'Test Printer',
            message: 'Objects detected on build plate',
          },
        })
      );

      await waitFor(() => {
        expect(document.body.textContent).toContain('Print Paused!');
      });

      // Click the "I Understand" button
      const button = document.querySelector('button');
      if (button && button.textContent?.includes('I Understand')) {
        button.click();
      }

      // Find and click the "I Understand" button by searching all buttons
      const buttons = document.querySelectorAll('button');
      buttons.forEach((btn) => {
        if (btn.textContent?.includes('I Understand')) {
          btn.click();
        }
      });

      await waitFor(() => {
        // Modal should be closed
        expect(document.body.textContent).not.toContain('Print Paused!');
      });
    });
  });

  describe('developer mode warning banner', () => {
    it('shows warning banner when printers lack developer mode', async () => {
      server.use(
        http.get('/api/v1/printers/developer-mode-warnings', () => {
          return HttpResponse.json([
            { printer_id: 1, name: 'X1 Carbon' },
          ]);
        })
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.body.textContent).toContain('Developer LAN mode is not enabled on');
        expect(document.body.textContent).toContain('X1 Carbon');
      });
    });

    it('shows multiple printer names in warning banner', async () => {
      server.use(
        http.get('/api/v1/printers/developer-mode-warnings', () => {
          return HttpResponse.json([
            { printer_id: 1, name: 'X1 Carbon' },
            { printer_id: 2, name: 'P1S' },
          ]);
        })
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.body.textContent).toContain('X1 Carbon');
        expect(document.body.textContent).toContain('P1S');
      });
    });

    it('hides warning banner when no printers lack developer mode', async () => {
      // Default handler returns empty array
      render(<Layout />);

      await waitFor(() => {
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
      });

      // Banner should not be present
      expect(document.body.textContent).not.toContain('Developer LAN mode is not enabled on');
    });

    it('shows how to enable link in warning banner', async () => {
      server.use(
        http.get('/api/v1/printers/developer-mode-warnings', () => {
          return HttpResponse.json([
            { printer_id: 1, name: 'X1 Carbon' },
          ]);
        })
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.body.textContent).toContain('How to enable');
        const link = document.querySelector('a[href*="enable-developer-mode"]');
        expect(link).toBeInTheDocument();
      });
    });
  });

  describe('update banner suppression for HA addon', () => {
    // HA Supervisor surfaces its own update notification natively in the HA
    // UI, so the in-app banner would be duplicate noise that links to a page
    // that just says "update via HA". Suppress it for HA addon deployments.
    it('hides the update-available banner when running as an HA addon', async () => {
      server.use(
        http.get('/api/v1/updates/check', () => {
          return HttpResponse.json({
            update_available: true,
            current_version: '0.2.4',
            latest_version: '0.2.5',
            is_docker: true,
            is_ha_addon: true,
            update_method: 'ha_addon',
          });
        }),
      );

      render(<Layout />);

      await waitFor(() => {
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
      });

      expect(document.body.textContent).not.toContain('Update available');
    });

    it('still shows the update-available banner for plain Docker deployments', async () => {
      server.use(
        http.get('/api/v1/updates/check', () => {
          return HttpResponse.json({
            update_available: true,
            current_version: '0.2.4',
            latest_version: '0.2.5',
            is_docker: true,
            is_ha_addon: false,
            update_method: 'docker',
          });
        }),
      );

      render(<Layout />);

      await waitFor(() => {
        expect(document.body.textContent).toContain('0.2.5');
      });
    });
  });

  describe('MakerWorld sidebar permission gate (#1175)', () => {
    // The MakerWorld sidebar entry was visible to every authenticated user
    // regardless of group permissions because Layout's `navPermissions` map
    // had no entry for `makerworld`. Backend routes already gated on
    // `makerworld:view`, so users without the permission saw the entry,
    // clicked, and got 403'd by every API call inside the page. The fix
    // adds `makerworld: 'makerworld:view'` to the map so the entry is
    // hidden when the permission is absent — same shape as every other
    // sidebar entry.
    const enableAuthWithUser = (permissions: string[]) => {
      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 1,
            username: 'tester',
            role: 'user',
            is_active: true,
            is_admin: false,
            groups: [{ id: 2, name: 'Standard Users' }],
            permissions,
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
      );
      // AuthProvider needs a token in localStorage to fetch /auth/me; the
      // value isn't validated by the mocked server.
      window.localStorage.setItem('auth_token', 'test-token');
    };

    const findMakerWorldNavLink = () => {
      // Sidebar nav links use react-router's `to` prop, which renders as a
      // plain `<a href="/model-sources">`. Match on the href so the test isn't
      // coupled to whatever locale string is rendered. The page was
      // MakerWorld-only until Manyfold joined it as a second tab (#1471).
      return document.querySelector('aside a[href="/model-sources"]');
    };

    it('hides the Model Sources nav entry when the user has neither source permission', async () => {
      // Standard user without the MakerWorld permission. Every other
      // permission they hold (library:read, etc.) is irrelevant here — the
      // gate is per-entry and the MakerWorld entry must not render.
      enableAuthWithUser(['library:read', 'archives:read', 'queue:read']);

      render(<Layout />);

      await waitFor(() => {
        // Wait for the auth resolution + sidebar render. Some other nav
        // entry (Files / Archives) confirms the sidebar finished mounting.
        const sidebar = document.querySelector('aside');
        expect(sidebar).toBeInTheDocument();
        expect(sidebar?.querySelector('a[href="/files"]')).toBeInTheDocument();
      });

      await waitFor(() => expect(findMakerWorldNavLink()).toBeNull());
    });

    it('shows the MakerWorld nav entry when the user has makerworld:view', async () => {
      enableAuthWithUser([
        'library:read',
        'archives:read',
        'queue:read',
        'makerworld:view',
      ]);

      render(<Layout />);

      await waitFor(() => {
        expect(findMakerWorldNavLink()).toBeInTheDocument();
      });
    });

    it('shows the Model Sources nav entry with manyfold:view alone (#1471)', async () => {
      enableAuthWithUser(['library:read', 'manyfold:view']);

      render(<Layout />);

      await waitFor(() => {
        expect(findMakerWorldNavLink()).toBeInTheDocument();
      });
    });
  });

  describe('Sidebar gate accepts granular read tiers (#1755)', () => {
    // Default Operators group is seeded with `*:read_own` only — never the
    // legacy `*:read`. Previously the sidebar gate checked the legacy alone,
    // so Archives / Queue / Files were hidden from every non-admin even
    // though the underlying API endpoints accepted their requests. These
    // tests pin that the gate accepts ANY of the three tiers (legacy /
    // _own / _all) for the three resources that ship granular variants.
    const enableAuthWithUser = (permissions: string[]) => {
      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 1,
            username: 'tester',
            role: 'user',
            is_active: true,
            is_admin: false,
            groups: [{ id: 2, name: 'Operators' }],
            permissions,
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
      );
      window.localStorage.setItem('auth_token', 'test-token');
    };

    const sidebarLink = (href: string) =>
      document.querySelector(`aside a[href="${href}"]`);

    it('shows Files in the sidebar when the user only has library:read_own', async () => {
      enableAuthWithUser(['library:read_own']);

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside')).toBeInTheDocument();
        expect(sidebarLink('/files')).toBeInTheDocument();
      });
    });

    it('shows Files in the sidebar when the user only has library:read_all', async () => {
      enableAuthWithUser(['library:read_all']);

      render(<Layout />);

      await waitFor(() => {
        expect(sidebarLink('/files')).toBeInTheDocument();
      });
    });

    it('shows Archives in the sidebar when the user only has archives:read_own', async () => {
      enableAuthWithUser(['archives:read_own']);

      render(<Layout />);

      await waitFor(() => {
        expect(sidebarLink('/archives')).toBeInTheDocument();
      });
    });

    it('shows Queue in the sidebar when the user only has queue:read_own', async () => {
      enableAuthWithUser(['queue:read_own']);

      render(<Layout />);

      await waitFor(() => {
        expect(sidebarLink('/queue')).toBeInTheDocument();
      });
    });

    it('still hides Files when the user has none of the three read tiers', async () => {
      enableAuthWithUser(['printers:read']);

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside')).toBeInTheDocument();
      });

      expect(sidebarLink('/files')).toBeNull();
      expect(sidebarLink('/archives')).toBeNull();
      expect(sidebarLink('/queue')).toBeNull();
    });
  });
  describe('outcome confirmation deep link (#1898)', () => {
    // The URL a Pushover / Bark notification opens in a brand new tab. Layout
    // is the PARENT of the page that renders at /archives, and React flushes a
    // child's effects before its parent's — so while the page owned this deep
    // link it dispatched `print-confirm-request` before Layout had added the
    // listener, and then stripped the parameter, leaving nothing to recover.
    // These tests pin the read where the dialog state lives.
    const archive = {
      id: 42,
      printer_id: 1,
      filename: 'bracket.gcode.3mf',
      print_name: 'Bracket',
      status: 'completed',
      photos: null,
      user_verdict: null,
      user_verdict_source: null,
      confirm_requested: true,
    };

    beforeEach(() => {
      server.use(
        http.get('/api/v1/archives/42', () => HttpResponse.json(archive))
      );
    });

    afterEach(() => {
      window.history.replaceState({}, '', '/');
    });

    it('opens the dialog on a cold load, with no page mounted to relay the event', async () => {
      window.history.replaceState({}, '', '/archives?confirm=42');

      render(<Layout />);

      expect(await screen.findByTestId('confirm-outcome-dialog')).toBeInTheDocument();
    });

    it('opens the dialog on the plate the link names', async () => {
      // Plates of one archive each have their own question (#1898).
      let requestedRun: string | null = null;
      server.use(
        http.get('/api/v1/archives/42', ({ request }) => {
          requestedRun = new URL(request.url).searchParams.get('run_id');
          return HttpResponse.json({ ...archive, run_id: 7 });
        }),
      );
      window.history.replaceState({}, '', '/archives?confirm=42&run=7&status=completed');

      render(<Layout />);

      await screen.findByTestId('confirm-outcome-dialog');
      await waitFor(() => {
        expect(requestedRun).toBe('7');
      });
      await waitFor(() => {
        expect(window.location.search).toBe('?status=completed');
      });
    });

    it('strips the parameter but keeps the rest of the query', async () => {
      window.history.replaceState({}, '', '/archives?confirm=42&status=completed');

      render(<Layout />);

      await screen.findByTestId('confirm-outcome-dialog');
      await waitFor(() => {
        expect(window.location.search).toBe('?status=completed');
      });
      expect(window.location.pathname).toBe('/archives');
    });

    it('ignores a confirm parameter that is not an archive id', async () => {
      window.history.replaceState({}, '', '/archives?confirm=not-an-id');

      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('confirm-outcome-dialog')).toBeNull();
      // Nothing was consumed, so nothing is rewritten either.
      expect(window.location.search).toBe('?confirm=not-an-id');
    });

    // Round 4: the dialog's only outcome for a user who may not record a
    // verdict is a 403 from the PATCH, so it is gated like plate-not-empty —
    // on archives:update_all / archives:update_own, the names the June
    // permission migration left in the default groups.
    describe('without permission to record a verdict', () => {
      const withPermissions = (permissions: string[]) => {
        server.use(
          http.get('/api/v1/auth/status', () =>
            HttpResponse.json({ auth_enabled: true, requires_setup: false }),
          ),
          http.get('/api/v1/auth/me', () =>
            HttpResponse.json({
              id: 1,
              username: 'tester',
              role: 'user',
              is_active: true,
              is_admin: false,
              groups: [{ id: 2, name: 'Standard Users' }],
              permissions,
              created_at: '2026-01-01T00:00:00Z',
            }),
          ),
        );
        // localStorage is a mock in this suite, so the token goes through the
        // client the way the app sets it — AuthProvider will not fetch
        // /auth/me without one, and the permissions would stay empty.
        setAuthToken('test-token');
      };

      afterEach(() => {
        setAuthToken(null);
      });

      it('ignores the event', async () => {
        withPermissions(['archives:read_all']);
        render(<Layout />);

        await waitFor(() => {
          expect(document.querySelector('aside')).toBeInTheDocument();
        });
        window.dispatchEvent(
          new CustomEvent('print-confirm-request', { detail: { archive_id: 42 } })
        );

        await waitFor(() => {
          expect(screen.queryByTestId('confirm-outcome-dialog')).toBeNull();
        });
      });

      it('consumes the deep link without opening anything', async () => {
        withPermissions(['archives:read_all']);
        window.history.replaceState({}, '', '/archives?confirm=42');

        render(<Layout />);

        // The parameter still goes: a link that can open nothing should not
        // survive a reload either.
        await waitFor(() => {
          expect(window.location.search).toBe('');
        });
        expect(screen.queryByTestId('confirm-outcome-dialog')).toBeNull();
      });

      it('opens for a user who may update their own archives', async () => {
        withPermissions(['archives:read_own', 'archives:update_own']);
        render(<Layout />);

        await waitFor(() => {
          expect(document.querySelector('aside')).toBeInTheDocument();
        });
        window.dispatchEvent(
          new CustomEvent('print-confirm-request', { detail: { archive_id: 42 } })
        );

        expect(await screen.findByTestId('confirm-outcome-dialog')).toBeInTheDocument();
      });
    });

    it('still opens from the WebSocket event, which carries no URL', async () => {
      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside')).toBeInTheDocument();
      });
      window.dispatchEvent(
        new CustomEvent('print-confirm-request', { detail: { archive_id: 42 } })
      );

      expect(await screen.findByTestId('confirm-outcome-dialog')).toBeInTheDocument();
    });

    it('opens from the WebSocket event on the plate it names', async () => {
      let requestedRun: string | null = null;
      server.use(
        http.get('/api/v1/archives/42', ({ request }) => {
          requestedRun = new URL(request.url).searchParams.get('run_id');
          return HttpResponse.json({ ...archive, run_id: 7 });
        }),
      );
      render(<Layout />);

      await waitFor(() => {
        expect(document.querySelector('aside')).toBeInTheDocument();
      });
      window.dispatchEvent(
        new CustomEvent('print-confirm-request', { detail: { archive_id: 42, run_id: 7 } })
      );

      await screen.findByTestId('confirm-outcome-dialog');
      await waitFor(() => {
        expect(requestedRun).toBe('7');
      });
    });
  });
});
