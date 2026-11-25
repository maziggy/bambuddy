/**
 * First-run setup: the request that creates the admin account (or turns
 * authentication off) is exactly what the form says, and invalid input
 * sends nothing.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { api } from '../../api/client';
import { SetupPage } from '../../pages/SetupPage';

describe('SetupPage wiring', () => {
  let setup: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    setup = vi
      .spyOn(api, 'setupAuth')
      .mockResolvedValue({ auth_enabled: true, admin_created: true } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('with auth enabled sends auth_enabled:true plus the typed admin credentials', async () => {
    const user = userEvent.setup();
    render(<SetupPage />);

    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.type(screen.getByLabelText(/admin username/i), 'admin-test');
    await user.type(screen.getByLabelText(/admin password/i), 'test-password-1');
    await user.type(screen.getByLabelText(/^confirm password/i), 'test-password-1');
    await user.click(screen.getByRole('button', { name: /complete setup/i }));

    await waitFor(() => expect(setup).toHaveBeenCalledTimes(1));
    expect(setup).toHaveBeenCalledWith({
      auth_enabled: true,
      admin_username: 'admin-test',
      admin_password: 'test-password-1',
    });
  });

  it('mismatched passwords send nothing and show an error', async () => {
    const user = userEvent.setup();
    render(<SetupPage />);

    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.type(screen.getByLabelText(/admin username/i), 'admin-test');
    await user.type(screen.getByLabelText(/admin password/i), 'test-password-1');
    await user.type(screen.getByLabelText(/^confirm password/i), 'test-password-2');
    await user.click(screen.getByRole('button', { name: /complete setup/i }));

    expect(await screen.findByText(/passwords do not match/i)).toBeInTheDocument();
    expect(setup).not.toHaveBeenCalled();
  });

  it('a username without a password sends nothing', async () => {
    const user = userEvent.setup();
    render(<SetupPage />);

    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.type(screen.getByLabelText(/admin username/i), 'admin-test');
    await user.click(screen.getByRole('button', { name: /complete setup/i }));

    expect(await screen.findByText(/please enter both admin username and password/i)).toBeInTheDocument();
    expect(setup).not.toHaveBeenCalled();
  });

  it('enabled with both fields empty sends auth_enabled:true and no credentials (use existing admins)', async () => {
    const user = userEvent.setup();
    render(<SetupPage />);

    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.click(screen.getByRole('button', { name: /complete setup/i }));

    await waitFor(() => expect(setup).toHaveBeenCalledTimes(1));
    expect(setup).toHaveBeenCalledWith({
      auth_enabled: true,
      admin_username: '',
      admin_password: '',
    });
  });

  it('skipping auth sends auth_enabled:false and never the typed credentials', async () => {
    setup.mockResolvedValue({ auth_enabled: false, admin_created: false } as never);
    const user = userEvent.setup();
    render(<SetupPage />);

    // Type credentials, then untick: they must not leave the browser
    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.type(screen.getByLabelText(/admin username/i), 'admin-test');
    await user.click(screen.getByLabelText(/enable authentication/i));
    await user.click(screen.getByRole('button', { name: /complete setup/i }));

    await waitFor(() => expect(setup).toHaveBeenCalledTimes(1));
    expect(setup).toHaveBeenCalledWith({
      auth_enabled: false,
      admin_username: undefined,
      admin_password: undefined,
    });
  });
});
