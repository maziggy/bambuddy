/**
 * Wiring tests for FinancePage (admin view): pin the exact billing requests
 * -- wallet deposit / withdraw, transaction edit / delete, cost centre
 * create / update / delete, member add / remove, manual print -- that a
 * cancelled destructive confirm sends nothing, and that the money-moving
 * controls are absent without cost_centers:modify.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FinancePage } from '../../pages/FinancePage';
import { setAuthToken } from '../../api/client';

const centre = (over: Record<string, unknown>) => ({
  id: 20,
  name: 'Lab',
  is_private: false,
  owner_user_id: null,
  is_active: true,
  total_balance: 0,
  total_budget: null,
  monthly_budget: 100,
  budget_mode: 'monthly',
  budget_limit: 100,
  budget_used: 0,
  budget_available: 100,
  can_print: true,
  ...over,
});

const CENTRES = [
  centre({ id: 20, name: 'Lab' }),
  centre({ id: 21, name: 'Shop', monthly_budget: 40, budget_limit: 40, budget_available: 40 }),
  centre({ id: 22, name: 'Alice private', is_private: true, owner_user_id: 3 }),
];

const USERS = [
  { id: 3, username: 'alice' },
  { id: 5, username: 'bob' },
];

const TX = {
  id: 900,
  user_id: 5,
  cost_center_id: 20,
  transaction_type: 'deposit',
  amount: 12.5,
  balance_after: 12.5,
  description: 'Filament refill',
  created_by_user_id: null,
  print_run_id: null,
  print_archive_id: null,
  print_queue_id: null,
  created_at: '2026-10-11T10:00:00',
};

interface Captured {
  method: string;
  path: string;
  body?: unknown;
}

let calls: Captured[];

async function capture(request: Request) {
  const url = new URL(request.url);
  const text = request.method === 'DELETE' ? '' : await request.text();
  calls.push({ method: request.method, path: url.pathname, body: text ? JSON.parse(text) : undefined });
}

const mutations = () => calls.filter((c) => c.method !== 'GET');

beforeEach(() => {
  calls = [];
  setAuthToken(null);
  server.use(
    http.get('*/api/v1/settings/ui-flags', () => HttpResponse.json({ billing_enabled: true, currency: 'USD' })),
    http.get('*/api/v1/finance/me/balance', () =>
      HttpResponse.json({ user_id: 1, balance: 0, currency: 'USD', updated_at: null }),
    ),
    http.get('*/api/v1/finance/me/transactions', () => HttpResponse.json({ items: [], total: 0 })),
    http.get('*/api/v1/finance/transactions', () =>
      HttpResponse.json({ items: [TX], total: 1, limit: 50, offset: 0 }),
    ),
    http.get('*/api/v1/finance/cost-centers/mine', () => HttpResponse.json([])),
    http.get('*/api/v1/finance/cost-centers', () => HttpResponse.json(CENTRES)),
    http.get('*/api/v1/finance/cost-centers/:id', ({ params }) => {
      const id = Number(params.id);
      const c = CENTRES.find((x) => x.id === id);
      return HttpResponse.json({
        ...c,
        members: id === 20 ? [{ id: 1, cost_center_id: 20, user_id: 3, can_print: true }] : [],
      });
    }),
    http.get('*/api/v1/users/slim', () => HttpResponse.json(USERS)),
    http.post('*/api/v1/finance/*', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({});
    }),
    http.patch('*/api/v1/finance/*', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({});
    }),
    http.delete('*/api/v1/finance/*', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ status: 'ok' });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
});

async function openAdmin(user: ReturnType<typeof userEvent.setup>) {
  render(<FinancePage />);
  await user.click(await screen.findByText('Admin view'));
  await screen.findByText('Filament refill');
}

/** The FinanceModal (or ConfirmModal) overlay whose heading is ``title``. */
async function modal(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title });
  return heading.closest('div.fixed') as HTMLElement;
}

