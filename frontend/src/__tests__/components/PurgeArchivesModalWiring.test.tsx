/**
 * Wiring tests for PurgeArchivesModal: pin the requests the destructive
 * bulk archive purge actually sends (preview query params, execute body),
 * and that nothing is sent while the confirm button is disabled or when
 * the modal is cancelled.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PurgeArchivesModal } from '../../components/PurgeArchivesModal';

interface PreviewCall {
  days: string | null;
  purgeStats: string | null;
}

let previewCalls: PreviewCall[];
let executeBodies: Array<Record<string, unknown>>;
let previewCount: number;

beforeEach(() => {
  previewCalls = [];
  executeBodies = [];
  previewCount = 4;
  server.use(
    http.get('*/api/v1/archives/purge/preview', ({ request }) => {
      const url = new URL(request.url);
      previewCalls.push({
        days: url.searchParams.get('older_than_days'),
        purgeStats: url.searchParams.get('purge_stats'),
      });
      return HttpResponse.json({
        count: previewCount,
        total_bytes: 1024,
        sample_filenames: ['old-a.3mf'],
        older_than_days: Number(url.searchParams.get('older_than_days')),
      });
    }),
    http.post('*/api/v1/archives/purge', async ({ request }) => {
      executeBodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ deleted: previewCount, purge_stats: false });
    }),
  );
});

describe('PurgeArchivesModal wiring', () => {
  it('previews with the initial days and executes with the same days value', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<PurgeArchivesModal onClose={onClose} initialDays={90} />);

    const confirm = await screen.findByRole('button', { name: 'Remove 4 archive(s)' });
    expect(previewCalls).toEqual([{ days: '90', purgeStats: 'false' }]);

    await user.click(confirm);

    await waitFor(() => expect(executeBodies).toHaveLength(1));
    expect(executeBodies[0]).toEqual({ older_than_days: 90, purge_stats: false });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('re-previews after the days input changes and executes with the new value', async () => {
    const user = userEvent.setup();
    render(<PurgeArchivesModal onClose={vi.fn()} initialDays={90} />);
    await screen.findByRole('button', { name: 'Remove 4 archive(s)' });

    const input = screen.getByLabelText('Delete archives not printed in the last');
    fireEvent.change(input, { target: { value: '30' } });

    await waitFor(() =>
      expect(previewCalls).toContainEqual({ days: '30', purgeStats: 'false' }),
    );
    const confirm = await screen.findByRole('button', { name: 'Remove 4 archive(s)' });
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await waitFor(() => expect(executeBodies).toHaveLength(1));
    expect(executeBodies[0]).toEqual({ older_than_days: 30, purge_stats: false });
  });

  it('sends purge_stats=true on preview and execute when "Also remove from statistics" is ticked', async () => {
    const user = userEvent.setup();
    render(<PurgeArchivesModal onClose={vi.fn()} initialDays={120} />);
    await screen.findByRole('button', { name: 'Remove 4 archive(s)' });

    await user.click(screen.getByRole('checkbox'));
    await waitFor(() =>
      expect(previewCalls).toContainEqual({ days: '120', purgeStats: 'true' }),
    );

    await user.click(await screen.findByRole('button', { name: 'Remove 4 archive(s)' }));
    await waitFor(() => expect(executeBodies).toHaveLength(1));
    expect(executeBodies[0]).toEqual({ older_than_days: 120, purge_stats: true });
  });

  it('keeps execute disabled and sends nothing when the preview matches zero archives', async () => {
    previewCount = 0;
    const user = userEvent.setup();
    render(<PurgeArchivesModal onClose={vi.fn()} initialDays={90} />);

    const confirm = await screen.findByRole('button', { name: 'Remove 0 archive(s)' });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(executeBodies).toHaveLength(0);
  });

  it('Cancel closes the modal without executing the purge', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<PurgeArchivesModal onClose={onClose} initialDays={90} />);
    await screen.findByRole('button', { name: 'Remove 4 archive(s)' });

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(executeBodies).toHaveLength(0);
  });

  it('disables the purge until the preview matches the typed age', async () => {
    render(<PurgeArchivesModal onClose={vi.fn()} initialDays={365} />);
    const confirm = await screen.findByRole('button', { name: 'Remove 4 archive(s)' });
    expect(confirm).toBeEnabled();

    fireEvent.change(screen.getByLabelText('Delete archives not printed in the last'), {
      target: { value: '1' },
    });

    expect(confirm).toBeDisabled();
    await waitFor(() => expect(previewCalls.map((c) => c.days)).toContain('1'));
  });

  // The preview is debounced (300 ms); a click inside that window must not
  // purge by an age the shown count wasn't made for.
  it('never executes with a days value that has not been previewed', async () => {
    const user = userEvent.setup();
    render(<PurgeArchivesModal onClose={vi.fn()} initialDays={365} />);
    const confirm = await screen.findByRole('button', { name: 'Remove 4 archive(s)' });

    fireEvent.change(screen.getByLabelText('Delete archives not printed in the last'), {
      target: { value: '1' },
    });
    // Immediately, inside the debounce window.
    await user.click(confirm);

    await waitFor(() => expect(executeBodies.length + previewCalls.length).toBeGreaterThan(1));
    for (const body of executeBodies) {
      expect(previewCalls.map((c) => c.days)).toContain(String(body.older_than_days));
    }
  });
});
