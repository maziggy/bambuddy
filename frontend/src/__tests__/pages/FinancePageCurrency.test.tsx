/**
 * #3123: the Finance page renders the install's currency.
 *
 * It was the only surface in the app that took its currency from a data row
 * (`wallet.currency`, a column since removed) instead of the `currency`
 * setting, and it fell back to EUR where every other page falls back to USD.
 * An install configured for AUD showed a euro balance, euro cost-center
 * budgets and euro transactions -- they all read the same variable. The
 * balance response still carries a currency; the point here is that the page
 * does not depend on it, so a failed or ungranted wallet fetch cannot change
 * the symbol.
 *
 * The currency comes from /settings/ui-flags rather than /settings, which
 * needs SETTINGS_READ that a cost_centers:read_own user does not have (#3023).
 */

import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FinancePage } from '../../pages/FinancePage';
import { server } from '../mocks/server';

function mockFinance({ uiFlagCurrency, walletCurrency }: { uiFlagCurrency?: string; walletCurrency: string }) {
  server.use(
    http.get('*/api/v1/settings/ui-flags', () =>
      HttpResponse.json({ billing_enabled: true, ...(uiFlagCurrency ? { currency: uiFlagCurrency } : {}) }),
    ),
    http.get('*/api/v1/finance/me/balance', () =>
      HttpResponse.json({ user_id: 1, balance: 12.34, currency: walletCurrency, updated_at: null }),
    ),
    http.get('*/api/v1/finance/me/transactions', () => HttpResponse.json({ items: [], total: 0 })),
    http.get('*/api/v1/finance/cost-centers/mine', () => HttpResponse.json([])),
    http.get('*/api/v1/finance/cost-centers', () => HttpResponse.json([])),
    http.get('*/api/v1/users/slim', () => HttpResponse.json([])),
  );
}

describe('FinancePage currency', () => {
  it('renders the configured currency even when the wallet row says otherwise', async () => {
    // The reporter's case: currency set to AUD, wallet minted as EUR.
    mockFinance({ uiFlagCurrency: 'AUD', walletCurrency: 'EUR' });

    render(<FinancePage />);

    await waitFor(() => expect(screen.getByText('$12.34')).toBeInTheDocument());
    expect(screen.queryByText('€12.34')).not.toBeInTheDocument();
  });

  it('falls back to USD, not EUR, when the install exposes no currency', async () => {
    // Matches AppSettings.currency's default and every other page's fallback.
    mockFinance({ walletCurrency: 'EUR' });

    render(<FinancePage />);

    await waitFor(() => expect(screen.getByText('$12.34')).toBeInTheDocument());
  });
});
