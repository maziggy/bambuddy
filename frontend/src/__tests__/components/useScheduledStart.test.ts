import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useScheduledStart } from '../../components/PrintModal/useScheduledStart';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

it('expires a future start without any other render', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 10, 23, 12, 0, 59));
  const { result } = renderHook(() => useScheduledStart(true, '2026-11-23T12:01', 'iso', '24h'));
  expect(result.current.future).toBe(true);
  act(() => { vi.advanceTimersByTime(999); });
  expect(result.current.future).toBe(true);
  act(() => { vi.advanceTimersByTime(1); });
  expect(result.current.future).toBe(false);
  expect(result.current.past).toBe(true);
});

it('rejects the skipped spring hour and accepts the first hour after it', () => {
  vi.stubEnv('TZ', 'America/New_York');
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-03-01T12:00:00Z'));
  const { result } = renderHook(() => useScheduledStart(true, '', 'iso', '24h'));
  act(() => result.current.setFields('2026-03-08', '02:30'));
  expect(result.current.dateValid).toBe(true);
  expect(result.current.timeValid).toBe(false);
  expect(result.current.future).toBe(false);
  act(() => result.current.setFields('2026-03-08', '03:30'));
  expect(result.current.timeValid).toBe(true);
  expect(result.current.start).toBe(Date.parse('2026-03-08T07:30:00Z'));
});
