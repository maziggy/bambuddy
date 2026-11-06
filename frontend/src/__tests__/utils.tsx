/**
 * Test utilities and wrapper components.
 */

import React from 'react';
import { vi } from 'vitest';
import { render } from '@testing-library/react';
import type { RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '../contexts/ThemeContext';
import { ToastProvider } from '../contexts/ToastContext';
import { AuthProvider } from '../contexts/AuthContext';
import { EmbeddedCameraProvider } from '../contexts/EmbeddedCameraContext';

// Create a new QueryClient for each test
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

interface AllProvidersProps {
  children: React.ReactNode;
}

function AllProviders({ children }: AllProvidersProps) {
  const queryClient = createTestQueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* ThemeProvider is now mounted inside AuthProvider in App.tsx so
            its initial ``api.getSettings()`` sync can gate on auth state.
            Tests follow the same nesting; otherwise ThemeProvider's
            ``useAuth()`` throws "AuthContext must be used inside
            AuthProvider". */}
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              <EmbeddedCameraProvider>{children}</EmbeddedCameraProvider>
            </ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

/**
 * Custom render function that wraps components with all providers.
 */
function customRender(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
) {
  return render(ui, { wrapper: AllProviders, ...options });
}

// Re-export everything from testing-library
export * from '@testing-library/react';

// Override render with our custom render
export { customRender as render };

/**
 * Create a test QueryClient with custom configuration.
 */
export { createTestQueryClient };

/**
 * Helper to wait for async operations.
 */
export const waitForAsync = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * The spool QR scanner's live camera, pointed at a label reading `rawValue` for
 * as long as the test runs: a camera that opens, a video with frames, and the
 * platform decoder reading that label off each one.
 */
export function stubLiveCamera(rawValue: string) {
  const track = { stop: vi.fn(), applyConstraints: vi.fn().mockResolvedValue(undefined) };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  vi.stubGlobal('navigator', { ...navigator, mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(4); // HAVE_ENOUGH_DATA
  vi.stubGlobal(
    'BarcodeDetector',
    class {
      detect = vi.fn().mockResolvedValue([{ rawValue }]);
    },
  );
}

/** Let the spool QR scanner's live loop (a decode every 200 ms) run a few more frames. */
export const frames = (n: number) => new Promise((resolve) => setTimeout(resolve, 250 * n));
