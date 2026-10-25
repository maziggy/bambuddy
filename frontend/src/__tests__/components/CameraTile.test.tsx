import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, screen } from '@testing-library/react';
import { render } from '../utils';
import { CameraTile } from '../../components/CameraTile';
import { setStreamToken } from '../../api/client';

// The shared render() util mounts AuthProvider, which fires an async
// /auth/me probe on mount. Each test absorbs that settle with a single
// `await act(async () => {})` after render so the AuthProvider state
// update doesn't bleed into the assertion phase as an act() warning.
async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('CameraTile', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(null, { status: 200 }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('renders the live stream URL in live mode', async () => {
    render(
      <CameraTile
        printerId={42}
        printerName="X1C-Lab"
        mode="live"
        snapshotIntervalMs={5000}
        connected
      />,
    );
    await flushMicrotasks();
    const img = screen.getByAltText('X1C-Lab') as HTMLImageElement;
    expect(img.src).toContain('/api/v1/printers/42/camera/stream');
    expect(img.src).toContain('fps=8');
  });

  it('renders the snapshot URL and refreshes on the interval', async () => {
    render(
      <CameraTile
        printerId={7}
        printerName="P1S-Garage"
        mode="snapshot"
        snapshotIntervalMs={1000}
        connected
      />,
    );
    await flushMicrotasks();
    const initial = (screen.getByAltText('P1S-Garage') as HTMLImageElement).src;
    expect(initial).toContain('/api/v1/printers/7/camera/snapshot');

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    const refreshed = (screen.getByAltText('P1S-Garage') as HTMLImageElement).src;
    expect(refreshed).toContain('/api/v1/printers/7/camera/snapshot');
    expect(refreshed).not.toBe(initial);
  });

  it('shows an offline placeholder when not connected', async () => {
    render(
      <CameraTile
        printerId={1}
        printerName="A1-Offline"
        mode="live"
        snapshotIntervalMs={5000}
        connected={false}
      />,
    );
    await flushMicrotasks();
    expect(screen.queryByAltText('A1-Offline')).toBeNull();
  });

  it('shows the paused placeholder in paused mode', async () => {
    render(
      <CameraTile
        printerId={9}
        printerName="H2D-Booth"
        mode="paused"
        snapshotIntervalMs={5000}
        connected
      />,
    );
    await flushMicrotasks();
    expect(screen.queryByAltText('H2D-Booth')).toBeNull();
  });

  // External cameras run one stream per viewer; the stop names this tile's own,
  // so it ends this tile's stream and not another viewer's of the printer.
  it('stops its own stream by the viewer id it opened it with', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(new Response(null, { status: 200 }));
    const { rerender } = render(
      <CameraTile printerId={11} printerName="X1C-Viewer" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const viewer = new URL((screen.getByAltText('X1C-Viewer') as HTMLImageElement).src).searchParams.get('viewer');
    expect(viewer).toMatch(/^[A-Za-z0-9_-]{1,64}$/);

    await act(async () => {
      rerender(<CameraTile printerId={11} printerName="X1C-Viewer" mode="snapshot" snapshotIntervalMs={5000} connected />);
    });

    const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
    expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(viewer);
  });

  it('gives each tile its own viewer id', async () => {
    render(
      <>
        <CameraTile printerId={11} printerName="Tile-A" mode="live" snapshotIntervalMs={5000} connected />
        <CameraTile printerId={11} printerName="Tile-B" mode="live" snapshotIntervalMs={5000} connected />
      </>,
    );
    await flushMicrotasks();
    const id = (name: string) => new URL((screen.getByAltText(name) as HTMLImageElement).src).searchParams.get('viewer');

    expect(id('Tile-A')).not.toBe(id('Tile-B'));
  });

  it('POSTs /camera/stop when leaving live mode', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(null, { status: 200 }),
    );
    const { rerender } = render(
      <CameraTile
        printerId={11}
        printerName="X1C-Stop"
        mode="live"
        snapshotIntervalMs={5000}
        connected
      />,
    );
    await flushMicrotasks();
    fetchMock.mockClear();

    await act(async () => {
      rerender(
        <CameraTile
          printerId={11}
          printerName="X1C-Stop"
          mode="snapshot"
          snapshotIntervalMs={5000}
          connected
        />,
      );
    });

    const stopCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes('/api/v1/printers/11/camera/stop'),
    );
    expect(stopCalls.length).toBeGreaterThan(0);
  });
  // A server-ended MJPEG stream fires no img event (load once in Chromium, per
  // frame in Firefox, nothing at the end), so a live tile renews its stream.
  it('renews a live stream every minute without stopping it', async () => {
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const initial = (screen.getByAltText('X1C-Lab') as HTMLImageElement).src;

    await act(async () => {
      vi.advanceTimersByTime(59_000);
    });
    expect((screen.getByAltText('X1C-Lab') as HTMLImageElement).src).toBe(initial);

    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });
    const renewed = (screen.getByAltText('X1C-Lab') as HTMLImageElement).src;
    expect(renewed).toContain('/api/v1/printers/42/camera/stream');
    expect(renewed).not.toBe(initial);
    // /camera/stop would end the stream the other viewers of this printer share.
    expect(vi.mocked(global.fetch).mock.calls.filter(([url]) => String(url).includes('/camera/stop'))).toHaveLength(0);
  });

  it('closes the old stream before opening the renewed one', async () => {
    // A detached img keeps its MJPEG connection open (measured in Chromium):
    // the browser's per-host pool fills and a USB camera stays held.
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const old = screen.getByAltText('X1C-Lab') as HTMLImageElement;

    await act(async () => {
      vi.advanceTimersByTime(61_000);
    });

    expect(old.getAttribute('src')).toBeNull();
    expect(screen.getByAltText('X1C-Lab')).not.toBe(old);
  });

  it('takes the error of a cleared img for no failure', async () => {
    // Browsers fire error on an img whose src is set to '' (the renewal's close).
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const img = screen.getByAltText('X1C-Lab') as HTMLImageElement;

    await act(async () => {
      img.setAttribute('src', '');
      img.dispatchEvent(new Event('error'));
    });

    expect(screen.getByAltText('X1C-Lab')).toBeTruthy();
  });

  it('waits a moment between closing the old stream and opening the new one', async () => {
    // A USB camera serves one reader: the old ffmpeg must let go of it first.
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const old = screen.getByAltText('X1C-Lab') as HTMLImageElement;

    await act(async () => {
      vi.advanceTimersByTime(60_100);
    });
    expect(old.getAttribute('src')).toBeNull();
    expect(screen.getByAltText('X1C-Lab')).toBe(old);

    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByAltText('X1C-Lab')).not.toBe(old);
  });

  it('does not renew while the page is hidden', async () => {
    // The stream token is not refreshed in a background tab; a renewal there
    // would ask with an expired one.
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const old = screen.getByAltText('X1C-Lab') as HTMLImageElement;
    const src = old.src;

    await act(async () => {
      vi.advanceTimersByTime(125_000);
    });

    expect(screen.getByAltText('X1C-Lab')).toBe(old);
    expect(old.src).toBe(src);
    hidden.mockRestore();
  });

  async function renewedThenFailed() {
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    await act(async () => {
      vi.advanceTimersByTime(60_500);
    });
    const renewed = screen.getByAltText('X1C-Lab') as HTMLImageElement;
    await act(async () => {
      renewed.dispatchEvent(new Event('error'));
    });
    return renewed;
  }

  it('retries a renewal that failed once, after a few seconds', async () => {
    const renewed = await renewedThenFailed();
    expect(screen.getByAltText('X1C-Lab')).toBe(renewed);

    await act(async () => {
      vi.advanceTimersByTime(5_500);
    });

    const retried = screen.getByAltText('X1C-Lab') as HTMLImageElement;
    expect(retried).not.toBe(renewed);
    expect(retried.src).toContain('/api/v1/printers/42/camera/stream');
  });

  it('keeps the stream closed while it waits, whatever re-renders the tile', async () => {
    // A failed renewal makes the app mint a new stream token; the re-render it
    // causes must not reconnect the img before the retry is due.
    const { rerender } = render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    await act(async () => {
      vi.advanceTimersByTime(60_500);
    });
    await act(async () => {
      screen.getByAltText('X1C-Lab').dispatchEvent(new Event('error'));
    });

    await act(async () => {
      setStreamToken('fresh-token');
      rerender(
        <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected progress={1} />,
      );
    });

    expect(screen.getByAltText('X1C-Lab').getAttribute('src')).toBeNull();
    setStreamToken(null);
  });

  it('shows no signal when the retry fails too', async () => {
    await renewedThenFailed();
    await act(async () => {
      vi.advanceTimersByTime(5_500);
    });

    await act(async () => {
      screen.getByAltText('X1C-Lab').dispatchEvent(new Event('error'));
    });

    expect(screen.queryByAltText('X1C-Lab')).toBeNull();
  });

  it('treats a renewed stream that showed a frame like any other', async () => {
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    await act(async () => {
      vi.advanceTimersByTime(60_500);
    });
    const renewed = screen.getByAltText('X1C-Lab');
    await act(async () => {
      renewed.dispatchEvent(new Event('load'));
    });

    await act(async () => {
      renewed.dispatchEvent(new Event('error'));
    });

    expect(screen.queryByAltText('X1C-Lab')).toBeNull();
  });

  it('closes the live stream when the tile leaves live mode', async () => {
    const { rerender } = render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const live = screen.getByAltText('X1C-Lab') as HTMLImageElement;

    await act(async () => {
      rerender(<CameraTile printerId={42} printerName="X1C-Lab" mode="paused" snapshotIntervalMs={5000} connected />);
    });
    await flushMicrotasks();

    expect(live.getAttribute('src')).toBeNull();
  });

  it('closes the live stream when the tile goes away', async () => {
    const { unmount } = render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    const live = screen.getByAltText('X1C-Lab') as HTMLImageElement;

    unmount();
    await flushMicrotasks();

    expect(live.getAttribute('src')).toBeNull();
  });

  // A snapshot camera that is down keeps its stream open without a frame: the
  // img fires neither load nor error, and the tile would stay blank.
  async function renewedWithoutFrame() {
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    await act(async () => {
      vi.advanceTimersByTime(60_500);
    });
    return screen.getByAltText('X1C-Lab') as HTMLImageElement;
  }

  it('retries a renewal that shows no frame within 15 seconds', async () => {
    const renewed = await renewedWithoutFrame();

    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    expect(screen.getByAltText('X1C-Lab')).toBe(renewed);

    await act(async () => {
      vi.advanceTimersByTime(5_500);
    });
    expect(screen.getByAltText('X1C-Lab')).not.toBe(renewed);
  });

  it('shows no signal when the retry shows no frame either', async () => {
    await renewedWithoutFrame();

    await act(async () => {
      vi.advanceTimersByTime(20_500);
    });
    await act(async () => {
      vi.advanceTimersByTime(15_500);
    });

    expect(screen.queryByAltText('X1C-Lab')).toBeNull();
  });

  it('keeps a renewal that showed a frame', async () => {
    const renewed = await renewedWithoutFrame();
    await act(async () => {
      renewed.dispatchEvent(new Event('load'));
    });

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });

    expect(screen.getByAltText('X1C-Lab')).toBe(renewed);
  });

  it('keeps its stream under StrictMode', async () => {
    // React 19 runs a ref's cleanup once on mount in development; that must
    // not close the stream of the img that stays.
    render(
      <StrictMode>
        <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />
      </StrictMode>,
    );
    await flushMicrotasks();

    expect(screen.getByAltText('X1C-Lab').getAttribute('src')).toContain('/api/v1/printers/42/camera/stream');
  });

  it('does not count the time a hidden page cannot load the renewal', async () => {
    // A lazy img does not load in a background tab; the tab hidden right after
    // a renewal must not end a working camera on no signal.
    const renewed = await renewedWithoutFrame();
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);

    await act(async () => {
      vi.advanceTimersByTime(45_000);
    });
    hidden.mockRestore();

    expect(screen.getByAltText('X1C-Lab')).toBe(renewed);
  });

  it('leaves a tile showing no signal alone', async () => {
    // Its retry would fail again, and every failed camera image makes the app
    // mint a new stream token and reconnect every camera on the page.
    render(
      <CameraTile printerId={42} printerName="X1C-Lab" mode="live" snapshotIntervalMs={5000} connected />,
    );
    await flushMicrotasks();
    await act(async () => {
      screen.getByAltText('X1C-Lab').dispatchEvent(new Event('error'));
    });

    await act(async () => {
      vi.advanceTimersByTime(121_000);
    });

    expect(screen.queryByAltText('X1C-Lab')).toBeNull();
  });

  describe('a failed snapshot', () => {
    // The server answers 503 while a live view of the printer is starting:
    // it will not take the camera from that viewer. One failure is retried.
    async function snapshotTile() {
      render(
        <CameraTile printerId={7} printerName="P1S-Garage" mode="snapshot" snapshotIntervalMs={2000} connected />,
      );
      await flushMicrotasks();
    }
    const fail = async () =>
      act(async () => {
        screen.getByAltText('P1S-Garage').dispatchEvent(new Event('error'));
      });
    const tick = async () =>
      act(async () => {
        vi.advanceTimersByTime(2100);
      });

    it('is tried again at the next refresh', async () => {
      await snapshotTile();
      const first = (screen.getByAltText('P1S-Garage') as HTMLImageElement).src;
      await fail();
      expect(screen.queryByText('No signal')).toBeNull();

      await tick();

      const retried = screen.getByAltText('P1S-Garage') as HTMLImageElement;
      expect(retried.src).toContain('/api/v1/printers/7/camera/snapshot');
      expect(retried.src).not.toBe(first);
    });

    it('shows no signal when the retry fails too', async () => {
      await snapshotTile();
      await fail();
      await tick();
      await fail();

      expect(screen.getByText('No signal')).toBeInTheDocument();
    });

    it('gets a fresh retry after a snapshot loads', async () => {
      await snapshotTile();
      await fail();
      await tick();
      await act(async () => {
        screen.getByAltText('P1S-Garage').dispatchEvent(new Event('load'));
      });
      await tick();
      await fail();

      expect(screen.queryByText('No signal')).toBeNull();
    });
  });
});

// A portrait camera in a 16:9 tile was turned at the tile's size and cut off
// at the top and bottom (#3362).
describe('CameraTile with a camera turned sideways', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(null, { status: 200 }));
    const isTile = (el: HTMLElement) => el.classList.contains('aspect-video');
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return isTile(this) ? 320 : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return isTile(this) ? 180 : 0;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([90, 270])('lays a camera turned %i degrees out at the tile with its sides swapped', async (rotation) => {
    render(<CameraTile printerId={42} printerName="A1 Mini" cameraRotation={rotation} mode="live" snapshotIntervalMs={5000} connected />);

    const img = await screen.findByAltText('A1 Mini');
    expect(img).toHaveStyle({ position: 'absolute', width: '180px', height: '320px' });
    expect(img.style.transform).toBe(`translate(-50%, -50%) rotate(${rotation}deg)`);
  });

  it('only turns an upside-down camera', async () => {
    render(<CameraTile printerId={42} printerName="A1 Mini" cameraRotation={180} mode="live" snapshotIntervalMs={5000} connected />);

    const img = await screen.findByAltText('A1 Mini');
    expect(img.style.transform).toBe('rotate(180deg)');
    expect(img.style.width).toBe('');
  });
});
