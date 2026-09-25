/**
 * Zoom and resize behaviour of the 3D model viewer (#2976).
 *
 * Only what jsdom cannot provide is replaced: the WebGL renderer, the PMREM
 * environment it would bake, and OrbitControls. Scene, camera and the STL
 * loader are real, so the tests exercise the component's own framing maths.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, waitFor } from '@testing-library/react';
import * as THREE from 'three';

const mocks = vi.hoisted(() => ({
  setSize: vi.fn(),
  wheelSeen: vi.fn(),
  controlsInstances: [] as Array<{ object: THREE.Camera; domElement: HTMLElement; target: THREE.Vector3; minDistance: number; maxDistance: number }>,
}));

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };
    toneMapping = 0;
    toneMappingExposure = 1;
    setSize(width: number, height: number) {
      mocks.setSize(width, height);
      this.domElement.style.width = `${width}px`;
      this.domElement.style.height = `${height}px`;
    }
    setPixelRatio() {}
    render() {}
    dispose() {}
  }
  class FakePMREMGenerator {
    fromScene() {
      return { texture: { dispose() {} } };
    }
    dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeWebGLRenderer, PMREMGenerator: FakePMREMGenerator };
});

vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    object: THREE.Camera;
    domElement: HTMLElement;
    target = new THREE.Vector3();
    enableDamping = false;
    dampingFactor = 0;
    minDistance = 0;
    maxDistance = Infinity;
    constructor(object: THREE.Camera, domElement: HTMLElement) {
      this.object = object;
      this.domElement = domElement;
      // The real controls listen for wheel on the element they are handed;
      // the fake does the same so the test can tell where events end up.
      domElement.addEventListener('wheel', mocks.wheelSeen);
      mocks.controlsInstances.push(this);
    }
    update() {}
    dispose() {
      this.domElement.removeEventListener('wheel', mocks.wheelSeen);
    }
  },
}));

vi.mock('../../api/client', () => ({
  getAuthToken: () => null,
}));

import { ModelViewer } from '../../components/ModelViewer';

// Binary STL of one triangle, enough for a bounding box.
function tinyStl(): ArrayBuffer {
  const buffer = new ArrayBuffer(84 + 50);
  const view = new DataView(buffer);
  view.setUint32(80, 1, true);
  const floats = [0, 0, 1, 0, 0, 0, 20, 0, 0, 0, 20, 10];
  floats.forEach((value, i) => view.setFloat32(84 + i * 4, value, true));
  return buffer;
}

function setClientSize(element: HTMLElement, width: number, height: number) {
  Object.defineProperty(element, 'clientWidth', { configurable: true, value: width });
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: height });
}

describe('ModelViewer controls', () => {
  beforeEach(() => {
    mocks.setSize.mockClear();
    mocks.wheelSeen.mockClear();
    mocks.controlsInstances.length = 0;
    // jsdom lays nothing out; give every element a panel-sized box so the
    // camera gets a real aspect ratio to frame against.
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 800 });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 600 });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(tinyStl(), { status: 200 })),
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
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
    delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight;
  });

  async function renderLoaded() {
    const utils = render(<ModelViewer url="/api/v1/library/files/1/download" fileType="stl" />);
    const canvas = document.querySelector('canvas') as HTMLCanvasElement;
    // Loaded once the zoom / reset buttons replace the spinner.
    await waitFor(() => expect(document.querySelector('.lucide-zoom-in')).not.toBeNull());
    return { ...utils, canvas, controls: mocks.controlsInstances[0] };
  }

  it('hands the canvas that is in the DOM to OrbitControls, so wheel events reach it', async () => {
    const { canvas, controls } = await renderLoaded();

    expect(controls.domElement).toBe(canvas);
    expect(document.body.contains(canvas)).toBe(true);

    fireEvent.wheel(canvas, { deltaY: -100 });
    expect(mocks.wheelSeen).toHaveBeenCalledTimes(1);
  });

  it('bounds the dolly range and keeps the far plane beyond it', async () => {
    const { controls } = await renderLoaded();
    const camera = controls.object as THREE.PerspectiveCamera;

    const framed = camera.position.distanceTo(controls.target);
    expect(controls.maxDistance).toBeGreaterThan(framed);
    expect(Number.isFinite(controls.maxDistance)).toBe(true);
    expect(controls.minDistance).toBeGreaterThan(0);
    // Zooming all the way out must never push the model past the far plane.
    expect(camera.far).toBeGreaterThan(controls.maxDistance);
  });

  it('zoom buttons dolly along the view axis toward the orbit target', async () => {
    const { controls } = await renderLoaded();
    const camera = controls.object as THREE.PerspectiveCamera;
    const before = camera.position.clone();
    const distanceBefore = before.distanceTo(controls.target);
    const directionBefore = before.clone().sub(controls.target).normalize();

    fireEvent.click(document.querySelector('.lucide-zoom-in')!.closest('button')!);

    const distanceAfter = camera.position.distanceTo(controls.target);
    expect(distanceAfter).toBeCloseTo(distanceBefore * 0.8, 5);
    const directionAfter = camera.position.clone().sub(controls.target).normalize();
    expect(directionAfter.distanceTo(directionBefore)).toBeLessThan(1e-6);

    fireEvent.click(document.querySelector('.lucide-zoom-out')!.closest('button')!);
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(distanceBefore, 5);
  });

  it('resets to the framed view, not a fixed pose', async () => {
    const { controls } = await renderLoaded();
    const camera = controls.object as THREE.PerspectiveCamera;
    const framedPosition = camera.position.clone();
    const framedTarget = controls.target.clone();

    fireEvent.click(document.querySelector('.lucide-zoom-in')!.closest('button')!);
    fireEvent.click(document.querySelector('.lucide-rotate-ccw')!.closest('button')!);

    expect(camera.position.distanceTo(framedPosition)).toBeLessThan(1e-6);
    expect(controls.target.distanceTo(framedTarget)).toBeLessThan(1e-6);
  });

  it('resizes the canvas and camera when fullscreen changes', async () => {
    const { canvas, controls } = await renderLoaded();
    const camera = controls.object as THREE.PerspectiveCamera;
    const container = canvas.parentElement as HTMLElement;
    mocks.setSize.mockClear();

    setClientSize(container, 1920, 1080);
    act(() => {
      document.dispatchEvent(new Event('fullscreenchange'));
    });

    expect(mocks.setSize).toHaveBeenCalledWith(1920, 1080);
    expect(camera.aspect).toBeCloseTo(1920 / 1080, 5);
    expect(canvas.style.width).toBe('1920px');
  });

  it('ignores a resize to zero (hidden container) instead of collapsing the camera', async () => {
    const { canvas, controls } = await renderLoaded();
    const camera = controls.object as THREE.PerspectiveCamera;
    const container = canvas.parentElement as HTMLElement;
    const aspect = camera.aspect;
    mocks.setSize.mockClear();

    setClientSize(container, 0, 0);
    act(() => {
      document.dispatchEvent(new Event('fullscreenchange'));
    });

    expect(mocks.setSize).not.toHaveBeenCalled();
    expect(camera.aspect).toBe(aspect);
  });
});
