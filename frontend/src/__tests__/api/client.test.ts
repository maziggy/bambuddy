/**
 * Tests for the API client auth token handling.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { setAuthToken, getAuthToken, api, setStreamToken } from '../../api/client';

// Mock sessionStorage (H-5: tokens are stored in sessionStorage, not localStorage)
const sessionStorageMock = {
  store: {} as Record<string, string>,
  getItem: vi.fn((key: string) => sessionStorageMock.store[key] || null),
  setItem: vi.fn((key: string, value: string) => {
    sessionStorageMock.store[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete sessionStorageMock.store[key];
  }),
  clear: vi.fn(() => {
    sessionStorageMock.store = {};
  }),
};

Object.defineProperty(window, 'sessionStorage', {
  value: sessionStorageMock,
});

// Create MSW server
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  sessionStorageMock.clear();
  vi.mocked(localStorage.setItem).mockClear();
  vi.mocked(localStorage.removeItem).mockClear();
  setAuthToken(null);
});
afterAll(() => server.close());

describe('Auth Token Management', () => {
  it('setAuthToken stores token in sessionStorage', () => {
    setAuthToken('test-token-123');
    expect(sessionStorageMock.setItem).toHaveBeenCalledWith('auth_token', 'test-token-123');
    expect(getAuthToken()).toBe('test-token-123');
  });

  it('setAuthToken removes token from sessionStorage when null', () => {
    setAuthToken('test-token-123');
    setAuthToken(null);
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
    expect(getAuthToken()).toBeNull();
  });

  it("setAuthToken('persistent') writes to both sessionStorage and localStorage", () => {
    setAuthToken('persist-token', 'persistent');
    expect(sessionStorageMock.setItem).toHaveBeenCalledWith('auth_token', 'persist-token');
    expect(vi.mocked(localStorage.setItem)).toHaveBeenCalledWith('auth_token', 'persist-token');
    expect(getAuthToken()).toBe('persist-token');
  });

  it("setAuthToken('session') writes only to sessionStorage, not localStorage", () => {
    setAuthToken('session-token', 'session');
    expect(sessionStorageMock.setItem).toHaveBeenCalledWith('auth_token', 'session-token');
    expect(vi.mocked(localStorage.setItem)).not.toHaveBeenCalledWith('auth_token', expect.any(String));
  });

  it('setAuthToken(null) removes from both storages regardless of previous persistence', () => {
    setAuthToken('some-token', 'persistent');
    vi.mocked(localStorage.setItem).mockClear();
    setAuthToken(null);
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
    expect(vi.mocked(localStorage.removeItem)).toHaveBeenCalledWith('auth_token');
    expect(getAuthToken()).toBeNull();
  });

  it('setAuthToken keeps in-memory token when sessionStorage throws', () => {
    sessionStorageMock.setItem.mockImplementationOnce(() => {
      throw new DOMException('QuotaExceededError');
    });
    // Should not throw even when storage is unavailable
    expect(() => setAuthToken('fallback-token')).not.toThrow();
    // In-memory token must still be set
    expect(getAuthToken()).toBe('fallback-token');
  });

  it('setAuthToken(null) removes from sessionStorage even when localStorage.removeItem throws', () => {
    setAuthToken('some-token', 'persistent');
    vi.mocked(localStorage.removeItem).mockImplementationOnce(() => {
      throw new DOMException('SecurityError');
    });
    // Must not throw — localStorage failure must not abort the sessionStorage removal
    expect(() => setAuthToken(null)).not.toThrow();
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
    expect(getAuthToken()).toBeNull();
  });

  it('setAuthToken(null) removes from localStorage even when sessionStorage.removeItem throws', () => {
    setAuthToken('some-token', 'persistent');
    sessionStorageMock.removeItem.mockImplementationOnce(() => {
      throw new DOMException('SecurityError');
    });
    // Must not throw — sessionStorage failure must not abort the localStorage removal
    expect(() => setAuthToken(null)).not.toThrow();
    expect(vi.mocked(localStorage.removeItem)).toHaveBeenCalledWith('auth_token');
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

  it('clears token on 401 with invalid token message', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () => {
        return HttpResponse.json(
          { detail: 'Could not validate credentials' },
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
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('auth_token');
  });

  it('does not clear token on 401 with generic auth error', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () => {
        return HttpResponse.json(
          { detail: 'Authentication required' },
          { status: 401 }
        );
      })
    );

    setAuthToken('valid-token');
    expect(getAuthToken()).toBe('valid-token');

    try {
      await api.getSpoolmanSettings();
    } catch {
      // Expected to throw
    }

    // Token should NOT be cleared for generic auth errors (might be timing issue)
    expect(getAuthToken()).toBe('valid-token');
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

describe('Printer control endpoints', () => {
  it('refreshPrinterStatus POSTs to /printers/:id/refresh-status', async () => {
    let calledUrl: string | null = null;
    let calledMethod: string | null = null;
    server.use(
      http.post('/api/v1/printers/:id/refresh-status', ({ request, params }) => {
        calledUrl = `/printers/${params.id}/refresh-status`;
        calledMethod = request.method;
        return HttpResponse.json({ status: 'ok' });
      }),
    );

    const result = await api.refreshPrinterStatus(7);
    expect(calledMethod).toBe('POST');
    expect(calledUrl).toBe('/printers/7/refresh-status');
    expect(result).toEqual({ status: 'ok' });
  });

  it('setAirductMode passes mode in query string', async () => {
    let capturedUrl = '';
    server.use(
      http.post('/api/v1/printers/:id/airduct-mode', ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );

    await api.setAirductMode(3, 'cooling');
    expect(capturedUrl).toContain('mode=cooling');

    await api.setAirductMode(3, 'heating');
    expect(capturedUrl).toContain('mode=heating');
  });
});

// #1155 — `<img src>` can't carry an `Authorization: Bearer …` header, so the
// project cover-image URL must use the same stream-token pattern as
// /archives/{id}/thumbnail. A regression where `withStreamToken` is removed
// would break the modal preview AND the card thumbnail when auth is enabled.
describe('Project cover image URL (#1155)', () => {
  afterEach(() => {
    setStreamToken(null);
  });

  it('appends the stream token query string when one is set', () => {
    setStreamToken('abc123');
    const url = api.getProjectCoverImageUrl(42);
    expect(url).toContain('/projects/42/cover-image');
    expect(url).toContain('token=abc123');
  });

  it('returns the bare URL when no stream token is set', () => {
    setStreamToken(null);
    const url = api.getProjectCoverImageUrl(42);
    expect(url).toContain('/projects/42/cover-image');
    expect(url).not.toContain('token=');
  });

  it('URL-encodes a token containing query-string-unsafe characters', () => {
    setStreamToken('a&b=c');
    const url = api.getProjectCoverImageUrl(7);
    // Decoded back, the token must round-trip exactly.
    const params = new URL(url, 'http://x').searchParams;
    expect(params.get('token')).toBe('a&b=c');
  });
});
