/**
 * How the toolpath viewer is sized (#2887).
 *
 * The viewer appends its canvas into the same element it measures with
 * `clientWidth`/`clientHeight` and watches with a ResizeObserver. If that
 * element can take its height from its contents, the canvas ends up sizing the
 * box that sizes the canvas: every resize grew the container, which fired the
 * observer, which resized again. On `/gcode-viewer` the page climbed about
 * 190px a second and never rendered a frame, because a fresh ~18-megapixel
 * buffer was allocated and cleared before the previous one could finish.
 *
 * jsdom does no layout, so the loop itself cannot be reproduced here. What can
 * be pinned down is the structure that makes it impossible: the canvas is out
 * of flow, so it contributes nothing to its parent's height, and the measured
 * element takes a definite height from the pane above it rather than a
 * percentage that resolves to `auto`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';

// Everything three.js does here is arithmetic except creating a GL context,
// which jsdom has no answer for -- so only WebGLRenderer is replaced. Keeping
// Scene/PerspectiveCamera/GridHelper real means the component runs its actual
// setup path rather than one written for the test.
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeWebGLRenderer {
    domElement = document.createElement('canvas');
    setSize(width: number, height: number) {
      // Mirror three.js: the size is written onto the canvas as inline style,
      // which is what fed back into the container's height.
      this.domElement.style.width = `${width}px`;
      this.domElement.style.height = `${height}px`;
    }
    setPixelRatio() {}
    render() {}
    dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeWebGLRenderer };
});

vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    enableDamping = false;
    dampingFactor = 0;
    update() {}
    dispose() {}
  },
}));

import { GcodeToolpathViewer } from '../../components/GcodeToolpathViewer';

const canvas = () => document.querySelector('canvas') as HTMLCanvasElement;

describe('GcodeToolpathViewer sizing', () => {
  beforeEach(() => {
    // Hold the component in its loading state so the canvas stays mounted;
    // an error would swap the whole tree for a message.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('puts the canvas out of flow so it cannot size its own container', () => {
    render(<GcodeToolpathViewer gcodeUrl="/api/v1/archives/224/gcode" />);

    const element = canvas();
    expect(element).toBeTruthy();
    expect(element.style.position).toBe('absolute');
    // An in-flow canvas is `display: inline` by default, and the line box then
    // adds descender space on top of the height just set -- the ~33px per round
    // that drove the growth.
    expect(element.style.display).toBe('block');
    expect(element.style.inset).toBe('0');
  });

  it('measures an element with a definite height, not a percentage one', () => {
    render(<GcodeToolpathViewer gcodeUrl="/api/v1/archives/224/gcode" />);

    const measured = canvas().parentElement as HTMLElement;
    // `h-full` is `height: 100%`, which resolves to `auto` unless every ancestor
    // has a definite height -- on the full-page route none does. `inset-0`
    // against the positioned pane is definite whatever the page does.
    expect(measured.className).toContain('absolute');
    expect(measured.className).toContain('inset-0');
    expect(measured.className).not.toContain('h-full');
  });

  it('keeps the measured element inside a positioned pane', () => {
    const { container } = render(
      <GcodeToolpathViewer gcodeUrl="/api/v1/archives/224/gcode" className="flex-1 min-h-0" />,
    );

    // `inset-0` only means anything against a positioned ancestor; without the
    // `relative` pane the canvas would escape to the viewport.
    const pane = container.firstElementChild as HTMLElement;
    expect(pane.className).toContain('relative');
    expect(pane.className).toContain('flex-1');
    expect(pane.contains(canvas())).toBe(true);
  });
});
