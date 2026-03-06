/**
 * Tests for getDefaultState() factory function in EmbeddedCameraViewer.
 */

import { describe, it, expect } from 'vitest';
import { getDefaultState } from '../../components/cameraDefaults';

describe('getDefaultState', () => {
  it('returns correct shape with x, y, width, height', () => {
    const state = getDefaultState();
    expect(typeof state.x).toBe('number');
    expect(typeof state.y).toBe('number');
    expect(typeof state.width).toBe('number');
    expect(typeof state.height).toBe('number');
  });

  it('returns width 400 and height 300', () => {
    const state = getDefaultState();
    expect(state.width).toBe(400);
    expect(state.height).toBe(300);
  });

  it('computes x from window.innerWidth', () => {
    const state = getDefaultState();
    expect(state.x).toBe(window.innerWidth - 420);
  });

  it('returns y of 20', () => {
    const state = getDefaultState();
    expect(state.y).toBe(20);
  });

  it('returns fresh object each call', () => {
    const a = getDefaultState();
    const b = getDefaultState();
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });
});
