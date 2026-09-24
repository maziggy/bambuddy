/**
 * Tests for the OIDC group-sync form (#3107): the row-based mapping editor,
 * the orphaned-row flagging for deleted groups, and the summary card's
 * Group Sync line. The scope-mismatch warning was removed in review, and
 * one test pins that it stays gone.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { OIDCProviderSettings } from '../../components/OIDCProviderSettings';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const baseProvider = {
  id: 7,
  name: 'GroupSyncIdP',
  issuer_url: 'https://gs.example.com',
  client_id: 'gs-client',
  scopes: 'openid email profile',
  is_enabled: true,
  auto_create_users: false,
  auto_link_existing_accounts: false,
  email_claim: 'email',
  require_email_verified: true,
  group_claim: 'groups',
  group_mapping: {},
  icon_url: null,
  has_icon: false,
  default_group_id: null,
  is_autologin: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const mockGroups = [
  { id: 1, name: 'Administrators', description: '', permissions: [], is_system: true },
  { id: 2, name: 'Operators', description: '', permissions: [], is_system: true },
];

let savedPayload: Record<string, unknown> | null = null;

function mockCreate() {
  server.use(
    http.post('/api/v1/auth/oidc/providers', async ({ request }) => {
      savedPayload = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...baseProvider, id: 99, ...savedPayload }, { status: 201 });
    })
  );
}

beforeEach(() => {
  savedPayload = null;
  server.use(
    http.get('/api/v1/auth/oidc/providers/all', () =>
      HttpResponse.json([{ ...baseProvider }])
    ),
    http.get('/api/v1/groups/', () => HttpResponse.json(mockGroups))
  );
});

async function openCreateForm() {
  await waitFor(() => {
    expect(screen.getAllByRole('button', { name: /Add Provider/i })[0]).toBeInTheDocument();
  });
  await userEvent.click(screen.getAllByRole('button', { name: /Add Provider/i })[0]);
}

async function fillRequiredFields() {
  // The form's labels are not wired with htmlFor, so drive by placeholder
  // (same approach the existing OIDCProviderSettings test uses).
  await userEvent.type(screen.getByPlaceholderText('Google'), 'GS-IdP');
  await userEvent.type(screen.getByPlaceholderText('https://accounts.google.com'), 'https://gs.example.com');
  await userEvent.type(screen.getByPlaceholderText('your-client-id'), 'gs-client');
  await userEvent.type(screen.getByPlaceholderText(/new secret/i), 'gs-secret');
}

describe('OIDC group sync form', () => {
  it('adds a mapping row, fills the IdP name and picks a Bambuddy group', async () => {
    mockCreate();
    render(<OIDCProviderSettings />);
    await openCreateForm();
    await fillRequiredFields();

    await userEvent.click(screen.getByRole('button', { name: /Add Mapping/i }));
    const idpInput = screen.getByPlaceholderText(/Identity provider group name/i);
    await userEvent.type(idpInput, 'idp-ops');

    const selects = screen.getAllByDisplayValue(/Select Bambuddy group/i);
    await userEvent.selectOptions(selects[0], 'Operators');

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() => {
      expect(savedPayload).not.toBeNull();
    });
    expect(savedPayload!.group_mapping).toEqual({ 'idp-ops': 'Operators' });
    expect(savedPayload!.group_claim).toBe('groups');
  });

  it('saves an empty mapping as {} when no rows are added', async () => {
    mockCreate();
    render(<OIDCProviderSettings />);
    await openCreateForm();
    await fillRequiredFields();

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() => {
      expect(savedPayload).not.toBeNull();
    });
    expect(savedPayload!.group_mapping).toEqual({});
  });

  it('removes a row with its trash button and the mapping is not sent', async () => {
    mockCreate();
    render(<OIDCProviderSettings />);
    await openCreateForm();
    await fillRequiredFields();

    await userEvent.click(screen.getByRole('button', { name: /Add Mapping/i }));
    const idpInput = screen.getByPlaceholderText(/Identity provider group name/i);
    await userEvent.type(idpInput, 'idp-ops');

    // The row's trash button is the button inside the same row container
    const row = idpInput.closest('div')?.parentElement;
    const trash = row?.querySelector('button');
    expect(trash).not.toBeNull();
    await userEvent.click(trash!);

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() => {
      expect(savedPayload).not.toBeNull();
    });
    expect(savedPayload!.group_mapping).toEqual({});
  });

  it('shows the stale group as a deleted option when the mapping names a removed group', async () => {
    server.use(
      http.get('/api/v1/auth/oidc/providers/all', () =>
        HttpResponse.json([
          {
            ...baseProvider,
            group_mapping: { 'idp-ops': 'DeletedGroup' },
          },
        ])
      )
    );
    render(<OIDCProviderSettings />);

    // Summary card shows Group Sync on
    await waitFor(() => {
      expect(screen.getByText(/Group Sync/i)).toBeInTheDocument();
    });

    // Open the edit form: the orphaned row must be flagged
    const editButton = await screen.findByTestId('edit-provider-7');
    await userEvent.click(editButton);
    // The orphaned row shows the stale name as a "(deleted)" option AND the
    // standalone warning line; assert on the warning's distinctive text.
    await waitFor(() => {
      expect(screen.getByText(/has been deleted/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/DeletedGroup \(deleted\)/i)).toBeInTheDocument();
  });

  it('shows Group Sync on the summary card only when a mapping exists', async () => {
    render(<OIDCProviderSettings />);
    await waitFor(() => {
      expect(screen.getByText(/Group Sync/i)).toBeInTheDocument();
    });
    // base provider has empty mapping -> the status reads Off, not On
    expect(screen.queryByText(/\(groups\)/)).not.toBeInTheDocument();
  });

  it('does not show a scopes warning on the claim fields (removed in review)', async () => {
    render(<OIDCProviderSettings />);
    await openCreateForm();
    // With stock scopes "openid email profile" and claim "groups", the removed
    // warning must not render anywhere in the form.
    expect(
      screen.queryByText(/won't be returned by your identity provider/i)
    ).not.toBeInTheDocument();
  });
});
