/**
 * A saved SMTP or LDAP bind password only goes to the server it was entered
 * for: changing the server asks for it again, before the save is refused.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { EmailSettings } from '../../components/EmailSettings';
import { LDAPSettings } from '../../components/LDAPSettings';

const AGAIN = /the address changed: enter it again/i;

describe('saved server passwords', () => {
  beforeEach(() => {
    server.use(
      http.get('*/api/v1/auth/smtp', () =>
        HttpResponse.json({
          smtp_host: 'smtp.example',
          smtp_port: 587,
          smtp_username: 'u',
          smtp_password: null,
          smtp_security: 'starttls',
          smtp_auth_enabled: true,
          smtp_from_email: 'a@example.com',
          smtp_from_name: 'BamBuddy',
        }),
      ),
      http.get('*/api/v1/auth/advanced-auth/status', () => HttpResponse.json({ advanced_auth_enabled: false, smtp_configured: true })),
      http.get('*/api/v1/settings/', () =>
        HttpResponse.json({
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
        }),
      ),
      http.get('*/api/v1/auth/ldap/status', () => HttpResponse.json({ ldap_enabled: false, ldap_configured: true })),
      http.get('*/api/v1/groups/', () => HttpResponse.json([])),
    );
  });

  it('asks for the SMTP password again when the server changes', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    const host = await screen.findByDisplayValue('smtp.example');
    expect(screen.queryByText(AGAIN)).not.toBeInTheDocument();
    await user.clear(host);
    await user.type(host, 'smtp.elsewhere.example');

    expect(screen.getByText(AGAIN)).toBeInTheDocument();
  });

  it('asks for the LDAP bind password again when the server changes', async () => {
    const user = userEvent.setup();
    render(<LDAPSettings />);

    const url = await screen.findByDisplayValue('ldaps://ldap.example:636');
    expect(screen.queryByText(AGAIN)).not.toBeInTheDocument();
    await user.clear(url);
    await user.type(url, 'ldap://ldap.elsewhere.example');

    expect(screen.getByText(AGAIN)).toBeInTheDocument();
  });
});
