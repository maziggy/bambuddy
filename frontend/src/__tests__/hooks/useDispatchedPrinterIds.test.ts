/**
 * Tests for useDispatchedPrinterIds — the hook that exposes printer IDs with
 * a queued/active background-dispatch job so PrinterSelector can grey them
 * out between dispatch-accepted and the printer's PRINT_START report.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useDispatchedPrinterIds,
  __resetDispatchedPrinterIdsForTests,
} from '../../hooks/useDispatchedPrinterIds';

function fire(detail: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('background-dispatch', { detail }));
  });
}

describe('useDispatchedPrinterIds', () => {
  beforeEach(() => {
    __resetDispatchedPrinterIdsForTests();
  });

  afterEach(() => {
    __resetDispatchedPrinterIdsForTests();
  });

  it('returns an empty set initially', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    expect(result.current.size).toBe(0);
  });

  it('picks up printer IDs from dispatched_jobs', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({
      dispatched_jobs: [
        { job_id: 1, printer_id: 42, printer_name: 'Farm-A' },
      ],
      active_jobs: [],
    });
    expect(result.current.has(42)).toBe(true);
    expect(result.current.size).toBe(1);
  });

  it('picks up printer IDs from active_jobs', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({
      dispatched_jobs: [],
      active_jobs: [
        { job_id: 1, printer_id: 7, printer_name: 'Farm-B' },
      ],
    });
    expect(result.current.has(7)).toBe(true);
  });

  it('unions both lists', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({
      dispatched_jobs: [{ job_id: 1, printer_id: 1 }],
      active_jobs: [{ job_id: 2, printer_id: 2 }],
    });
    expect(result.current.size).toBe(2);
    expect(result.current.has(1)).toBe(true);
    expect(result.current.has(2)).toBe(true);
  });

  it('clears printers when subsequent event reports no jobs', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({ dispatched_jobs: [{ job_id: 1, printer_id: 9 }], active_jobs: [] });
    expect(result.current.has(9)).toBe(true);
    fire({ dispatched_jobs: [], active_jobs: [] });
    expect(result.current.size).toBe(0);
  });

  it('ignores jobs without a numeric printer_id', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({
      dispatched_jobs: [
        { job_id: 1, printer_id: 'not-a-number' },
        { job_id: 2 },
        { job_id: 3, printer_id: 5 },
      ],
      active_jobs: [],
    });
    expect(result.current.size).toBe(1);
    expect(result.current.has(5)).toBe(true);
  });

  it('keeps snapshot reference stable when content is unchanged', () => {
    const { result } = renderHook(() => useDispatchedPrinterIds());
    fire({ dispatched_jobs: [{ printer_id: 1 }], active_jobs: [] });
    const first = result.current;
    fire({ dispatched_jobs: [{ printer_id: 1 }], active_jobs: [] });
    expect(result.current).toBe(first);
  });

  it('shares state across hook instances', () => {
    const a = renderHook(() => useDispatchedPrinterIds());
    const b = renderHook(() => useDispatchedPrinterIds());
    fire({ dispatched_jobs: [{ printer_id: 11 }], active_jobs: [] });
    expect(a.result.current.has(11)).toBe(true);
    expect(b.result.current.has(11)).toBe(true);
  });
});
