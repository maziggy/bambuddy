/**
 * Tests for LdapUserPicker (#1298).
 *
 * The picker is rendered inside the user-create modal when LDAP is enabled.
 * It owns its own search + provision mutation; the parent modal just provides
 * the onSuccess callback that closes the modal and toasts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { LdapUserPicker } from '../../components/LdapUserPicker';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    searchLDAPDirectory: vi.fn(),
    provisionLDAPUser: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getSettings: vi.fn().mockResolvedValue({}),
  },
}));

describe('LdapUserPicker', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('does not search until the user types at least 2 characters', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<LdapUserPicker onSuccess={() => {}} />);

    const input = screen.getByPlaceholderText(/type a username/i);
    await user.type(input, 'a');

    // Advance well past the debounce window — a 1-char query must still not fire.
    await vi.advanceTimersByTimeAsync(1000);

    expect(api.searchLDAPDirectory).not.toHaveBeenCalled();
    expect(screen.getByText(/at least 2 characters/i)).toBeInTheDocument();
  });

  it('debounces typing and only sends the final query', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    (api.searchLDAPDirectory as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(<LdapUserPicker onSuccess={() => {}} />);
    const input = screen.getByPlaceholderText(/type a username/i);

    await user.type(input, 'jdoe');
    // After the last keystroke, the 300ms debounce hasn't elapsed yet — verify
    // we haven't fired a request for an intermediate value like 'jd' or 'jdo'.
    expect(api.searchLDAPDirectory).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      expect(api.searchLDAPDirectory).toHaveBeenCalledTimes(1);
      expect(api.searchLDAPDirectory).toHaveBeenCalledWith('jdoe');
    });
  });

  it('renders search results and lets the admin select and provision one', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    (api.searchLDAPDirectory as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        username: 'jdoe',
        email: 'jdoe@example.com',
        display_name: 'John Doe',
        dn: 'cn=John Doe,dc=example,dc=com',
        already_provisioned: false,
      },
    ]);
    (api.provisionLDAPUser as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 42,
      username: 'jdoe',
      auth_source: 'ldap',
      groups: [],
      permissions: [],
      role: 'user',
      is_active: true,
      is_admin: false,
      email: 'jdoe@example.com',
      created_at: '2026-05-15T10:00:00Z',
    });

    const onSuccess = vi.fn();
    render(<LdapUserPicker onSuccess={onSuccess} />);

    await user.type(screen.getByPlaceholderText(/type a username/i), 'jdoe');
    await vi.advanceTimersByTimeAsync(350);

    // Result list renders with the username + display name visible.
    const resultRow = await screen.findByText('jdoe');
    expect(resultRow).toBeInTheDocument();
    expect(screen.getByText(/john doe/i)).toBeInTheDocument();

    await user.click(resultRow);

    // Submit button activates after selection. The label is "Provision user"
    // — match it specifically so we don't accidentally select the "Provisioning..."
    // loading variant.
    const submit = screen.getByRole('button', { name: /^provision user$/i });
    expect(submit).not.toBeDisabled();
    await user.click(submit);

    await waitFor(() => {
      expect(api.provisionLDAPUser).toHaveBeenCalledWith('jdoe');
      expect(onSuccess).toHaveBeenCalledTimes(1);
      expect(onSuccess.mock.calls[0][0].username).toBe('jdoe');
    });
  });

  it('disables already-provisioned rows so admins cannot pick them', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    (api.searchLDAPDirectory as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        username: 'existing',
        email: 'existing@example.com',
        display_name: null,
        dn: 'cn=existing,dc=example,dc=com',
        already_provisioned: true,
      },
    ]);

    render(<LdapUserPicker onSuccess={() => {}} />);
    await user.type(screen.getByPlaceholderText(/type a username/i), 'existing');
    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      expect(screen.getByText(/already provisioned/i)).toBeInTheDocument();
    });

    // The row's <button> is disabled — userEvent.click will throw, so we just
    // assert the disabled attribute is set, which is the contract that drives
    // the cursor + opacity styling.
    const rowButton = screen.getByText('existing').closest('button')!;
    expect(rowButton).toBeDisabled();

    // The submit button stays disabled because there's no selectable row.
    const submit = screen.getByRole('button', { name: /^provision user$/i });
    expect(submit).toBeDisabled();
  });

  it('surfaces provision errors instead of swallowing them', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    (api.searchLDAPDirectory as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        username: 'jdoe',
        email: null,
        display_name: null,
        dn: 'cn=jdoe,dc=example,dc=com',
        already_provisioned: false,
      },
    ]);
    (api.provisionLDAPUser as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('LDAP server unreachable')
    );

    const onSuccess = vi.fn();
    render(<LdapUserPicker onSuccess={onSuccess} />);
    await user.type(screen.getByPlaceholderText(/type a username/i), 'jdoe');
    await vi.advanceTimersByTimeAsync(350);

    await user.click(await screen.findByText('jdoe'));
    await user.click(screen.getByRole('button', { name: /^provision user$/i }));

    await waitFor(() => {
      expect(screen.getByText(/ldap server unreachable/i)).toBeInTheDocument();
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
