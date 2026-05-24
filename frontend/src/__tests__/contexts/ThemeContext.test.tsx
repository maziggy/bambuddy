/**
 * Tests for the ThemeContext system theme detection feature.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { ThemeProvider, useTheme } from '../../contexts/ThemeContext';
import type { ReactNode } from 'react';

// Helper to create a controllable matchMedia mock for individual tests
function mockMatchMedia(prefersDark: boolean) {
  let listener: ((e: MediaQueryListEvent) => void) | null = null;

  const mql = {
    matches: prefersDark,
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: (_event: string, cb: (e: MediaQueryListEvent) => void) => {
      listener = cb;
    },
    removeEventListener: () => {},
    dispatchEvent: () => true,
  };

  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => mql,
  });

  return {
    /** Simulate an OS theme change event */
    fireChange: (dark: boolean) => {
      mql.matches = dark;
      if (listener) {
        listener({ matches: dark } as MediaQueryListEvent);
      }
    },
  };
}

function wrapper({ children }: { children: ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>;
}

describe('ThemeContext', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = '';
  });

  describe('systemPreference initialization', () => {
    it('initializes systemPreference as dark when OS prefers dark', () => {
      mockMatchMedia(true);

      const { result } = renderHook(() => useTheme(), { wrapper });

      // When mode is system, resolvedMode should follow OS preference
      act(() => result.current.setMode('system'));
      expect(result.current.resolvedMode).toBe('dark');
    });

    it('initializes systemPreference as light when OS prefers light', () => {
      mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });

      act(() => result.current.setMode('system'));
      expect(result.current.resolvedMode).toBe('light');
    });
  });

  describe('matchMedia change event', () => {
    it('updates systemPreference when OS theme changes', () => {
      const { fireChange } = mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('system'));

      expect(result.current.resolvedMode).toBe('light');

      act(() => fireChange(true));
      expect(result.current.resolvedMode).toBe('dark');

      act(() => fireChange(false));
      expect(result.current.resolvedMode).toBe('light');
    });
  });

  describe('resolvedMode', () => {
    it('follows explicit mode when mode is dark', () => {
      mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('dark'));

      expect(result.current.resolvedMode).toBe('dark');
    });

    it('follows explicit mode when mode is light', () => {
      mockMatchMedia(true);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('light'));

      expect(result.current.resolvedMode).toBe('light');
    });

    it('follows systemPreference when mode is system', () => {
      const { fireChange } = mockMatchMedia(true);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('system'));

      expect(result.current.resolvedMode).toBe('dark');

      act(() => fireChange(false));
      expect(result.current.resolvedMode).toBe('light');
    });

    it('ignores OS changes when mode is explicit', () => {
      const { fireChange } = mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('dark'));

      act(() => fireChange(true));
      expect(result.current.resolvedMode).toBe('dark');
    });
  });

  describe('document root dark class', () => {
    it('adds dark class when mode is system and OS prefers dark', () => {
      mockMatchMedia(true);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('system'));

      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('removes dark class when mode is system and OS prefers light', () => {
      mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('system'));

      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });

    it('adds dark class when mode is explicitly dark', () => {
      mockMatchMedia(false);

      const { result } = renderHook(() => useTheme(), { wrapper });
      act(() => result.current.setMode('dark'));

      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });
  });

  describe('toggleMode', () => {
    it('cycles dark → light → system → dark', () => {
      mockMatchMedia(false);
      localStorage.setItem('theme-mode', 'dark');

      const { result } = renderHook(() => useTheme(), { wrapper });

      expect(result.current.mode).toBe('dark');

      act(() => result.current.toggleMode());
      expect(result.current.mode).toBe('light');

      act(() => result.current.toggleMode());
      expect(result.current.mode).toBe('system');

      act(() => result.current.toggleMode());
      expect(result.current.mode).toBe('dark');
    });
  });
});
