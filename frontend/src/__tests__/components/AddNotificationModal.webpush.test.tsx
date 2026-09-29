import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AddNotificationModal } from '../../components/AddNotificationModal';
import { api } from '../../api/client';
import type { NotificationProvider } from '../../api/client';
import { prepareWebPush, subscribeWebPush, supportsWebPush } from '../../utils/webPush';

vi.mock('../../utils/webPush', () => ({
  prepareWebPush: vi.fn(), subscribeWebPush: vi.fn(), supportsWebPush: vi.fn(),
}));

const subscription = { endpoint: 'https://fcm.googleapis.com/test-only', keys: { auth: 'test', p256dh: 'test' } };

beforeEach(() => {
  vi.mocked(supportsWebPush).mockReturnValue(true);
  vi.mocked(prepareWebPush).mockResolvedValue({} as ServiceWorkerRegistration);
  vi.mocked(subscribeWebPush).mockResolvedValue(subscription);
  vi.spyOn(api, 'getWebPushPublicKey').mockResolvedValue({ public_key: 'test-public-key' });
  vi.stubGlobal('Notification', { requestPermission: vi.fn().mockResolvedValue('granted') });
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

async function selectPush() {
  const user = userEvent.setup();
  render(<AddNotificationModal onClose={() => {}} />);
  await user.selectOptions(screen.getAllByRole('combobox')[0], 'webpush');
  return user;
}

describe('Web Push in the real notification provider form', () => {
  it('keeps Email first and selected by default, appending Push after the existing providers', () => {
    render(<AddNotificationModal onClose={() => {}} />);
    const providerSelect = screen.getAllByRole('combobox')[0];
    expect(providerSelect).toHaveValue('email');
    expect(within(providerSelect).getAllByRole('option').map(option => option.getAttribute('value'))).toEqual([
      'email', 'telegram', 'discord', 'ntfy', 'pushover', 'bark', 'callmebot', 'webhook', 'homeassistant', 'webpush',
    ]);
  });

  it('offers Push alongside existing providers without requesting permission on selection', async () => {
    await selectPush();
    expect(screen.getByRole('option', { name: 'Push (browser)' })).toBeInTheDocument();
    expect(screen.getByText('Printer Filter')).toBeInTheDocument();
    expect(screen.getByText('Notification Events')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Test Configuration' })).toBeDisabled();
    expect(Notification.requestPermission).not.toHaveBeenCalled();
  });

  it('enrolls on click and saves through the existing create endpoint', async () => {
    const create = vi.spyOn(api, 'createNotificationProvider').mockResolvedValue({ id: 42 } as NotificationProvider);
    const user = await selectPush();
    const enable = screen.getByRole('button', { name: 'Enable notifications on this device' });
    await waitFor(() => expect(enable).toBeEnabled());
    await user.click(enable);
    expect(Notification.requestPermission).toHaveBeenCalledOnce();
    expect(subscribeWebPush).toHaveBeenCalledOnce();
    expect(screen.getByText('Device ready. Save to receive event notifications.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Enable notifications on this device' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Replace with this device' })).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('My Notifications'), 'My phone');
    await user.click(screen.getByRole('button', { name: /^Add$/ }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      name: 'My phone', provider_type: 'webpush', config: { subscription },
      on_print_complete: true, printer_id: null,
    })));
  });

  it('keeps test disabled when permission is denied', async () => {
    vi.mocked(Notification.requestPermission).mockResolvedValue('denied');
    const user = await selectPush();
    const enable = screen.getByRole('button', { name: 'Enable notifications on this device' });
    await waitFor(() => expect(enable).toBeEnabled());
    await user.click(enable);
    expect(await screen.findByText(/Notification permission was not granted/)).toBeInTheDocument();
    expect(subscribeWebPush).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Test Configuration' })).toBeDisabled();
  });

  it('explains unsupported browsers and does not attempt enrollment', async () => {
    vi.mocked(supportsWebPush).mockReturnValue(false);
    await selectPush();
    expect(screen.getByText('Use HTTPS and a browser that supports Web Push.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Enable notifications on this device' })).toBeDisabled();
    expect(api.getWebPushPublicKey).not.toHaveBeenCalled();
  });

  it('tests a saved device without re-enrolling the browser that edits it', async () => {
    const test = vi.spyOn(api, 'testNotificationProvider').mockResolvedValue({ success: true, message: 'Accepted' });
    const user = userEvent.setup();
    render(<AddNotificationModal provider={{ id: 5, name: 'Phone', provider_type: 'webpush',
      config: { registered: true } } as NotificationProvider} onClose={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Enable notifications on this device' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Replace with this device' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Test Configuration' }));
    await waitFor(() => expect(test).toHaveBeenCalledWith(5));
    expect(Notification.requestPermission).not.toHaveBeenCalled();
    expect(subscribeWebPush).not.toHaveBeenCalled();
  });

  it('replaces a saved destination only after explicit enrollment and saving', async () => {
    const update = vi.spyOn(api, 'updateNotificationProvider').mockResolvedValue({ id: 5 } as NotificationProvider);
    const test = vi.spyOn(api, 'testNotificationConfig').mockResolvedValue({ success: true, message: 'Accepted' });
    const user = userEvent.setup();
    render(<AddNotificationModal provider={{ id: 5, name: 'Phone', provider_type: 'webpush',
      config: { registered: true } } as NotificationProvider} onClose={() => {}} />);
    const replace = screen.getByRole('button', { name: 'Replace with this device' });
    await waitFor(() => expect(replace).toBeEnabled());
    await user.click(replace);
    expect(await screen.findByText('Device ready. Save to receive event notifications.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Replace with this device' })).not.toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Test Configuration' }));
    await waitFor(() => expect(test).toHaveBeenCalledWith({ provider_type: 'webpush', config: { subscription } }));
    await user.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(5, expect.objectContaining({ config: { subscription } })));
  });

  it('offers first-time enrollment when a saved subscription has expired', async () => {
    render(<AddNotificationModal provider={{ id: 5, name: 'Phone', provider_type: 'webpush',
      config: { registered: false } } as NotificationProvider} onClose={() => {}} />);
    expect(screen.getByRole('button', { name: 'Enable notifications on this device' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Replace with this device' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Test Configuration' })).toBeDisabled();
  });
});
