/**
 * #3581: a print charge's description can list what it is made of, and a
 * failed print's filament can be charged in full or not at all. The ledger
 * keeps reading the "[failed: ...]" suffix as before: it becomes the badge,
 * and the breakdown stays in the description.
 */

import { describe, it, expect } from 'vitest';
import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FinancePage } from '../../pages/FinancePage';
import { server } from '../mocks/server';

function charge(id: number, description: string, amount: number) {
  return {
    id,
    user_id: 1,
    cost_center_id: null,
    transaction_type: 'print_charge',
    amount,
    balance_after: 0,
    description,
    created_by_user_id: null,
    print_run_id: `run-${id}`,
    print_archive_id: id,
    print_queue_id: null,
    created_at: '2026-11-17T10:00:00',
  };
}

const CASES: Array<[string, string, string | null]> = [
  // [stored description, what the ledger shows, badge]
  [
    'Print charge: Benchy (filament 1.20 + energy 0.08 + wear 0.25)',
    'Print charge: Benchy (filament 1.20 + energy 0.08 + wear 0.25)',
    null,
  ],
  [
    'Print charge: Cube (filament 0.60 + energy 0.08) [failed: 50.0% filament (50.0g/100.0g)]',
    'Print charge: Cube (filament 0.60 + energy 0.08)',
    'failed',
  ],
  [
    'Print charge: Vase (filament 1.20; filament charged in full) [failed: full filament]',
    'Print charge: Vase (filament 1.20; filament charged in full)',
    'failed',
  ],
  [
    'Print charge: Gear (energy 0.08 + wear 0.25; filament not charged) [cancelled: filament not charged]',
    'Print charge: Gear (energy 0.08 + wear 0.25; filament not charged)',
    'cancelled',
  ],
  [
    'Print charge: Hook (filament 1.20 + wear 0.25; energy not available)',
    'Print charge: Hook (filament 1.20 + wear 0.25; energy not available)',
    null,
  ],
];

describe('FinancePage - print charge breakdown (#3581)', () => {
  it.each(CASES)('%s', async (stored, shown, badge) => {
    server.use(
      http.get('*/api/v1/settings/ui-flags', () => HttpResponse.json({ billing_enabled: true, currency: 'USD' })),
      http.get('*/api/v1/finance/me/balance', () =>
        HttpResponse.json({ user_id: 1, balance: 0, currency: 'USD', updated_at: null }),
      ),
      http.get('*/api/v1/finance/me/transactions', () =>
        HttpResponse.json({ items: [charge(1, stored, -1.53)], total: 1, limit: 50, offset: 0 }),
      ),
      http.get('*/api/v1/finance/cost-centers/mine', () => HttpResponse.json([])),
      http.get('*/api/v1/finance/cost-centers', () => HttpResponse.json([])),
      http.get('*/api/v1/users/slim', () => HttpResponse.json([])),
    );

    render(<FinancePage />);

    const row = (await screen.findByText(shown)).closest('tr') as HTMLElement;
    expect(within(row).getByText('-$1.53')).toBeInTheDocument();
    expect(within(row).queryByText(/\[(failed|cancelled|aborted):/)).toBeNull();
    const partial = within(row).queryByText(/^(Failed|Cancelled|Aborted|Partial)$/i);
    if (badge === null) expect(partial).toBeNull();
    else expect(partial?.textContent?.toLowerCase()).toBe(badge);
  });
});
