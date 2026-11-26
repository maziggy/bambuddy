import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  ApiError,
  api,
  type DirectPrintLabelsRequest,
  type LabelPrintJob,
} from '../../api/client';
import { autoPrintRequests } from '../../utils/labelPrint';
import {
  chunkForPrint,
  printCapsProblem,
  printLabelsAfterSave,
  printLabelsWithToasts,
  LabelJobError,
  waitForLabelJob,
} from '../../utils/labelPrintJobs';

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: { printLabelsToPrinter: vi.fn(), getLabelPrintJob: vi.fn() },
}));

// Echoes the fallback with {{x}} filled in, like a missing key would.
const t = ((_key: string, fallback: string, opts?: Record<string, unknown>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(opts?.[k] ?? ''))) as never;

const job = (over: Partial<LabelPrintJob>): LabelPrintJob => ({
  job_id: 'j1',
  state: 'queued',
  printed: 0,
  total: 3,
  error: null,
  ...over,
});
const BODY = { spool_ids: [1], template: 'roll', monochrome: true } as DirectPrintLabelsRequest;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe('waitForLabelJob (#3612)', () => {
  it('polls once a second until the job is done', async () => {
    vi.mocked(api.getLabelPrintJob)
      .mockResolvedValueOnce(job({ state: 'queued' }))
      .mockResolvedValueOnce(job({ state: 'printing', printed: 1 }))
      .mockResolvedValueOnce(job({ state: 'done', printed: 3 }));
    const result = waitForLabelJob('j1', t);
    await vi.advanceTimersByTimeAsync(0);
    expect(api.getLabelPrintJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(api.getLabelPrintJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.getLabelPrintJob).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    await expect(result).resolves.toMatchObject({ state: 'done', printed: 3 });
    expect(api.getLabelPrintJob).toHaveBeenCalledWith('j1');
  });

  it('throws the job error when it failed', async () => {
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'failed', error: 'Label printer reports: cover open' }));
    await expect(waitForLabelJob('j1', t)).rejects.toThrow('Label printer reports: cover open');
  });

  it('says the job is unknown on a 404', async () => {
    vi.mocked(api.getLabelPrintJob).mockRejectedValue(new ApiError('Not found', 404));
    await expect(waitForLabelJob('j1', t)).rejects.toThrow(/no longer knows this print job/);
    expect(api.getLabelPrintJob).toHaveBeenCalledTimes(1);
  });

  it('survives a few failed polls but not a run of them', async () => {
    vi.mocked(api.getLabelPrintJob)
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(job({ state: 'done', printed: 3 }));
    const ok = waitForLabelJob('j1', t);
    await vi.advanceTimersByTimeAsync(1000);
    await expect(ok).resolves.toMatchObject({ state: 'done' });

    vi.mocked(api.getLabelPrintJob).mockReset().mockRejectedValue(new Error('network down'));
    const bad = waitForLabelJob('j1', t).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await bad).toMatchObject({ message: 'network down' });
  });

  it('gives up 30 minutes after the job started printing, with a clear message', async () => {
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'printing', printed: 1 }));
    const result = waitForLabelJob('j1', t).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(29 * 60_000);
    expect(vi.mocked(api.getLabelPrintJob).mock.calls.length).toBeGreaterThan(1000);
    const before = vi.mocked(api.getLabelPrintJob).mock.calls.length;
    await vi.advanceTimersByTimeAsync(2 * 60_000);
    const err = await result;
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toContain('after 30 minutes');
    expect((err as LabelJobError).printed).toBe(1);
    // And it stopped polling.
    const after = vi.mocked(api.getLabelPrintJob).mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(vi.mocked(api.getLabelPrintJob).mock.calls.length).toBe(after);
    expect(after).toBeGreaterThan(before);
  });

  it('does not count the time a job waits in the queue', async () => {
    let state: LabelPrintJob['state'] = 'queued';
    vi.mocked(api.getLabelPrintJob).mockImplementation(async () => job({ state }));
    const result = waitForLabelJob('j1', t).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(45 * 60_000); // far longer than the bound, still queued
    state = 'printing';
    await vi.advanceTimersByTimeAsync(29 * 60_000); // not yet 30 minutes of printing
    state = 'done';
    await vi.advanceTimersByTimeAsync(2000);
    await expect(result).resolves.toMatchObject({ state: 'done' });
  });
});

