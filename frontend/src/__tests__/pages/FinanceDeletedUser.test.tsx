/**
 * What a deleted user leaves in the finance admin view: ledger entries without
 * a user (kept, so a shared cost centre stays spent) and private cost centres
 * without an owner. Neither reads as "User #null" or "Personal", which look
 * like the viewing admin's own. An entry without a user says "No user": with
 * auth off a print never had one, so "Deleted user" would be wrong there. A
 * private centre is always created with an owner, so none means deleted.
 */

import { describe, it, expect } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FinancePage } from '../../pages/FinancePage';
import { server } from '../mocks/server';

const orphanedCentre = {
  id: 9,
  name: 'Alice private',
  is_private: true,
  owner_user_id: null,
  is_active: true,
  total_balance: 0,
  total_budget: null,
  monthly_budget: null,
  budget_mode: 'none',
  budget_limit: null,
  budget_used: null,
  budget_available: null,
  can_print: true,
};

const orphanedEntry = {
  id: 1,
  user_id: null,
  cost_center_id: null,
  transaction_type: 'deposit',
  amount: 42,
  balance_after: 42,
  description: 'Left by a deleted user',
  created_by_user_id: null,
  print_run_id: null,
  print_archive_id: null,
  print_queue_id: null,
  created_at: '2026-10-11T10:00:00',
};

describe('FinancePage after a user was deleted', () => {
  it('labels their ledger entries as having no user and their private cost centre as a deleted user', async () => {
    server.use(
      http.get('*/api/v1/settings/ui-flags', () => HttpResponse.json({ billing_enabled: true, currency: 'USD' })),
      http.get('*/api/v1/finance/me/balance', () =>
        HttpResponse.json({ user_id: 1, balance: 0, currency: 'USD', updated_at: null }),
      ),
      http.get('*/api/v1/finance/me/transactions', () => HttpResponse.json({ items: [], total: 0 })),
      http.get('*/api/v1/finance/transactions', () =>
        HttpResponse.json({ items: [orphanedEntry], total: 1, limit: 50, offset: 0 }),
      ),
      http.get('*/api/v1/finance/cost-centers/mine', () => HttpResponse.json([])),
      http.get('*/api/v1/finance/cost-centers', () => HttpResponse.json([orphanedCentre])),
      http.get('*/api/v1/users/slim', () => HttpResponse.json([])),
    );

    render(<FinancePage />);
    await userEvent.click(await screen.findByText('Admin view'));

    const entry = (await screen.findByText('Left by a deleted user')).closest('tr') as HTMLElement;
    expect(within(entry).getByText('No user')).toBeInTheDocument();
    expect(within(entry).queryByText('Deleted user')).not.toBeInTheDocument();
    await waitFor(() => {
      const rows = screen
        .getAllByText('Alice private')
        .map((cell) => cell.closest('tr'))
        .filter((row): row is HTMLTableRowElement => row !== null);
      expect(rows.some((row) => within(row).queryByText('Deleted user'))).toBe(true);
    });
    expect(screen.queryByText(/User #null/)).not.toBeInTheDocument();
  });
});
