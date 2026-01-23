/**
 * Test utilities and wrapper components.
 */

import React from 'react';
import { render } from '@testing-library/react';
import type { RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '../contexts/ThemeContext';
import { ToastProvider } from '../contexts/ToastContext';

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
        <ThemeProvider>
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
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