/** The cost-centre table row for ``name`` (the one with an Edit button). */
async function centreRow(name: string): Promise<HTMLElement> {
  let row: HTMLElement | undefined;
  await waitFor(() => {
    row = screen
      .getAllByText(name)
      .map((el) => el.closest('tr') as HTMLElement | null)
      .find((tr): tr is HTMLElement => !!tr && !!within(tr).queryByTitle('Edit'));
    expect(row).toBeDefined();
  });
  return row!;
}

describe('FinancePage wiring: wallet adjustment', () => {
  it('Deposit 10.00 to bob posts /finance/users/5/deposit with amount, description and cost centre', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Adjust wallet' }));
    const dlg = await modal('Adjust wallet');
    const [userSelect, typeSelect, centreSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(userSelect, '5');
    expect(typeSelect).toHaveValue('deposit');
    await user.type(within(dlg).getByPlaceholderText('0.00'), '10.00');
    await user.selectOptions(centreSelect, '21');
    await user.type(within(dlg).getByPlaceholderText('Description (optional)'), 'Top up');
    await user.click(within(dlg).getByRole('button', { name: 'Apply adjustment' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/finance/users/5/deposit',
      body: { amount: 10, description: 'Top up', cost_center_id: 21 },
    });
  });

  it('Withdraw posts /finance/users/{id}/withdraw with no cost centre', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Adjust wallet' }));
    const dlg = await modal('Adjust wallet');
    const [userSelect, typeSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(userSelect, '3');
    await user.selectOptions(typeSelect, 'withdraw');
    await user.type(within(dlg).getByPlaceholderText('0.00'), '2.5');
    await user.click(within(dlg).getByRole('button', { name: 'Apply adjustment' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/finance/users/3/withdraw',
      body: { amount: 2.5, cost_center_id: null },
    });
  });

  it('a zero amount sends nothing', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Adjust wallet' }));
    const dlg = await modal('Adjust wallet');
    await user.type(within(dlg).getByPlaceholderText('0.00'), '0');
    await user.click(within(dlg).getByRole('button', { name: 'Apply adjustment' }));

    await screen.findByText('Amount must be greater than zero');
    expect(mutations()).toHaveLength(0);
  });
});

describe('FinancePage wiring: transactions', () => {
  async function txRow(): Promise<HTMLElement> {
    return (await screen.findByText('Filament refill')).closest('tr') as HTMLElement;
  }

  it('edit transaction sends PATCH /finance/transactions/{id} with the edited fields', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    const buttons = within(await txRow()).getAllByRole('button');
    await user.click(buttons[0]); // pencil
    const dlg = await modal('Edit Transaction');
    const [userSelect, centreSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(userSelect, '3');
    await user.selectOptions(centreSelect, '21');
    const amount = within(dlg).getByRole('spinbutton');
    await user.clear(amount);
    await user.type(amount, '7.25');
    const desc = within(dlg).getByRole('textbox');
    await user.clear(desc);
    await user.type(desc, 'Corrected');
    await user.click(within(dlg).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'PATCH',
      path: '/api/v1/finance/transactions/900',
      body: { user_id: 3, cost_center_id: 21, amount: 7.25, description: 'Corrected' },
    });
  });

  // The backend clears the cost centre only for an explicit null
  // (edit_transaction checks model_fields_set); a missing key keeps the old one.
  it('moving a transaction to "No cost center" sends cost_center_id: null', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await txRow()).getAllByRole('button')[0]);
    const dlg = await modal('Edit Transaction');
    const [, centreSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(centreSelect, '');
    await user.click(within(dlg).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0].body).toHaveProperty('cost_center_id', null);
  });

  it('delete transaction -> confirm sends DELETE /finance/transactions/{id}', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await txRow()).getAllByRole('button')[1]); // trash
    const dlg = await modal('Delete transaction');
    await user.click(within(dlg).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/finance/transactions/900' });
  });

  it('delete transaction -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await txRow()).getAllByRole('button')[1]);
    const dlg = await modal('Delete transaction');
    await user.click(within(dlg).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete transaction' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });

  it('manual print posts /finance/transactions/manual with user and cost_center_id', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Add manual print' }));
    const dlg = await modal('Add manual print');
    const [userSelect, centreSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(userSelect, '5');
    await user.selectOptions(centreSelect, '21');
    await user.type(within(dlg).getByPlaceholderText('e.g., 4.00'), '4');
    await user.type(within(dlg).getByPlaceholderText('Description (optional)'), 'Hand print');
    await user.click(within(dlg).getByRole('button', { name: 'Add manual print' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    const call = mutations()[0];
    expect(call.method).toBe('POST');
    expect(call.path).toBe('/api/v1/finance/transactions/manual');
    expect(call.body).toMatchObject({ user_id: 5, cost_center_id: 21, amount: 4, description: 'Hand print' });
  });

  it('manual print without a cost centre sends nothing', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Add manual print' }));
    const dlg = await modal('Add manual print');
    const [userSelect] = within(dlg).getAllByRole('combobox');
    await user.selectOptions(userSelect, '5');
    await user.type(within(dlg).getByPlaceholderText('e.g., 4.00'), '4');
    await user.click(within(dlg).getByRole('button', { name: 'Add manual print' }));

    await screen.findByText('Please select a cost center');
    expect(mutations()).toHaveLength(0);
  });
});

describe('FinancePage wiring: cost centres', () => {
  it('create posts /finance/cost-centers with name and monthly budget', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Create cost center' }));
    const dlg = await modal('Create cost center');
    await user.type(within(dlg).getByPlaceholderText('Name'), '  Robotics ');
    const inputs = within(dlg).getAllByRole('spinbutton');
    await user.type(inputs[inputs.length - 1], '50');
    await user.click(within(dlg).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/finance/cost-centers',
      body: { name: 'Robotics', total_budget: null, monthly_budget: 50, is_active: true },
    });
  });

  it('edit sends the name and budgets PATCHes for that centre id', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await centreRow('Shop')).getByTitle('Edit'));
    const dlg = await modal('Edit cost center');
    const name = within(dlg).getByPlaceholderText('Name');
    await waitFor(() => expect(name).toHaveValue('Shop'));
    await user.clear(name);
    await user.type(name, 'Workshop');
    await user.selectOptions(within(dlg).getByRole('combobox'), 'total');
    const budget = within(dlg).getByRole('spinbutton');
    await user.clear(budget);
    await user.type(budget, '300');
    await user.click(within(dlg).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mutations()).toHaveLength(2));
    expect(mutations()).toEqual([
      { method: 'PATCH', path: '/api/v1/finance/cost-centers/21', body: { name: 'Workshop' } },
      {
        method: 'PATCH',
        path: '/api/v1/finance/cost-centers/21/budgets',
        body: { total_budget: 300, monthly_budget: null },
      },
    ]);
  });

  it('editing a private centre saves its budget without renaming it (#3358)', async () => {
    // As the backend does: a private centre can't be renamed or deactivated.
    server.use(
      http.patch('*/api/v1/finance/cost-centers/22', async ({ request }) => {
        await capture(request);
        return HttpResponse.json(
          { detail: 'Private cost centers cannot be deactivated or renamed; set their budget to 0 to prevent printing' },
          { status: 400 },
        );
      }),
    );
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await centreRow('Alice private')).getByTitle('Edit'));
    const dlg = await modal('Edit cost center');
    await waitFor(() => expect(within(dlg).getByPlaceholderText('Name')).toHaveValue('Alice private'));
    expect(within(dlg).getByPlaceholderText('Name')).toBeDisabled();
    const budget = within(dlg).getByRole('spinbutton');
    await user.clear(budget);
    await user.type(budget, '0');
    await user.click(within(dlg).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.getByText('Cost center updated')).toBeInTheDocument());
    expect(mutations()).toEqual([
      {
        method: 'PATCH',
        path: '/api/v1/finance/cost-centers/22/budgets',
        body: { total_budget: null, monthly_budget: 0 },
      },
    ]);
  });

  it('delete -> confirm sends DELETE for that centre; private centres offer no delete', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    expect(within(await centreRow('Alice private')).queryByTitle('Delete')).toBeNull();
    await user.click(within(await centreRow('Lab')).getByTitle('Delete'));
    const dlg = (await screen.findByText('Delete cost center "Lab"?')).closest('div.fixed') as HTMLElement;
    await user.click(within(dlg).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/finance/cost-centers/20' });
  });

  it('delete -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(within(await centreRow('Lab')).getByTitle('Delete'));
    const dlg = (await screen.findByText('Delete cost center "Lab"?')).closest('div.fixed') as HTMLElement;
    await user.click(within(dlg).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete cost center "Lab"?')).toBeNull());
    expect(mutations()).toHaveLength(0);
  });
});

