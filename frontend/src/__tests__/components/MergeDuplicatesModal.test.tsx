/**
 * MergeDuplicatesModal (archive redesign P2): the groups the server found,
 * the ones it leaves out and why, the choice of groups, the confirm step,
 * and what is sent and reported.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { MergeDuplicatesModal } from '../../components/MergeDuplicatesModal';

const member = (id: number, name: string, runs: number) => ({
  id,
  print_name: name,
  filename: `${name}.3mf`,
  created_at: '2026-09-01T09:00:00',
  run_count: runs,
});

let mergeBodies: Array<Record<string, unknown>>;
let mergeResult: Record<string, unknown>;
let groups: unknown[];

beforeEach(() => {
  mergeBodies = [];
  groups = [
    { survivor: member(1, 'Planter', 2), merged: [member(4, 'Planter', 1), member(7, 'Planter', 0)], skipped: null },
    { survivor: member(2, 'Bracket', 1), merged: [member(5, 'Bracket', 1)], skipped: null },
    { survivor: member(3, 'Hook', 1), merged: [member(6, 'Hook', 1)], skipped: 'busy' },
    { survivor: member(8, 'Lid', 1), merged: [member(9, 'Lid', 1)], skipped: 'conflicting_files' },
  ];
  mergeResult = { merged: [{ survivor_id: 1, merged_ids: [4, 7] }, { survivor_id: 2, merged_ids: [5] }], skipped: [] };
  server.use(
    http.get('*/api/v1/archives/merge-preview', () => HttpResponse.json({ groups })),
    http.post('*/api/v1/archives/merge', async ({ request }) => {
      mergeBodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(mergeResult);
    }),
  );
});

describe('MergeDuplicatesModal', () => {
  it('lists the groups, the archive that stays and the ones merging in', async () => {
    render(<MergeDuplicatesModal onClose={vi.fn()} />);

    const planter = await screen.findByTestId('merge-group-1');
    expect(planter).toHaveTextContent('Planter');
    expect(planter).toHaveTextContent('2 print(s)');
    expect(planter.querySelectorAll('li')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Merge 2 group(s)' })).toBeEnabled();
  });

  it('shows the groups left out with their reason and no checkbox', async () => {
    render(<MergeDuplicatesModal onClose={vi.fn()} />);

    const hook = await screen.findByTestId('merge-group-3');
    expect(hook).toHaveTextContent('In use: printing, queued or just printed');
    expect(hook.querySelector('input[type="checkbox"]')).toBeNull();
    expect(screen.getByTestId('merge-group-8')).toHaveTextContent('different source or design files');
  });

  it('merges only the ticked groups, after a confirm', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mergeResult = { merged: [{ survivor_id: 1, merged_ids: [4, 7] }], skipped: [] };
    render(<MergeDuplicatesModal onClose={onClose} />);

    await user.click(await screen.findByRole('checkbox', { name: 'Merge the archives of Bracket' }));
    await user.click(screen.getByRole('button', { name: 'Merge 1 group(s)' }));
    expect(mergeBodies).toEqual([]);
    expect(screen.getByText(/1 group\(s\) will be merged/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Yes, merge 1 group(s)' }));

    await waitFor(() => expect(mergeBodies).toEqual([{ survivor_ids: [1] }]));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(await screen.findByText('Merged 1 group(s).')).toBeInTheDocument();
  });

  it('shows which groups the merge left out and why, and stays open', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mergeResult = { merged: [{ survivor_id: 1, merged_ids: [4, 7] }], skipped: [{ survivor_id: 2, reason: 'failed' }] };
    render(<MergeDuplicatesModal onClose={onClose} />);

    await user.click(await screen.findByRole('button', { name: 'Merge 2 group(s)' }));
    await user.click(screen.getByRole('button', { name: 'Yes, merge 2 group(s)' }));

    expect(await screen.findByText('Merged 1 group(s); 1 left out.')).toBeInTheDocument();
    const report = await screen.findByTestId('merge-result');
    expect(report).toHaveTextContent('Bracket');
    expect(report).toHaveTextContent('The merge failed.');
    expect(onClose).not.toHaveBeenCalled();
    const closes = screen.getAllByRole('button', { name: 'Close' });
    await user.click(closes[closes.length - 1]);
    expect(onClose).toHaveBeenCalled();
  });

  it('says so when there is nothing to merge', async () => {
    groups = [];
    render(<MergeDuplicatesModal onClose={vi.fn()} />);

    expect(await screen.findByText('No duplicates to merge.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Merge 0 group(s)' })).toBeDisabled();
  });

  it('cannot merge when every group is left out', async () => {
    groups = [groups[2]];
    render(<MergeDuplicatesModal onClose={vi.fn()} />);

    await screen.findByTestId('merge-group-3');
    expect(screen.getByRole('button', { name: 'Merge 0 group(s)' })).toBeDisabled();
  });
});
