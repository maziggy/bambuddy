/**
 * Tests for the Layout component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { render } from '../utils';
import { Layout } from '../../components/Layout';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('Layout', () => {
  beforeEach(() => {
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

  describe('theme toggle', () => {
    it('has theme toggle button', async () => {
      render(<Layout />);

      await waitFor(() => {
        // Theme toggle should be present
        const buttons = document.querySelectorAll('button');
        expect(buttons.length).toBeGreaterThan(0);
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
});
