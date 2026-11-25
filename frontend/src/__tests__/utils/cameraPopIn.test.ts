/**
 * A camera window moved back into a Bambuddy tab as an overlay (#2737): one
 * tab takes it, the one it was opened from first, and with none the window
 * is told so it can stay open.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  isStandaloneWebApp,
  listenForCameraPopIn,
  readOpenerTabId,
  registerCameraTab,
  requestCameraPopIn,
} from '../../utils/camera';

const cleanups: Array<() => void> = [];

function tab(tabId: string, canShow: (printerId: number) => Promise<boolean> = async () => true) {
  const open = vi.fn();
  cleanups.push(listenForCameraPopIn(tabId, open, canShow));
  return open;
}

afterEach(() => {
  cleanups.splice(0).forEach(stop => stop());
  sessionStorage.clear();
});

describe('requestCameraPopIn', () => {
  it('opens the camera in the tab that answers', async () => {
    const open = tab('t1');

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(true);

    expect(open).toHaveBeenCalledExactlyOnceWith(7, 'P1S');
  });

  it('reports no tab when none is open, so the window stays', async () => {
    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(false);
  });

  it('opens it in one tab only when several are open', async () => {
    const tabs = [tab('t1'), tab('t2'), tab('t3')];

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(true);

    expect(tabs.map(open => open.mock.calls.length).reduce((a, b) => a + b)).toBe(1);
  });

  it('prefers the tab the window was opened from', async () => {
    const first = tab('t1');
    const opener = tab('t2');

    await expect(requestCameraPopIn(7, 'P1S', 't2', { answerMs: 50 })).resolves.toBe(true);

    expect(opener).toHaveBeenCalledExactlyOnceWith(7, 'P1S');
    expect(first).not.toHaveBeenCalled();
  });

  it('falls back to another tab when the one it came from is gone', async () => {
    const other = tab('t1');

    await expect(requestCameraPopIn(7, 'P1S', 'gone', { answerMs: 50 })).resolves.toBe(true);

    expect(other).toHaveBeenCalledExactlyOnceWith(7, 'P1S');
  });

  it('stops answering once the tab is cleaned up', async () => {
    const open = vi.fn();
    listenForCameraPopIn('t1', open)();

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(false);
    expect(open).not.toHaveBeenCalled();
  });

  it('opens it once when a duplicated tab answers under the same tab id', async () => {
    const original = tab('t1');
    const duplicate = tab('t1');

    await expect(requestCameraPopIn(7, 'P1S', 't1', { answerMs: 50 })).resolves.toBe(true);

    expect(original.mock.calls.length + duplicate.mock.calls.length).toBe(1);
  });

  it('is not opened by a tab that gets the claim after the window gave up', async () => {
    const open = tab('t1');
    // The window sets the deadline at 1000; the tab reads the clock long after.
    vi.spyOn(Date, 'now').mockReturnValueOnce(1000).mockReturnValue(10_000_000);

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50, doneMs: 200 })).resolves.toBe(false);

    expect(open).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it('moves on to another tab when the claimed one cannot show the printer', async () => {
    // Each tab has its own sign-in: the window's own tab may now be someone
    // who cannot see this printer.
    const refusing = tab('opener', async () => false);
    const other = tab('t2');

    await expect(requestCameraPopIn(7, 'P1S', 'opener', { answerMs: 50 })).resolves.toBe(true);

    expect(refusing).not.toHaveBeenCalled();
    expect(other).toHaveBeenCalledExactlyOnceWith(7, 'P1S');
  });

  it('stays in the window when no tab can show the printer', async () => {
    const refusing = tab('t1', async () => false);

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(false);

    expect(refusing).not.toHaveBeenCalled();
  });

  it('treats a failed check as cannot show', async () => {
    const failing = tab('t1', async () => {
      throw new Error('offline');
    });

    await expect(requestCameraPopIn(7, 'P1S', null, { answerMs: 50 })).resolves.toBe(false);
    expect(failing).not.toHaveBeenCalled();
  });

  it('ignores an offer for no valid printer', async () => {
    const open = tab('t1');

    await expect(requestCameraPopIn(0, 'P1S', null, { answerMs: 50 })).resolves.toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});

describe('the opener tab id', () => {
  it('is what a window opened from the tab reads back', () => {
    // A popup starts with a copy of its opener's sessionStorage.
    const tabId = registerCameraTab();
    expect(readOpenerTabId()).toBe(tabId);
  });

  it('stays the same across a reload, so an open window still finds its tab', () => {
    expect(registerCameraTab()).toBe(registerCameraTab());
  });

  it('is made fresh in a tab that has none', () => {
    const first = registerCameraTab();
    sessionStorage.clear();
    expect(registerCameraTab()).not.toBe(first);
  });
});

describe('isStandaloneWebApp (#3419)', () => {
  afterEach(() => {
    delete (navigator as Navigator & { standalone?: boolean }).standalone;
    vi.restoreAllMocks();
  });

  it('is false in a browser tab', () => {
    expect(isStandaloneWebApp()).toBe(false);
  });

  it('reads iOS Safari home-screen apps by navigator.standalone', () => {
    Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });
    expect(isStandaloneWebApp()).toBe(true);
  });

  it('leaves an app installed on a desktop or Android alone: window.open works there', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) => ({ matches: query === '(display-mode: standalone)' }) as MediaQueryList,
    );
    expect(isStandaloneWebApp()).toBe(false);
  });
});
