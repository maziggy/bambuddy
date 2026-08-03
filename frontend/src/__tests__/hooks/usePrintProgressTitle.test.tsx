import type { ReactNode } from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the theme pref and the API the hook reads, so the effect can be exercised
// without the real providers. `theme.value` is swapped per test.
const h = vi.hoisted(() => ({
  theme: {
    value: {
      progressInTitle: false,
      resolvedMode: 'dark',
      darkAccent: 'green',
      lightAccent: 'green',
    } as { progressInTitle: boolean; resolvedMode: string; darkAccent: string; lightAccent: string },
  },
  getPrinters: vi.fn(),
  getPrinterStatus: vi.fn(),
}));

vi.mock('../../contexts/ThemeContext', () => ({ useTheme: () => h.theme.value }));
vi.mock('../../api/client', () => ({
  api: { getPrinters: h.getPrinters, getPrinterStatus: h.getPrinterStatus },
}));

import { pickActivePrint, usePrintProgressTitle, type ProgressStatus } from '../../hooks/usePrintProgressTitle';

const running = (progress: number, remaining_time: number | null): ProgressStatus => ({
  state: 'RUNNING',
  progress,
  remaining_time,
});

describe('pickActivePrint', () => {
  it('returns null when nothing is printing', () => {
    expect(pickActivePrint([])).toBeNull();
    expect(pickActivePrint([undefined])).toBeNull();
    expect(pickActivePrint([{ state: 'IDLE', progress: 0, remaining_time: null }])).toBeNull();
    // The real paused state is 'PAUSE', not 'PAUSED'.
    expect(pickActivePrint([{ state: 'PAUSE', progress: 40, remaining_time: 10 }])).toBeNull();
  });

  it('ignores RUNNING prints with no progress value', () => {
    expect(pickActivePrint([{ state: 'RUNNING', progress: null, remaining_time: 5 }])).toBeNull();
  });

  it('picks the soonest-finishing print among several running', () => {
    const soonest = running(20, 12);
    expect(pickActivePrint([running(80, 45), soonest, running(50, 30)])).toBe(soonest);
  });

  it('tie-breaks equal ETAs by highest progress', () => {
    const further = running(70, 15);
    expect(pickActivePrint([running(30, 15), further])).toBe(further);
  });

  it('treats a null remaining_time as furthest away', () => {
    const withEta = running(10, 60);
    expect(pickActivePrint([running(90, null), withEta])).toBe(withEta);
  });

  it('treats remaining_time <= 0 as unknown — a just-started print must not win', () => {
    // The backend serialises "ETA not known yet" as 0 (not null). A printer that
    // just started (0) must not steal the tab from one that is nearly done.
    const almostDone = running(95, 180);
    expect(pickActivePrint([running(2, 0), almostDone])).toBe(almostDone);
    expect(pickActivePrint([running(2, -1), almostDone])).toBe(almostDone);
  });
});

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

// jsdom has no canvas backend — calling getContext('2d') logs a "Not implemented"
// jsdomError (with a full React stack) into the suite output on every run, and
// leaves the favicon path untested because it bails on the null context. Stub
// both canvas calls so the ring code actually executes and the swap is assertable.
const RING_URL = 'data:image/png;base64,ring';
const fakeCtx = {
  beginPath: vi.fn(),
  arc: vi.fn(),
  stroke: vi.fn(),
  lineWidth: 0,
  strokeStyle: '',
  lineCap: 'butt',
} as unknown as CanvasRenderingContext2D;

const realGetContext = HTMLCanvasElement.prototype.getContext;
const realToDataURL = HTMLCanvasElement.prototype.toDataURL;

function faviconHref(): string {
  return document.querySelector<HTMLLinkElement>('link[rel~="icon"]')!.href;
}

describe('usePrintProgressTitle effect', () => {
  beforeEach(() => {
    h.getPrinters.mockReset();
    h.getPrinterStatus.mockReset();

    HTMLCanvasElement.prototype.getContext = (() =>
      fakeCtx) as typeof HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.toDataURL = (() =>
      RING_URL) as typeof HTMLCanvasElement.prototype.toDataURL;

    // Replaces <title> too, so set the title after wiring the head up — the hook
    // captures document.title at mount.
    document.head.innerHTML = '<link rel="icon" href="/favicon.svg">';
    document.title = 'Bambuddy';
  });
  afterEach(() => {
    cleanup();
    HTMLCanvasElement.prototype.getContext = realGetContext;
    HTMLCanvasElement.prototype.toDataURL = realToDataURL;
  });

  it('is inert while the pref is off — never touches the tab title', async () => {
    h.theme.value = { progressInTitle: false, resolvedMode: 'dark', darkAccent: 'green', lightAccent: 'green' };
    document.title = 'Something Else';

    renderHook(() => usePrintProgressTitle(), { wrapper: wrapper() });

    await new Promise((r) => setTimeout(r, 20));
    expect(document.title).toBe('Something Else');
    expect(faviconHref()).toContain('/favicon.svg');
    expect(h.getPrinters).not.toHaveBeenCalled();
  });

  it('shows the active print percentage in the title and swaps the favicon', async () => {
    h.theme.value = { progressInTitle: true, resolvedMode: 'dark', darkAccent: 'green', lightAccent: 'green' };
    h.getPrinters.mockResolvedValue([{ id: 1 }]);
    h.getPrinterStatus.mockResolvedValue({ state: 'RUNNING', progress: 42, remaining_time: 600 });

    renderHook(() => usePrintProgressTitle(), { wrapper: wrapper() });

    await waitFor(() => expect(document.title).toBe('42% · Bambuddy'));
    expect(faviconHref()).toBe(RING_URL);
  });

  it('restores the original title and favicon when the pref is switched off', async () => {
    h.theme.value = { progressInTitle: true, resolvedMode: 'dark', darkAccent: 'green', lightAccent: 'green' };
    h.getPrinters.mockResolvedValue([{ id: 1 }]);
    h.getPrinterStatus.mockResolvedValue({ state: 'RUNNING', progress: 42, remaining_time: 600 });

    const { rerender } = renderHook(() => usePrintProgressTitle(), { wrapper: wrapper() });
    await waitFor(() => expect(document.title).toBe('42% · Bambuddy'));

    h.theme.value = { ...h.theme.value, progressInTitle: false };
    rerender();

    await waitFor(() => expect(document.title).toBe('Bambuddy'));
    expect(faviconHref()).toContain('/favicon.svg');
  });
});
