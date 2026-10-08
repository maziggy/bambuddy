/** Tests for provider selection and OctoEverywhere failure detection settings. */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FailureDetectionSettings } from '../../components/FailureDetectionSettings';
import { setAuthToken } from '../../api/client';

const baseSettings = {
  obico_enabled: false,
  obico_ml_url: '',
  obico_ml_token: '',
  obico_sensitivity: 'medium',
  obico_action: 'notify',
  obico_poll_interval: 10,
  obico_enabled_printers: '',
  octoeverywhere_enabled: true,
  octoeverywhere_api_key: '',
  octoeverywhere_api_key_configured: true,
  octoeverywhere_confidence: 'medium',
  octoeverywhere_action: 'notify',
  octoeverywhere_enabled_printers: '',
  octoeverywhere_poll_interval: 20,
};

const baseStatus = {
  enabled: true,
  api_key_configured: true,
  confidence: 'medium',
  action: 'notify',
  poll_interval: 20,
  is_running: true,
  last_error: null,
  last_error_code: null,
  per_printer: {},
  history: [],
  notifications: { configured: true, uncovered_printers: [] },
};

async function waitForForm() {
  const apiKey = await screen.findByLabelText('Gadget API key');
  await waitFor(() => expect(apiKey).not.toBeDisabled());
  return apiKey;
}

