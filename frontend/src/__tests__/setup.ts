/**
 * Test setup file for Vitest.
 * Configures testing environment, mocks, and MSW server.
 */

import '@testing-library/jest-dom';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './mocks/server';

// Initialize i18n for tests (suppresses react-i18next warnings)
import '../i18n';

// Setup MSW server
beforeAll(() =>
  server.listen({
    // Bypass unhandled requests silently (don't warn, just let them through)
    // Handlers use wildcard (*) prefix to match any origin
    onUnhandledRequest: 'bypass',
  })
);
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

// Mock window.matchMedia for responsive components
// Uses a plain function (not vi.fn) so vi.restoreAllMocks() in tests can't wipe it
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true,
  }),
});

// Mock ResizeObserver
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverMock);

// Mock IntersectionObserver
class IntersectionObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  root = null;
  rootMargin = '';
  thresholds = [];
}
vi.stubGlobal('IntersectionObserver', IntersectionObserverMock);

// Mock WebSocket
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = MockWebSocket.OPEN;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  url: string;
  constructor(url: string) {
    this.url = url;
    setTimeout(() => this.onopen?.(new Event('open')), 0);
  }

  send = vi.fn();
  close = vi.fn();
}
vi.stubGlobal('WebSocket', MockWebSocket);

// Mock scrollTo
window.scrollTo = vi.fn();

// Silence jsdom's "Not implemented: navigation (except hash changes)"
// warning when production code does ``window.location.href = '/setup'``
// (AuthContext setup-redirect) or other full-page nav assignments.
//
// jsdom defines ``href`` as a non-configurable accessor on
// ``Location.prototype``, so it cannot be redefined on the instance via
// ``Object.defineProperty``. We wrap the real jsdom Location in a Proxy
// that turns ``href = "..."`` writes into silent no-ops; everything
// else (reads of ``pathname`` / ``search`` / ``hash``, writes to
// ``hash``, ``assign()`` / ``replace()`` calls, ``history.replaceState``
// updating ``search``) passes through unchanged. The ``get`` trap is
// deliberately permissive: returning a substitute value for a non-
// configurable target property violates Proxy invariants and the spread
// operator (``{ ...window.location }``) walks every key — tests that
// use the spread to copy the location object must keep working.
{
  const realLocation = window.location;
  const locationProxy = new Proxy(realLocation, {
    set(target, prop, value) {
      if (prop === 'href') {
        // Silently swallow "navigation not implemented". Tests asserting
        // on the redirect should replace ``window.location`` themselves
        // (several existing tests do exactly this).
        return true;
      }
      Reflect.set(target, prop, value);
      return true;
    },
    get(target, prop, receiver) {
      // Return the exact value present on the target. Returning a
      // bound/wrapped version of a non-configurable function (``assign``
      // is one) violates Proxy invariants (the spread operator at one
      // call site triggers this). Production code that does
      // ``window.location.assign(url)`` calls the function with the
      // proxy as ``this``, which jsdom still accepts because its
      // Location methods unwrap their receiver internally.
      return Reflect.get(target, prop, receiver);
    },
  });
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: locationProxy,
  });
}

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// Suppress console output during tests (reduces noise)
// Remove these lines if you need to debug test output
vi.spyOn(console, 'log').mockImplementation(() => {});
vi.spyOn(console, 'warn').mockImplementation(() => {});
vi.spyOn(console, 'error').mockImplementation(() => {});