describe('FinancePage wiring: members', () => {
  it('add member posts the selected centre id and the user id with can_print', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Manage cost center members' }));
    const dlg = await modal('Manage cost center members');
    const [centreSelect, userSelect] = within(dlg).getAllByRole('combobox');
    await waitFor(() => expect(centreSelect).toHaveValue('20'));
    // alice (3) is already a member of Lab, so bob is the only candidate.
    await waitFor(() => expect(userSelect).toHaveValue('5'));
    expect(within(userSelect).queryByRole('option', { name: 'alice' })).toBeNull();
    await user.click(within(dlg).getByRole('checkbox'));
    await user.click(within(dlg).getByRole('button', { name: 'Add member' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/finance/cost-centers/20/members',
      body: { user_id: 5, can_print: false },
    });
  });

  it('add member follows a switched cost centre', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Manage cost center members' }));
    const dlg = await modal('Manage cost center members');
    const [centreSelect, userSelect] = within(dlg).getAllByRole('combobox');
    await waitFor(() => expect(centreSelect).toHaveValue('20'));
    await user.selectOptions(centreSelect, '21');
    await user.selectOptions(userSelect, '3');
    await user.click(within(dlg).getByRole('button', { name: 'Add member' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/finance/cost-centers/21/members',
      body: { user_id: 3, can_print: true },
    });
  });

  it('remove member sends DELETE /finance/cost-centers/{centre}/members/{user}', async () => {
    const user = userEvent.setup();
    await openAdmin(user);

    await user.click(screen.getByRole('button', { name: 'Manage cost center members' }));
    const dlg = await modal('Manage cost center members');
    const memberRow = (await within(dlg).findByText('alice', { selector: 'td' })).closest('tr') as HTMLElement;
    await user.click(within(memberRow).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/finance/cost-centers/20/members/3' });
  });
});

describe('FinancePage wiring: permission gates', () => {
  const signInWith = (permissions: string[]) => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({ id: 1, username: 'viewer', is_admin: false, permissions }),
      ),
    );
  };

  it('without cost_centers:modify there is no wallet, manual-print, members or delete control', async () => {
    signInWith(['cost_centers:read_own', 'cost_centers:read_all', 'users:read_slim']);
    const user = userEvent.setup();
    await openAdmin(user);

    expect(screen.queryByRole('button', { name: 'Adjust wallet' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add manual print' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Manage cost center members' })).toBeNull();
    expect(screen.queryByTitle('Delete')).toBeNull();
  });

  it('with cost_centers:modify and users:read_slim the wallet controls are offered', async () => {
    signInWith(['cost_centers:read_own', 'cost_centers:read_all', 'cost_centers:modify', 'users:read_slim']);
    const user = userEvent.setup();
    await openAdmin(user);

    expect(screen.getByRole('button', { name: 'Adjust wallet' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add manual print' })).toBeInTheDocument();
  });
});
