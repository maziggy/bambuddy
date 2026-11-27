/**
 * The shopping list CSV shows typed text (here a note) as it was typed when
 * opened in a spreadsheet: a note that starts like a formula gets a leading
 * quote.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ForecastPanel } from '../../components/ForecastPanel';

const NOTE = '=HYPERLINK("https://elsewhere.example","open")';

afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

describe('ForecastPanel shopping list CSV', () => {
  it('writes a note that starts like a formula as text', async () => {
    server.use(
      http.get('*/api/v1/settings/', () => HttpResponse.json({ forecast_global_lead_time_days: 7 })),
      http.get('*/api/v1/inventory/sku-settings', () => HttpResponse.json([])),
      http.get(/\/api\/v1\/inventory\/usage/, () => HttpResponse.json([])),
      http.get('*/api/v1/inventory/shopping-list', () =>
        HttpResponse.json([
          {
            id: 1, material: 'PLA', subtype: null, brand: 'Polymaker', color_name: 'Red',
            quantity_spools: 2, status: 'pending', note: NOTE, added_at: '2025-01-01T00:00:00Z',
          },
        ]),
      ),
    );
    const blobs: Blob[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
      blobs.push(blob as Blob);
      return 'blob:shopping-list';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<ForecastPanel spools={[]} />);
    // The list's badge shows once it has loaded; then open it.
    await screen.findByText('1');
    await user.click(screen.getByRole('button', { name: /shopping list/i }));
    await user.click(await screen.findByRole('button', { name: 'CSV' }));

    await waitFor(() => expect(blobs).toHaveLength(1));
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blobs[0]);
    });
    const [, row] = text.split('\n');
    expect(row).toContain('"Polymaker"');
    expect(row.endsWith(`"'=HYPERLINK(""https://elsewhere.example"",""open"")"`)).toBe(true);
  });
});
