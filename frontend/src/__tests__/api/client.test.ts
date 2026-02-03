/**
 * Tests for the API client auth token handling.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { setAuthToken, getAuthToken, api } from '../../api/client';

// Mock localStorage
const localStorageMock = {
  store: {} as Record<string, string>,
  getItem: vi.fn((key: string) => localStorageMock.store[key] || null),
  setItem: vi.fn((key: string, value: string) => {
    localStorageMock.store[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete localStorageMock.store[key];
  }),
  clear: vi.fn(() => {
    localStorageMock.store = {};
  }),
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

// Create MSW server
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  localStorageMock.clear();
  setAuthToken(null);
});
afterAll(() => server.close());

describe('Auth Token Management', () => {
  it('setAuthToken stores token in localStorage', () => {
    setAuthToken('test-token-123');
    expect(localStorageMock.setItem).toHaveBeenCalledWith('auth_token', 'test-token-123');
    expect(getAuthToken()).toBe('test-token-123');
  });

  it('setAuthToken removes token from localStorage when null', () => {
    setAuthToken('test-token-123');
    setAuthToken(null);
    expect(localStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
    expect(getAuthToken()).toBeNull();
  });
});

describe('API Client Auth Header', () => {
  it('includes Authorization header when token is set', async () => {
    let capturedHeaders: Headers | null = null;

    server.use(
      http.get('/api/v1/settings/spoolman', ({ request }) => {
        capturedHeaders = request.headers;
        return HttpResponse.json({
          spoolman_enabled: 'false',
          spoolman_url: '',
          spoolman_sync_mode: 'auto',
        });
      })
    );

    setAuthToken('test-jwt-token');
    await api.getSpoolmanSettings();

    expect(capturedHeaders).not.toBeNull();
    expect(capturedHeaders!.get('Authorization')).toBe('Bearer test-jwt-token');
  });

  it('does not include Authorization header when token is not set', async () => {
    let capturedHeaders: Headers | null = null;

    server.use(
      http.get('/api/v1/settings/spoolman', ({ request }) => {
        capturedHeaders = request.headers;
        return HttpResponse.json({
          spoolman_enabled: 'false',
          spoolman_url: '',
          spoolman_sync_mode: 'auto',
        });
      })
    );

    setAuthToken(null);
    await api.getSpoolmanSettings();

    expect(capturedHeaders).not.toBeNull();
    expect(capturedHeaders!.get('Authorization')).toBeNull();
  });

  it('clears token on 401 Unauthorized response', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () => {
        return HttpResponse.json(
          { detail: 'Not authenticated' },
          { status: 401 }
        );
      })
    );

    setAuthToken('expired-token');
    expect(getAuthToken()).toBe('expired-token');

    try {
      await api.getSpoolmanSettings();
    } catch {
      // Expected to throw
    }

    expect(getAuthToken()).toBeNull();
    expect(localStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
  });
});

describe('FormData requests include auth header', () => {
  it('importProjectFile includes Authorization header', async () => {
    // Mock fetch directly for FormData requests (MSW can be flaky with multipart in some environments)
    const originalFetch = global.fetch;
    let capturedHeaders: Headers | null = null;

    global.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/projects/import/file')) {
        capturedHeaders = new Headers(init?.headers);
        return Promise.resolve(new Response(JSON.stringify({
          id: 1,
          name: 'Test Project',
          description: '',
          total_cost: 0,
          total_print_time_seconds: 0,
          total_prints: 0,
          total_quantity: 0,
          status: 'active',
          due_date: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          archives: [],
          bom_items: [],
        }), { status: 200 }));
      }
      return originalFetch(url, init);
    });

    try {
      setAuthToken('test-token');
      const file = new File(['test content'], 'test.zip', { type: 'application/zip' });
      await api.importProjectFile(file);

      expect(capturedHeaders).not.toBeNull();
      expect(capturedHeaders!.get('Authorization')).toBe('Bearer test-token');
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('exportProjectZip includes Authorization header', async () => {
    let capturedHeaders: Headers | null = null;

    server.use(
      http.get('/api/v1/projects/:projectId/export', ({ request }) => {
        capturedHeaders = request.headers;
        const zipContent = new Uint8Array([0x50, 0x4b, 0x03, 0x04]); // ZIP magic bytes
        return new HttpResponse(zipContent, {
          status: 200,
          headers: {
            'Content-Type': 'application/zip',
            'Content-Disposition': 'attachment; filename="project.zip"',
          },
        });
      })
    );

    setAuthToken('test-token');
    await api.exportProjectZip(1);

    expect(capturedHeaders).not.toBeNull();
    expect(capturedHeaders!.get('Authorization')).toBe('Bearer test-token');
  });
});
