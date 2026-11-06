/**
 * Pages load as separate files (#3175). A tab from before an update asks for
 * files the update deleted: it must reload once, never loop, and never while
 * the server is down (a kiosk would be left on the browser's error page).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Component, Suspense, type ReactNode } from 'react';

const originalLocation = window.location;
let reload: ReturnType<typeof vi.fn>;
let health: ReturnType<typeof vi.fn>;

async function freshModule() {
  vi.resetModules();
  return import('../../utils/lazyPage');
}

class Catch extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    return this.state.error ? <p>crashed: {this.state.error}</p> : this.props.children;
  }
}

beforeEach(() => {
  reload = vi.fn();
  Object.defineProperty(window, 'location', { configurable: true, value: { ...originalLocation, reload } });
  window.sessionStorage.clear();
  health = vi.fn().mockResolvedValue(new Response('{"status":"healthy"}', { status: 200 }));
  vi.stubGlobal('fetch', health);
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('reloadForMissingChunk', () => {
  it('reloads once and records when', async () => {
    const { reloadForMissingChunk } = await freshModule();

    expect(reloadForMissingChunk()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(Number(window.sessionStorage.getItem('bambuddy_chunk_reload_at'))).toBeGreaterThan(0);

    // A second failure in the same page while the reload is under way.
    expect(reloadForMissingChunk()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not reload again right after its own reload', async () => {
    window.sessionStorage.setItem('bambuddy_chunk_reload_at', String(Date.now() - 2000));
    const { reloadForMissingChunk } = await freshModule();

    expect(reloadForMissingChunk()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('reloads again for a later update', async () => {
    window.sessionStorage.setItem('bambuddy_chunk_reload_at', String(Date.now() - 60_000));
    const { reloadForMissingChunk } = await freshModule();

    expect(reloadForMissingChunk()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('never reloads without sessionStorage, since nothing could stop a loop', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { reloadForMissingChunk } = await freshModule();

    expect(reloadForMissingChunk()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('lazyPage', () => {
  it('renders the named export', async () => {
    const { lazyPage } = await freshModule();
    const Page = lazyPage(async () => ({ HelloPage: () => <p>hello page</p> }), 'HelloPage');

    render(<Suspense fallback={<p>loading</p>}><Page /></Suspense>);

    expect(await screen.findByText('hello page')).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it('reloads instead of crashing when the file is gone', async () => {
    const { lazyPage } = await freshModule();
    const Page = lazyPage(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module')), 'X');

    render(<Catch><Suspense fallback={<p>loading</p>}><Page /></Suspense></Catch>);

    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(screen.getByText('loading')).toBeInTheDocument();
    expect(screen.queryByText(/crashed/)).not.toBeInTheDocument();
  });

  it('keeps the spinner while the server is down, then reloads', async () => {
    let up = false;
    health.mockImplementation(() =>
      up ? Promise.resolve(new Response('', { status: 200 })) : Promise.reject(new TypeError('Failed to fetch')),
    );
    const { lazyPage } = await freshModule();
    const Page = lazyPage(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module')), 'X');

    render(<Catch><Suspense fallback={<p>loading</p>}><Page /></Suspense></Catch>);

    await vi.waitFor(() => expect(health).toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText('loading')).toBeInTheDocument();

    up = true;
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1), { timeout: 5000 });
    expect(screen.queryByText(/crashed/)).not.toBeInTheDocument();
  });

  it('shows the error when the reload did not help', async () => {
    window.sessionStorage.setItem('bambuddy_chunk_reload_at', String(Date.now()));
    const { lazyPage } = await freshModule();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const Page = lazyPage(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module')), 'X');

    render(<Catch><Suspense fallback={<p>loading</p>}><Page /></Suspense></Catch>);

    expect(await screen.findByText(/crashed: Failed to fetch/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('recoverFromMissingChunk', () => {
  it('reloads straight away when the server answers', async () => {
    const { recoverFromMissingChunk } = await freshModule();

    await expect(recoverFromMissingChunk()).resolves.toBe(true);
    expect(health).toHaveBeenCalledWith('/health', { cache: 'no-store', redirect: 'manual' });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('waits for the server to come back before reloading', async () => {
    vi.useFakeTimers();
    health
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('', { status: 502 }))
      .mockResolvedValueOnce(new Response('', { status: 522 }));
    const { recoverFromMissingChunk } = await freshModule();

    const recovered = recoverFromMissingChunk();
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(reload).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(3000);
    await expect(recovered).resolves.toBe(true);
    expect(health).toHaveBeenCalledTimes(4);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('counts a redirect to a login page as the server answering', async () => {
    health.mockResolvedValue({ status: 0, type: 'opaqueredirect' });
    const { recoverFromMissingChunk } = await freshModule();

    await expect(recoverFromMissingChunk()).resolves.toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('resolves false when its own reload did not help', async () => {
    window.sessionStorage.setItem('bambuddy_chunk_reload_at', String(Date.now()));
    const { recoverFromMissingChunk } = await freshModule();

    await expect(recoverFromMissingChunk()).resolves.toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
