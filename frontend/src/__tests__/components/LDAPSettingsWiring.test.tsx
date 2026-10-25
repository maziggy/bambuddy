/**
 * LDAP settings: the save request carries every ldap_* field including the
 * group mapping and default group, the bind password only when it was typed,
 * and the enable toggle / test button hit the right endpoints.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { LDAPSettings } from '../../components/LDAPSettings';

type Captured = { method: string; path: string; body: Record<string, unknown> | null };

let captured: Captured[];
let ldapStatus: { ldap_enabled: boolean; ldap_configured: boolean };

const STORED = {
  ldap_enabled: false,
  ldap_server_url: 'ldaps://ldap.example:636',
  ldap_bind_dn: 'cn=svc,dc=example',
  ldap_bind_password: '',
  ldap_search_base: 'dc=example',
  ldap_user_filter: '(uid={username})',
  ldap_security: 'ldaps',
  ldap_group_mapping: '',
  ldap_auto_provision: false,
  ldap_default_group: '',
};

async function capture(request: Request) {
  const text = await request.text();
  captured.push({
    method: request.method,
    path: new URL(request.url).pathname,
    body: text ? JSON.parse(text) : null,
  });
}

beforeEach(() => {
  captured = [];
  ldapStatus = { ldap_enabled: false, ldap_configured: true };
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/settings/', () => HttpResponse.json(STORED)),
    http.get('*/api/v1/auth/ldap/status', () => HttpResponse.json(ldapStatus)),
    http.get('*/api/v1/groups/', () =>
      HttpResponse.json([
        { id: 1, name: 'Administrators' },
        { id: 2, name: 'Operators' },
      ]),
    ),
    http.put('*/api/v1/settings/', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(STORED);
    }),
    http.post('*/api/v1/auth/ldap/test', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ success: true, message: 'Connected' });
    }),
  );
});

describe('LDAPSettings wiring', () => {
  it('save sends all ldap_* fields with group mapping/default group and no bind password when untouched', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    const base = await screen.findByDisplayValue('dc=example');
    await user.clear(base);
    await user.type(base, 'ou=people,dc=example');

    await user.click(screen.getByRole('button', { name: /advanced/i }));
    await user.click(screen.getByPlaceholderText(/PrintFarm_Admins/));
    await user.paste('{"cn=farm,dc=example":"Operators"}');
    await user.selectOptions(screen.getByRole('combobox'), 'Operators');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0].method).toBe('PUT');
    expect(captured[0].path).toBe('/api/v1/settings/');
    expect(captured[0].body).toEqual({
      ldap_server_url: 'ldaps://ldap.example:636',
      ldap_bind_dn: 'cn=svc,dc=example',
      ldap_search_base: 'ou=people,dc=example',
      ldap_user_filter: '(uid={username})',
      ldap_security: 'ldaps',
      ldap_group_mapping: '{"cn=farm,dc=example":"Operators"}',
      ldap_auto_provision: false,
      ldap_default_group: 'Operators',
    });
    expect(captured[0].body).not.toHaveProperty('ldap_bind_password');
  });

  it('a typed bind password is sent with the save', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    await screen.findByDisplayValue('dc=example');
    await user.type(document.querySelector('input[type="password"]') as HTMLInputElement, 'test-password-1');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0].body).toMatchObject({ ldap_bind_password: 'test-password-1' });
  });

  it('save without a server URL sends nothing', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    await user.clear(await screen.findByDisplayValue('ldaps://ldap.example:636'));
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(/ldap server url is required/i)).toBeInTheDocument();
    expect(captured).toHaveLength(0);
  });

  it('test connection posts the form as it is, unsaved edits included', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    const base = await screen.findByDisplayValue('dc=example');
    await user.clear(base);
    await user.type(base, 'ou=people,dc=example');
    await user.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({
      method: 'POST',
      path: '/api/v1/auth/ldap/test',
      body: {
        ldap_server_url: 'ldaps://ldap.example:636',
        ldap_bind_dn: 'cn=svc,dc=example',
        ldap_bind_password: '',
        ldap_search_base: 'ou=people,dc=example',
        ldap_user_filter: '(uid={username})',
        ldap_security: 'ldaps',
      },
    });
  });

  it('enable sends only {ldap_enabled:true}', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    await screen.findByDisplayValue('dc=example');
    await waitFor(async () => {
      await user.click(screen.getByRole('button', { name: /^enable$/i }));
      expect(captured).toHaveLength(1);
    });
    expect(captured[0]).toEqual({ method: 'PUT', path: '/api/v1/settings/', body: { ldap_enabled: true } });
  });

  it('disable sends {ldap_enabled:false} when LDAP is on', async () => {
    ldapStatus = { ldap_enabled: true, ldap_configured: true };
    const user = userEvent.setup();
    render(<LDAPSettings />);

    await screen.findByText(/ldap authentication is enabled/i);
    await waitFor(async () => {
      await user.click(screen.getByRole('button', { name: /^disable$/i }));
      expect(captured).toHaveLength(1);
    });
    expect(captured[0].body).toEqual({ ldap_enabled: false });
  });

  it('enable sends nothing until LDAP settings are saved', async () => {
    ldapStatus = { ldap_enabled: false, ldap_configured: false };
    const user = userEvent.setup();
    render(<LDAPSettings />);

    await screen.findByDisplayValue('dc=example');
    await waitFor(async () => {
      await user.click(screen.getByRole('button', { name: /^enable$/i }));
      expect(screen.getAllByText(/save ldap settings first/i).length).toBeGreaterThan(0);
    });
    expect(captured).toHaveLength(0);
  });
});
