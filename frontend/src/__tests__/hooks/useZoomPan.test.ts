/**
 * Tests for the useZoomPan hook.
 */

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useZoomPan } from '../../hooks/useZoomPan';
import { createRef } from 'react';

function setup(options?: { defaultMaxPan?: { x: number; y: number } }) {
  const containerRef = createRef<HTMLDivElement>();
  return renderHook(() =>
    useZoomPan({ containerRef, ...options })
  );
}

describe('useZoomPan', () => {
  describe('initial state', () => {
    it('starts at zoom level 1', () => {
      const { result } = setup();
      expect(result.current.zoomLevel).toBe(1);
    });

    it('starts with zero pan offset', () => {
      const { result } = setup();
      expect(result.current.panOffset).toEqual({ x: 0, y: 0 });
    });

    it('starts not panning', () => {
      const { result } = setup();
      expect(result.current.isPanning).toBe(false);
    });
  });

  describe('handleZoomIn', () => {
    it('increases zoom by 0.5', () => {
      const { result } = setup();
      act(() => result.current.handleZoomIn());
      expect(result.current.zoomLevel).toBe(1.5);
    });

    it('caps zoom at 4', () => {
      const { result } = setup();
      // Zoom in 10 times (each +0.5)
      for (let i = 0; i < 10; i++) {
        act(() => result.current.handleZoomIn());
      }
      expect(result.current.zoomLevel).toBe(4);
    });
  });

  describe('handleZoomOut', () => {
    it('decreases zoom by 0.5', () => {
      const { result } = setup();
      act(() => result.current.handleZoomIn()); // 1.5
      act(() => result.current.handleZoomIn()); // 2.0
      act(() => result.current.handleZoomOut()); // 1.5
      expect(result.current.zoomLevel).toBe(1.5);
    });

    it('caps zoom at 1 (no zoom below 1x)', () => {
      const { result } = setup();
      act(() => result.current.handleZoomOut());
      expect(result.current.zoomLevel).toBe(1);
    });

    it('resets pan offset when zoom reaches 1', () => {
      const { result } = setup();
      act(() => result.current.handleZoomIn()); // 1.5
      act(() => {
        result.current.setPanOffset({ x: 50, y: 50 });
      });
      act(() => result.current.handleZoomOut()); // 1.0
      expect(result.current.panOffset).toEqual({ x: 0, y: 0 });
    });
  });

  describe('resetZoom', () => {
    it('resets zoom and pan', () => {
      const { result } = setup();
      act(() => result.current.handleZoomIn());
      act(() => result.current.handleZoomIn());
      act(() => {
        result.current.setPanOffset({ x: 100, y: 100 });
      });
      act(() => result.current.resetZoom());
      expect(result.current.zoomLevel).toBe(1);
      expect(result.current.panOffset).toEqual({ x: 0, y: 0 });
    });
  });

  describe('handleMouseUp', () => {
    it('stops panning', () => {
      const { result } = setup();
      // Can't easily trigger panning state through mouse events in jsdom,
      // but we can verify handleMouseUp is callable and returns void
      act(() => result.current.handleMouseUp());
      expect(result.current.isPanning).toBe(false);
    });
  });

  describe('returned API completeness', () => {
    it('returns all expected functions and state', () => {
      const { result } = setup();
      const api = result.current;

      expect(typeof api.zoomLevel).toBe('number');
      expect(typeof api.setZoomLevel).toBe('function');
      expect(typeof api.panOffset).toBe('object');
      expect(typeof api.setPanOffset).toBe('function');
      expect(typeof api.isPanning).toBe('boolean');
      expect(typeof api.handleZoomIn).toBe('function');
      expect(typeof api.handleZoomOut).toBe('function');
      expect(typeof api.handleWheel).toBe('function');
      expect(typeof api.handleMouseDown).toBe('function');
      expect(typeof api.handleMouseMove).toBe('function');
      expect(typeof api.handleMouseUp).toBe('function');
      expect(typeof api.handleTouchStart).toBe('function');
      expect(typeof api.handleTouchMove).toBe('function');
      expect(typeof api.handleTouchEnd).toBe('function');
      expect(typeof api.resetZoom).toBe('function');
    });
  });
});
