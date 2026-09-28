/**
 * STEP loading state (#2976).
 *
 * A large STEP export can take over a minute in the OpenCascade worker. With
 * only a spinner on screen that read as a preview that never opens, so a
 * STEP load says what it is doing and counts the seconds. Other formats load
 * in a moment and keep the bare spinner.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';

// Only the GL context is faked (jsdom has none); the rest of three.js runs.
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };
    toneMapping = 0;
    toneMappingExposure = 1;
    setSize() {}
    setPixelRatio() {}
    render() {}
    dispose() {}
  }
  class FakePMREMGenerator {
    fromScene() {
      return { texture: new actual.Texture(), dispose() {} };
    }
    dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeWebGLRenderer, PMREMGenerator: FakePMREMGenerator };
});

vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: class {
    enableDamping = false;
    dampingFactor = 0;
    target = { set() {}, copy() {} };
    update() {}
    dispose() {}
  },
}));

import { ModelViewer } from '../../components/ModelViewer';

describe('ModelViewer STEP loading state', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Never resolves: the viewer stays in its loading state for the test.
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
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
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('says a STEP file is converting and counts the seconds', () => {
    render(<ModelViewer url="/api/v1/library/files/7/download" fileType="stp" />);

    expect(screen.getByText('Converting STEP model… 0 s')).toBeInTheDocument();
    expect(screen.getByText('Large STEP files can take a minute or more.')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText('Converting STEP model… 3 s')).toBeInTheDocument();
  });

  it('recognises a .step URL without an explicit file type', () => {
    render(<ModelViewer url="/files/housing.step" />);
    expect(screen.getByText('Converting STEP model… 0 s')).toBeInTheDocument();
  });

  it('keeps the bare spinner for formats that load quickly', () => {
    render(<ModelViewer url="/api/v1/library/files/8/download" fileType="stl" />);
    expect(screen.queryByText(/Converting STEP model/)).not.toBeInTheDocument();
    expect(screen.queryByText('Large STEP files can take a minute or more.')).not.toBeInTheDocument();
  });
});
