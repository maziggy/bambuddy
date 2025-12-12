/**
 * Test setup file for Vitest.
 * Configures testing environment, mocks, and MSW server.
 */

import '@testing-library/jest-dom';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './mocks/server';

// Setup MSW server - bypass WebSocket requests so our mock handles them
beforeAll(() =>
  server.listen({
    onUnhandledRequest: (request, print) => {
      // Allow WebSocket requests to pass through to our mock
      if (request.url.includes('/ws')) {
        return;
      }
      // Error on other unhandled requests
      print.error();
    },
  })
);
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

// Mock window.matchMedia for responsive components
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
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

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// Suppress console errors during tests (optional, can be removed for debugging)
// vi.spyOn(console, 'error').mockImplementation(() => {});
