/**
 * The archives calendar shows prints, not archives (archive redesign P4): a
 * file printed twice this month is there twice, on the days it ended.
 */

/// <reference types="@testing-library/jest-dom" />

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { CalendarView } from '../../components/CalendarView';
import type { Archive } from '../../api/client';

const now = new Date();
const onDay = (day: number) => new Date(now.getFullYear(), now.getMonth(), day, 12).toISOString();

const archive = (overrides: Partial<Archive>) =>
  ({
    filename: 'f.3mf',
    print_name: 'File',
    status: 'completed',
    thumbnail_path: null,
    filament_color: null,
    run_count: 0,
    completed_at: null,
    created_at: onDay(1),
    ...overrides,
  }) as Archive;

const run = (id: number, archiveId: number, day: number, status = 'completed') => ({
  id,
  archive_id: archiveId,
  status,
  completed_at: onDay(day),
  created_at: onDay(day),
  filament_color: null,
});

describe('CalendarView', () => {
  it('shows each print of an archive on its own day and opens it there', async () => {
    let asked: URLSearchParams | null = null;
    server.use(
      http.get('/api/v1/archives/prints', ({ request }) => {
        asked = new URL(request.url).searchParams;
        return HttpResponse.json({
          items: [run(31, 7, 3, 'failed'), run(32, 7, 5), run(33, 99, 5)],
          total: 3,
        });
      }),
    );
    const onPrintClick = vi.fn();
    render(
      <CalendarView
        archives={[
          archive({ id: 7, print_name: 'Planter', run_count: 2 }),
          // Printed before prints were logged: stands for its one print.
          archive({ id: 8, print_name: 'Hook', completed_at: onDay(5) }),
          archive({ id: 9, print_name: 'Never printed', status: 'archived' }),
        ]}
        printerId={2}
        onPrintClick={onPrintClick}
      />
    );

    // Three prints this month; archive 99 is one the page's filters hide.
    await waitFor(() => expect(screen.getByText('Prints this month').previousSibling?.textContent).toBe('3'));
    expect(asked!.get('printer_id')).toBe('2');
    // The month, as the calendar's local days.
    const last = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    expect([asked!.get('date_from'), asked!.get('date_to')]).toEqual([`${month}-01`, `${month}-${last}`]);
    expect(screen.getByText('Failed').previousSibling?.textContent).toBe('1');

    fireEvent.click(screen.getByRole('button', { name: /^5/ }));
    fireEvent.click(await screen.findByText('Hook'));
    expect(onPrintClick).toHaveBeenLastCalledWith(8, null);
    fireEvent.click(screen.getByText('Planter'));
    expect(onPrintClick).toHaveBeenLastCalledWith(7, 32);
    expect(screen.queryByText('Never printed')).toBeNull();
  });

  it('reads every page of a busy month', async () => {
    const offsets: string[] = [];
    server.use(
      http.get('/api/v1/archives/prints', ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset') ?? '0';
        offsets.push(offset);
        const start = Number(offset);
        const items = Array.from({ length: offset === '0' ? 500 : 1 }, (_, i) => run(start + i + 1, 7, 2));
        return HttpResponse.json({ items, total: 501 });
      }),
    );
    render(<CalendarView archives={[archive({ id: 7, print_name: 'Planter', run_count: 501 })]} />);

    await waitFor(() => expect(screen.getByText('Prints this month').previousSibling?.textContent).toBe('501'));
    expect(offsets).toEqual(['0', '500']);
  });
});