describe('print caps and chunking (#3612)', () => {
  it('flags more than 100 labels and more than 4000 mm of tape', () => {
    expect(printCapsProblem(100, 40)).toBeNull();
    expect(printCapsProblem(101, 20)).toBe('tooMany');
    expect(printCapsProblem(21, 200)).toBe('tooLong');
    expect(printCapsProblem(20, 200)).toBeNull();
  });

  it('chunks by count and by tape length', () => {
    const ids = Array.from({ length: 150 }, (_, i) => i + 1);
    expect(chunkForPrint(ids, 40).map((c) => c.length)).toEqual([100, 50]);
    // 4000 / 200 = 20 labels per request.
    expect(chunkForPrint(ids.slice(0, 45), 200).map((c) => c.length)).toEqual([20, 20, 5]);
    expect(chunkForPrint([], 40)).toEqual([]);
  });

  it('turns 150 spools into two requests within both caps', () => {
    const reqs = autoPrintRequests(Array.from({ length: 150 }, (_, i) => i + 1), '50');
    expect(reqs).toHaveLength(2);
    expect(reqs.map((r) => r.spool_ids.length)).toEqual([100, 50]);
    expect(reqs.every((r) => r.template === 'roll' && r.roll?.tape === '50')).toBe(true);
  });
});

describe('printing with toasts (#3612)', () => {
  it('toasts progress, then the printed count, once the job is done', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter).mockResolvedValue({ job_id: 'j1', total: 3 });
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'done', printed: 3 }));
    await printLabelsWithToasts('inventory', BODY, { t, showToast });
    expect(showToast).toHaveBeenCalledWith('Printing 3 labels…', 'info');
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith('Printed 3 labels', 'success');
  });

  it('toasts the job error when the printer fails', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter).mockResolvedValue({ job_id: 'j1', total: 3 });
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'failed', error: 'Label printer reports: no media' }));
    await printLabelsWithToasts('inventory', BODY, { t, showToast });
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith('Could not print: Label printer reports: no media', 'error');
  });

  it('explains a bare 504 from a reverse proxy', async () => {
    vi.mocked(api.printLabelsToPrinter).mockRejectedValue(new ApiError('HTTP 504', 504));
    await expect(printLabelsWithToasts('inventory', BODY, { t, showToast: vi.fn() })).rejects.toThrow(
      'The request timed out on the way to Bambuddy; check whether the label printed before trying again.',
    );
  });

  it('keeps the backend text of a 504 that has a JSON body', async () => {
    vi.mocked(api.printLabelsToPrinter).mockRejectedValue(new ApiError('Printer timed out', 504));
    await expect(printLabelsWithToasts('inventory', BODY, { t, showToast: vi.fn() })).rejects.toThrow('Printer timed out');
  });

  it('prints chunks one after the other and toasts the total once', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter).mockImplementation(async (_s, body) => ({
      job_id: `j${body.spool_ids.length}`,
      total: body.spool_ids.length,
    }));
    vi.mocked(api.getLabelPrintJob).mockImplementation(async (id) =>
      job({ job_id: id, state: 'done', printed: Number(id.slice(1)) }),
    );
    printLabelsAfterSave('inventory', autoPrintRequests(Array.from({ length: 150 }, (_, i) => i + 1), '50'), { t, showToast });
    await vi.advanceTimersByTimeAsync(0);
    expect(api.printLabelsToPrinter).toHaveBeenCalledTimes(2);
    expect(showToast).toHaveBeenCalledTimes(1);
    expect(showToast).toHaveBeenCalledWith('Printed 150 labels', 'success');
  });

  it('warns once with the spool saved text when a chunk fails', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter).mockResolvedValue({ job_id: 'j1', total: 1 });
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'failed', error: 'cover open' }));
    printLabelsAfterSave('inventory', [BODY, BODY], { t, showToast });
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith('Spool saved, but the label could not be printed: cover open', 'error');
    expect(api.printLabelsToPrinter).toHaveBeenCalledTimes(1);
  });

  it('says how many labels were printed before a later chunk failed', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter).mockImplementation(async (_s, body) => ({
      job_id: `j${body.spool_ids.length}`,
      total: body.spool_ids.length,
    }));
    // Chunk 1 (100 labels) prints; chunk 2 (50) fails after 7.
    vi.mocked(api.getLabelPrintJob).mockImplementation(async (id) =>
      id === 'j100'
        ? job({ job_id: id, state: 'done', printed: 100 })
        : job({ job_id: id, state: 'failed', printed: 7, error: 'cover open' }),
    );
    printLabelsAfterSave('inventory', autoPrintRequests(Array.from({ length: 150 }, (_, i) => i + 1), '50'), { t, showToast });
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith(
      'Spool saved, but the label printing stopped after 107 of 150 labels: cover open',
      'error',
    );
  });

  it('counts the earlier chunks when the next request is refused', async () => {
    const showToast = vi.fn();
    vi.mocked(api.printLabelsToPrinter)
      .mockResolvedValueOnce({ job_id: 'j100', total: 100 })
      .mockRejectedValueOnce(new ApiError('The label printer has too many jobs waiting; try again later', 503));
    vi.mocked(api.getLabelPrintJob).mockResolvedValue(job({ state: 'done', printed: 100 }));
    printLabelsAfterSave('inventory', autoPrintRequests(Array.from({ length: 150 }, (_, i) => i + 1), '50'), { t, showToast });
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith(
      'Spool saved, but the label printing stopped after 100 of 150 labels: The label printer has too many jobs waiting; try again later',
      'error',
    );
  });
});
