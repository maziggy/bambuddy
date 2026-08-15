/**
 * The Spool Inventory header must not overflow a phone viewport (#2813).
 *
 * Its five buttons come to roughly 600px side by side, and a flex row whose
 * items cannot shrink is as wide as its contents. At 390px that pushed the
 * header past the viewport, and since the whole page scrolls as one region
 * everything below it panned with the header.
 *
 * jsdom does no layout, so this asserts the two properties that make the
 * overflow impossible rather than a measured width: the header stacks below
 * `sm`, and the button group wraps. Same approach as the VirtualPrinterCard
 * header test (#2808).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { server } from '../mocks/server';

function setupHandlers() {
  server.use(
    http.get('/api/v1/settings/spoolman', () =>
      HttpResponse.json({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      })
    ),
    http.get('/api/v1/inventory/spools', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
  );
}

describe('InventoryPage — header layout', () => {
  beforeEach(() => {
    setupHandlers();
  });

  it('stacks the header below sm and keeps it a row from sm up', async () => {
    render(<InventoryPageRouter />);

    const heading = await screen.findByRole('heading', { name: 'Spool Inventory' });
    // h1 -> title block -> header row
    const header = heading.parentElement?.parentElement as HTMLElement;

    expect(header.className).toContain('flex-col');
    expect(header.className).toContain('sm:flex-row');
    // Desktop is unchanged: the row still spreads title and actions apart.
    expect(header.className).toContain('sm:justify-between');
  });

  it('lets the action buttons wrap instead of widening the header', async () => {
    render(<InventoryPageRouter />);

    // Located through the heading rather than by button name: the empty-state
    // panel offers its own "Add Spool" further down the page.
    const heading = await screen.findByRole('heading', { name: 'Spool Inventory' });
    const header = heading.parentElement?.parentElement as HTMLElement;
    const group = header.lastElementChild as HTMLElement;
    await waitFor(() => {
      expect(group.querySelector('button')).toBeInTheDocument();
    });

    // Without this the group is as wide as all five buttons laid end to end,
    // whatever the viewport is.
    expect(group.className).toContain('flex-wrap');
    // All five actions stay in that one group -- wrapping them is the fix,
    // hiding any of them is not.
    expect(group.querySelectorAll('button')).toHaveLength(5);
  });
});
