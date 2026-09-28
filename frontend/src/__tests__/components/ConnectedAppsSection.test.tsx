/**
 * Settings → API Keys → Connected apps.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { api, type ConnectedApp } from '../../api/client';

const mockUseAuth = { authEnabled: true };
vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { ConnectedAppsSection } from '../../components/ConnectedAppsSection';

function app(overrides: Partial<ConnectedApp> = {}): ConnectedApp {
  return {
    id: 1,
    name: 'Order Desk',
    client_id: 'bba_123',
    redirect_uri: 'http://orders.local:8090/auth/callback',
    enabled: true,
    created_at: '2026-09-26T10:00:00Z',
    last_used_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockUseAuth.authEnabled = true;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ConnectedAppsSection', () => {
  it('lists registered apps by client ID, never with a secret', async () => {
    vi.spyOn(api, 'listConnectedApps').mockResolvedValue([app()]);

    render(<ConnectedAppsSection />);

    expect(await screen.findByText('Order Desk')).toBeInTheDocument();
    expect(screen.getByText('bba_123')).toBeInTheDocument();
    expect(screen.queryByText(/bbs_/)).not.toBeInTheDocument();
  });

  it('shows the secret once after adding an app', async () => {
    vi.spyOn(api, 'listConnectedApps').mockResolvedValue([]);
    const create = vi
      .spyOn(api, 'createConnectedApp')
      .mockResolvedValue(app({ client_secret: 'bbs_only_once' }));

    render(<ConnectedAppsSection />);
    await screen.findByText('No connected apps yet.');

    await userEvent.type(screen.getByLabelText('App name'), 'Order Desk');
    await userEvent.type(screen.getByLabelText('Callback URL'), 'http://orders.local:8090/auth/callback');
    await userEvent.click(screen.getByRole('button', { name: 'Add app' }));

    expect(create).toHaveBeenCalledWith({
      name: 'Order Desk',
      redirect_uri: 'http://orders.local:8090/auth/callback',
    });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('bbs_only_once')).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: "I've saved it" }));
    await waitFor(() => expect(screen.queryByText('bbs_only_once')).not.toBeInTheDocument());
  });

  it('asks before replacing the secret', async () => {
    vi.spyOn(api, 'listConnectedApps').mockResolvedValue([app()]);
    const rotate = vi.spyOn(api, 'rotateConnectedAppSecret').mockResolvedValue(app({ client_secret: 'bbs_new' }));

    render(<ConnectedAppsSection />);
    await userEvent.click(await screen.findByRole('button', { name: 'New secret' }));
    expect(rotate).not.toHaveBeenCalled();

    const confirm = screen.getByRole('dialog');
    await userEvent.click(within(confirm).getByRole('button', { name: 'New secret' }));
    expect(rotate).toHaveBeenCalledWith(1);
    expect(await screen.findByText('bbs_new')).toBeInTheDocument();
  });

  it('explains that authentication has to be on first', async () => {
    mockUseAuth.authEnabled = false;
    const list = vi.spyOn(api, 'listConnectedApps');

    render(<ConnectedAppsSection />);

    expect(screen.getByText(/need Bambuddy authentication/)).toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
  });
});