describe('OctoEverywhere settings', () => {
  let savedSettings: Record<string, unknown>;
  let updates: Record<string, unknown>[];
  let storedApiKey: string;

  const saveSettings = (update: Record<string, unknown>) => {
    updates.push(update);
    if (typeof update.octoeverywhere_api_key === 'string') storedApiKey = update.octoeverywhere_api_key;
    savedSettings = {
      ...savedSettings,
      ...update,
      octoeverywhere_api_key: '',
      octoeverywhere_api_key_configured: !!storedApiKey,
    };
    if (update.octoeverywhere_enabled) savedSettings.obico_enabled = false;
    return savedSettings;
  };

  beforeEach(() => {
    savedSettings = { ...baseSettings };
    updates = [];
    storedApiKey = 'saved-api-key';
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(savedSettings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const update = await request.json() as Record<string, unknown>;
        return HttpResponse.json(saveSettings(update));
      }),
      http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json(baseStatus)),
      http.get('/api/v1/printers/', () => HttpResponse.json([
        { id: 1, name: 'X1 Carbon' },
        { id: 2, name: 'P1S' },
      ])),
    );
  });

  afterEach(() => setAuthToken(null));

  it('opens the enabled provider without returning the saved key to the input', async () => {
    render(<FailureDetectionSettings />);

    const apiKey = await waitForForm();
    expect(apiKey).toHaveAttribute('type', 'password');
    expect(apiKey).toHaveValue('');
    expect(apiKey).toHaveAttribute('placeholder', 'Leave empty to keep current');
    expect(screen.queryByDisplayValue('saved-api-key')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Test' })).not.toBeDisabled();
    expect(screen.getByLabelText('Provider')).toHaveValue('octoeverywhere');
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText('Obico ML API URL')).not.toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Inspection interval' })).toHaveValue('20');
    const confidence = screen.getByLabelText('Confidence');
    expect(confidence).toHaveValue('medium');
    expect(within(confidence).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Lowest (reports sooner, more false positives)',
      'Low',
      'Medium (balanced)',
      'High',
      'Highest (reports later, fewer false positives)',
    ]);
    expect(screen.getByText(/Lower confidence reports issues sooner/)).toBeInTheDocument();
    expect(screen.queryByText(/External URL is not configured/i)).not.toBeInTheDocument();
  });

  it('keeps credential and monitoring controls read-only without settings:update permission', async () => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () => HttpResponse.json({
        id: 2,
        username: 'viewer',
        is_admin: false,
        permissions: ['settings:read', 'printers:read'],
      })),
    );
    render(<FailureDetectionSettings />);

    expect(await screen.findByLabelText('Gadget API key')).toBeDisabled();
    expect(screen.getByRole('switch')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Test' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Inspection interval' })).toBeDisabled();
    expect(updates).toHaveLength(0);
  });

  it('lets an Obico user enter a key before enabling OctoEverywhere', async () => {
    savedSettings = {
      ...baseSettings,
      obico_enabled: true,
      obico_ml_url: 'http://obico:3333',
      octoeverywhere_enabled: false,
      octoeverywhere_api_key: '',
      octoeverywhere_api_key_configured: false,
    };
    storedApiKey = '';
    render(<FailureDetectionSettings />);
    const user = userEvent.setup();
    await screen.findByDisplayValue('http://obico:3333');
    await user.selectOptions(screen.getByLabelText('Provider'), 'octoeverywhere');
    const apiKey = await screen.findByLabelText('Gadget API key');
    await waitFor(() => expect(apiKey).not.toBeDisabled());
    await user.type(apiKey, 'new-api-key');
    await user.click(screen.getByRole('switch'));

    await waitFor(() => expect(updates.at(-1)).toMatchObject({
      octoeverywhere_enabled: true,
      octoeverywhere_api_key: 'new-api-key',
      octoeverywhere_confidence: 'medium',
      octoeverywhere_action: 'notify',
      octoeverywhere_enabled_printers: '',
    }));
    expect(savedSettings.obico_enabled).toBe(false);
  });

  it('keeps the OctoEverywhere panel selected after disabling detection', async () => {
    render(<FailureDetectionSettings />);
    await waitForForm();
    await userEvent.click(screen.getByRole('switch'));

    await waitFor(() => expect(savedSettings.octoeverywhere_enabled).toBe(false));
    expect(screen.getByLabelText('Provider')).toHaveValue('octoeverywhere');
    expect(screen.getByLabelText('Gadget API key')).toHaveValue('');
    expect(updates.at(-1)).not.toHaveProperty('octoeverywhere_api_key');
    expect(storedApiKey).toBe('saved-api-key');
  });

  it('tests a saved key without retrieving or resending it', async () => {
    let tested: unknown;
    server.use(
      http.post('/api/v1/octoeverywhere/test-connection', async ({ request }) => {
        tested = await request.json();
        return HttpResponse.json({ ok: true, status_code: 200, error: null });
      }),
    );
    render(<FailureDetectionSettings />);
    await waitForForm();
    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    await screen.findByText('OctoEverywhere Gadget API key verification successful!');
    expect(tested).toEqual({ confidence: 'medium' });
    expect(updates).toHaveLength(0);
    expect(storedApiKey).toBe('saved-api-key');
  });

  it('preserves the saved key when a replacement edit is cleared before saving', async () => {
    render(<FailureDetectionSettings />);
    const apiKey = await waitForForm();
    const user = userEvent.setup();
    await user.type(apiKey, 'unsaved-replacement');
    await user.clear(apiKey);
    await user.selectOptions(screen.getByLabelText('Confidence'), 'high');

    await waitFor(() => expect(updates.at(-1)?.octoeverywhere_confidence).toBe('high'));
    expect(updates.every((update) => !('octoeverywhere_api_key' in update))).toBe(true);
    expect(storedApiKey).toBe('saved-api-key');
  });

  it('does not save a partially typed replacement while the key field is focused', async () => {
    render(<FailureDetectionSettings />);
    const apiKey = await waitForForm();
    const user = userEvent.setup({ delay: 550 });
    await user.type(apiKey, 'new');
    expect(apiKey).toHaveValue('new');
    expect(updates).toHaveLength(0);
    await user.tab();

    await screen.findByText('Settings saved');
    expect(storedApiKey).toBe('new');
    expect(apiKey).toHaveValue('new');
  }, 10000);

  it('keeps a pasted key in the field after it auto-saves until the page is reopened', async () => {
    let tested = false;
    server.use(
      http.post('/api/v1/octoeverywhere/test-connection', () => {
        tested = true;
        return HttpResponse.json({ ok: true, status_code: 200, error: null });
      }),
    );
    const { unmount } = render(<FailureDetectionSettings />);
    const apiKey = await waitForForm();
    const user = userEvent.setup();
    await user.click(apiKey);
    await user.paste('pasted-api-key');
    await user.click(document.body);

    await screen.findByText('Settings saved');
    expect(storedApiKey).toBe('pasted-api-key');
    expect(apiKey).toHaveValue('pasted-api-key');

    // The saved key is not an unsaved change, so testing it doesn't save again.
    await user.click(screen.getByRole('button', { name: 'Test' }));
    await screen.findByText('OctoEverywhere Gadget API key verification successful!');
    expect(tested).toBe(true);
    expect(updates).toHaveLength(1);
    expect(apiKey).toHaveValue('pasted-api-key');

    unmount();
    render(<FailureDetectionSettings />);
    const reopened = await waitForForm();
    expect(reopened).toHaveValue('');
    expect(reopened).toHaveAttribute('placeholder', 'Leave empty to keep current');
  });

  it('updates the 5–30 second inspection interval immediately and persists its value', async () => {
    const { unmount } = render(<FailureDetectionSettings />);
    await waitForForm();
    const interval = screen.getByRole('slider', { name: 'Inspection interval' });
    expect(interval).toHaveAttribute('min', '5');
    expect(interval).toHaveAttribute('max', '30');
    expect(interval).toHaveAttribute('step', '1');
    expect(interval).toHaveValue('20');
    expect(screen.getByText('20 seconds')).toBeInTheDocument();

    fireEvent.change(interval, { target: { value: '5' } });
    expect(interval).toHaveAttribute('aria-valuetext', '5 seconds');
    expect(screen.getByText('5 seconds')).toBeInTheDocument();
    expect(updates).toHaveLength(0); // The value changes before the debounced save.
    await waitFor(() => expect(updates.at(-1)?.octoeverywhere_poll_interval).toBe(5));

    await waitFor(() => expect(interval).not.toBeDisabled());
    fireEvent.change(interval, { target: { value: '30' } });
    expect(screen.getByText('30 seconds')).toBeInTheDocument();
    await waitFor(() => expect(updates.at(-1)?.octoeverywhere_poll_interval).toBe(30));
    expect(updates.every((update) => !('octoeverywhere_api_key' in update))).toBe(true);

    unmount();
    render(<FailureDetectionSettings />);
    await waitForForm();
    expect(screen.getByRole('slider', { name: 'Inspection interval' })).toHaveValue('30');
    expect(screen.getByText('30 seconds')).toBeInTheDocument();
  });

  it('keeps setup focused on detection without pricing, billing, or usage limits', async () => {
    const { container } = render(<FailureDetectionSettings />);
    await waitForForm();

    expect(screen.getByRole('link', { name: 'Get your Gadget API key' })).toHaveAttribute('href', 'https://octoeverywhere.com/gadgetapi');
    expect(screen.getByRole('link', { name: 'Privacy policy' })).toHaveAttribute('href', 'https://octoeverywhere.com/privacy#gadget-developer-api');
    expect(container).not.toHaveTextContent(/printer.hours|allowance|charges|paid usage|calls per hour|pricing|billing|usage limit|account limit/i);
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
  });

  it('shows the localized usage-limit notice after a test and clears it after a successful retry', async () => {
    let limitReached = true;
    server.use(
      http.post('/api/v1/octoeverywhere/test-connection', () => HttpResponse.json(limitReached
        ? { ok: false, status_code: 402, error: 'Remote error text', error_code: 'OE_FREE_USAGE_LIMIT_REACHED' }
        : { ok: true, status_code: 200, error: null, error_code: null })),
    );
    render(<FailureDetectionSettings />);
    await waitForForm();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Test' }));

    expect(await screen.findByText('Usage limit reached.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Set up billing to continue' })).toHaveAttribute('href', 'https://octoeverywhere.com/gadgetapi');
    expect(screen.queryByText('Remote error text')).not.toBeInTheDocument();

    limitReached = false;
    await user.click(screen.getByRole('button', { name: 'Test' }));
    await screen.findByText('OctoEverywhere Gadget API key verification successful!');
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Usage limit reached.')).not.toBeInTheDocument();
  });

  it.each(['service', 'printer'])('localizes a %s usage-limit code and removes the notice after recovery', async (source) => {
    let limitReached = true;
    server.use(
      http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json({
        ...baseStatus,
        ...(limitReached && source === 'service' ? {
          last_error: 'Remote service error',
          last_error_code: 'OE_FREE_USAGE_LIMIT_REACHED',
        } : {}),
        ...(limitReached && source === 'printer' ? {
          per_printer: {
            '1': { class: 'error', print_quality: null, frame_count: 0, error: 'Remote printer error', error_code: 'OE_FREE_USAGE_LIMIT_REACHED' },
          },
        } : {}),
      })),
    );
    render(<FailureDetectionSettings />);

    expect(await screen.findByText('Usage limit reached.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Set up billing to continue' })).toHaveAttribute('href', 'https://octoeverywhere.com/gadgetapi');
    expect(screen.queryByText(/Remote (service|printer) error/)).not.toBeInTheDocument();

    limitReached = false;
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(screen.queryByText('Usage limit reached.')).not.toBeInTheDocument());
    expect(screen.queryByRole('link', { name: /set up billing/i })).not.toBeInTheDocument();
  });

  it.each(['lowest', 'highest'] as const)('saves edited credentials and %s confidence before testing', async (confidence) => {
    const calls: string[] = [];
    let tested: unknown;
    server.use(
      http.put('/api/v1/settings/', async ({ request }) => {
        calls.push('save');
        return HttpResponse.json(saveSettings(await request.json() as Record<string, unknown>));
      }),
      http.post('/api/v1/octoeverywhere/test-connection', async ({ request }) => {
        calls.push('test');
        tested = await request.json();
        return HttpResponse.json({ ok: true, status_code: 200, error: null });
      }),
    );
    render(<FailureDetectionSettings />);
    const user = userEvent.setup();
    const apiKey = await waitForForm();
    await user.clear(apiKey);
    await user.type(apiKey, 'replacement-key');
    await user.selectOptions(screen.getByLabelText('Confidence'), confidence);
    await user.click(screen.getByRole('button', { name: 'Test' }));

    expect(await screen.findByText('OctoEverywhere Gadget API key verification successful!')).toBeInTheDocument();
    expect(calls[0]).toBe('save');
    expect(calls.indexOf('test')).toBeGreaterThan(calls.indexOf('save'));
    expect(tested).toEqual({ confidence });
    expect(savedSettings.octoeverywhere_confidence).toBe(confidence);
    expect(storedApiKey).toBe('replacement-key');
    expect(apiKey).toHaveValue('replacement-key');
  });

  it('does not report a successful connection when settings could not be saved', async () => {
    let tested = false;
    server.use(
      http.put('/api/v1/settings/', () => HttpResponse.json({ detail: 'Cannot save settings' }, { status: 500 })),
      http.post('/api/v1/octoeverywhere/test-connection', () => {
        tested = true;
        return HttpResponse.json({ ok: true, status_code: 200, error: null });
      }),
    );
    render(<FailureDetectionSettings />);
    const user = userEvent.setup();
    const apiKey = await waitForForm();
    await user.type(apiKey, '-changed');
    await user.click(screen.getByRole('button', { name: 'Test' }));

    expect((await screen.findAllByText('Cannot save settings')).length).toBeGreaterThan(0);
    expect(tested).toBe(false);
    expect(screen.queryByText('OctoEverywhere Gadget API key verification successful!')).not.toBeInTheDocument();
  });

  it('shows rejected credentials without changing the saved key', async () => {
    server.use(
      http.post('/api/v1/octoeverywhere/test-connection', () => HttpResponse.json({
        ok: false,
        status_code: 401,
        error: 'OctoEverywhere rejected the Gadget API key.',
      })),
    );
    render(<FailureDetectionSettings />);
    const user = userEvent.setup();
    await waitForForm();
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('OctoEverywhere rejected the Gadget API key.')).toBeInTheDocument();

    expect(updates).toHaveLength(0);
    expect(storedApiKey).toBe('saved-api-key');
  });

  it('persists printer subsets and represents monitor-all as an empty string', async () => {
    render(<FailureDetectionSettings />);
    const user = userEvent.setup();
    await waitForForm();
    await user.click(screen.getByRole('checkbox', { name: /monitor all/i }));
    await user.click(screen.getByRole('checkbox', { name: 'P1S' }));

    await waitFor(() => expect(updates.at(-1)?.octoeverywhere_enabled_printers).toBe('[1]'));
    await user.click(screen.getByRole('checkbox', { name: /monitor all/i }));
    await waitFor(() => expect(updates.at(-1)?.octoeverywhere_enabled_printers).toBe(''));
  });

  it('displays quality out of ten and keeps camera failures distinct from safe verdicts', async () => {
    server.use(
      http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json({
        ...baseStatus,
        per_printer: {
          '1': { class: 'safe', print_quality: 9, frame_count: 20, error: null },
          '2': { class: 'error', print_quality: null, frame_count: 0, error: 'Camera unavailable' },
        },
        history: [{ timestamp: '2026-09-15T10:00:00Z', printer_id: 1, class: 'warning', print_quality: 5 }],
      })),
    );
    render(<FailureDetectionSettings />);

    expect(await screen.findByText('Print quality: 9/10')).toBeInTheDocument();
    expect(screen.getByText('Warning 5/10')).toBeInTheDocument();
    expect(screen.getByText('Not checking')).toBeInTheDocument();
    expect(screen.getByText('Camera unavailable')).toBeInTheDocument();
    expect(screen.getAllByText('Safe')).toHaveLength(1);
    expect(screen.queryByText('Print quality: 0/10')).not.toBeInTheDocument();
  });

  it('links to notification setup even when the selected action pauses the printer', async () => {
    savedSettings.octoeverywhere_action = 'pause';
    server.use(
      http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json({
        ...baseStatus,
        notifications: { configured: false, uncovered_printers: [1, 2] },
      })),
    );
    render(<FailureDetectionSettings />);

    expect(await screen.findByText(/Alerts are not configured. Enable AI Failure Detection/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Configure notifications' })).toHaveAttribute('href', '/settings?tab=notifications');
  });

  it('identifies monitored printers not covered by notification subscriptions', async () => {
    server.use(
      http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json({
        ...baseStatus,
        notifications: { configured: true, uncovered_printers: [2] },
      })),
    );
    render(<FailureDetectionSettings />);

    expect(await screen.findByText('Alerts are not configured for: P1S.')).toBeInTheDocument();
  });

  it.each([
    ['disabled despite a running scheduler', { enabled: false }, 'Disabled'],
    ['missing credentials', { api_key_configured: false }, 'Gadget API key required'],
    ['stopped service', { is_running: false }, 'Service unavailable'],
    ['no results yet', {}, 'Waiting for the first result'],
    ['a pending inference', { per_printer: { '1': { class: 'unknown', print_quality: null, frame_count: 0 } } }, 'Waiting for the first result'],
    ['a camera error', { per_printer: { '1': { class: 'error', print_quality: null, frame_count: 0 } } }, 'Monitoring needs attention'],
    ['an observed result', { per_printer: { '1': { class: 'safe', print_quality: 9, frame_count: 1 } } }, 'Detection results available'],
  ])('reports monitoring readiness for %s', async (_name, status, message) => {
    server.use(http.get('/api/v1/octoeverywhere/status', () => HttpResponse.json({ ...baseStatus, ...status })));
    render(<FailureDetectionSettings />);

    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});
