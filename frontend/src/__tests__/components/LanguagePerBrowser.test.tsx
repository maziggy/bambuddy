/**
 * Interface language per browser (#3623): every user can switch it from the
 * sidebar footer without settings permissions, and it never touches the
 * install-wide `language` setting.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { Layout } from '../../components/Layout';
import { SettingsPage } from '../../pages/SettingsPage';
import { api, getAuthToken, setAuthToken } from '../../api/client';
import i18n from '../../i18n';

const LANGUAGE_KEY = 'bambutrack_language';

const mockAuth = (enabled: boolean, permissions: string[] = []) => {
  server.use(
    http.get('/api/v1/auth/status', () =>
      HttpResponse.json({ auth_enabled: enabled, requires_setup: false }),
    ),
    http.get('/api/v1/auth/me', () =>
      HttpResponse.json({
        id: 2,
        username: 'helper',
        role: 'user',
        is_active: true,
        is_admin: false,
        groups: [{ id: 2, name: 'Helpers' }],
        permissions,
        created_at: '2026-01-01T00:00:00Z',
      }),
    ),
  );
};

describe('language per browser (#3623)', () => {
  let priorToken: string | null = null;
  let settingsWrites = 0;

  beforeEach(async () => {
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    localStorage.clear();
    settingsWrites = 0;
    await i18n.changeLanguage('en');
    vi.mocked(localStorage.setItem).mockClear();
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/version', () => HttpResponse.json({ version: '0.1.6', build: 'test' })),
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ check_updates: false, check_printer_firmware: false }),
      ),
      http.put('/api/v1/settings/', () => {
        settingsWrites += 1;
        return HttpResponse.json({});
      }),
      http.get('/api/v1/settings/ui-flags', () =>
        HttpResponse.json({ check_updates: false, billing_enabled: false, user_notifications_enabled: true, currency: 'EUR' }),
      ),
      http.get('/api/v1/external-links/', () => HttpResponse.json([])),
      http.get('/api/v1/smart-plugs/', () => HttpResponse.json([])),
      http.get('/api/v1/support/debug-logging', () => HttpResponse.json({ enabled: false })),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/pending-uploads/count', () => HttpResponse.json({ count: 0 })),
      http.get('/api/v1/updates/check', () => HttpResponse.json({ update_available: false })),
      http.get('/api/v1/printers/developer-mode-warnings', () => HttpResponse.json([])),
    );
    mockAuth(false);
  });

  afterEach(async () => {
    if (priorToken !== null) setAuthToken(priorToken, 'session');
    priorToken = null;
    vi.restoreAllMocks();
    await i18n.changeLanguage('en');
  });

  const signIn = (permissions: string[]) => {
    mockAuth(true, permissions);
    // Make /settings/ forbidden, as for a user without settings:read.
    server.use(
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ detail: 'Not enough permissions' }, { status: 403 }),
      ),
    );
    priorToken = getAuthToken();
    setAuthToken('test-token', 'session');
  };

  it('offers the picker with auth off and switches only the interface', async () => {
    const updateSettings = vi.spyOn(api, 'updateSettings');
    render(<Layout />);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Language' }))[0]);
    const select = await screen.findByRole('combobox', { name: 'Language' });
    expect(select.tagName).toBe('SELECT');

    fireEvent.change(select, { target: { value: 'de' } });

    await waitFor(() => expect(i18n.language).toBe('de'), { timeout: 15000 });
    expect(updateSettings).not.toHaveBeenCalled();
    expect(settingsWrites).toBe(0);
  });

  it('focuses the select on open, returns focus on Escape, closes on outside click', async () => {
    render(<Layout />);
    const trigger = (await screen.findAllByRole('button', { name: 'Language' }))[0];
    fireEvent.click(trigger);
    const select = await screen.findByRole('combobox', { name: 'Language' });
    expect(select).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Language' })).toBeNull());
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    await screen.findByRole('dialog', { name: 'Language' });
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Language' })).toBeNull());
  });

  it('shows a browser language with a region (de-DE) as its base language', async () => {
    await i18n.changeLanguage('de-DE');
    render(<Layout />);
    fireEvent.click((await screen.findAllByRole('button', { name: /^(Language|Sprache)$/ }))[0]);
    const select = (await screen.findByRole('combobox', { name: /^(Language|Sprache)$/ })) as HTMLSelectElement;
    expect(select.value).toBe('de');
  });

  it('offers the language button in the collapsed sidebar footer as well', async () => {
    render(<Layout />);
    await screen.findAllByRole('button', { name: 'Language' });
    // The expanded and the collapsed footer are separate markup.
    const toggle = screen.queryByTitle('Collapse sidebar') ?? screen.getByTitle('Expand sidebar');
    fireEvent.click(toggle);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Language' }))[0]);
    expect(await screen.findByRole('combobox', { name: 'Language' })).toBeInTheDocument();
  });

  it('lists every language by its own name', async () => {
    render(<Layout />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Language' }))[0]);
    const select = await screen.findByRole('combobox', { name: 'Language' });
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(labels).toHaveLength(16);
    expect(labels).toEqual(expect.arrayContaining(['Deutsch', '日本語', 'Português (Brasil)']));
  });

  it('is in the account menu for a user with no settings permissions', async () => {
    signIn(['inventory:read']);
    const changeLanguage = vi.spyOn(i18n, 'changeLanguage');
    const updateSettings = vi.spyOn(api, 'updateSettings');
    render(<Layout />);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Account' }))[0]);
    const select = await screen.findByRole('combobox', { name: 'Language' });
    fireEvent.change(select, { target: { value: 'fr' } });

    await waitFor(() => expect(changeLanguage).toHaveBeenCalledWith('fr'), { timeout: 15000 });
    await waitFor(() => expect(i18n.language).toBe('fr'), { timeout: 15000 });
    expect(updateSettings).not.toHaveBeenCalled();
    expect(settingsWrites).toBe(0);
  });

  it('persists the choice in the detector cache key and reads it back', async () => {
    render(<Layout />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Language' }))[0]);
    fireEvent.change(await screen.findByRole('combobox', { name: 'Language' }), { target: { value: 'de' } });

    await waitFor(() =>
      expect(localStorage.setItem).toHaveBeenCalledWith(LANGUAGE_KEY, 'de'),
    );

    // A reload runs the detector, which reads the same key first.
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === LANGUAGE_KEY ? 'de' : null,
    );
    const detector = i18n.services.languageDetector as { detect: () => string | string[] | undefined };
    const detected = detector.detect();
    expect([detected].flat()[0]).toBe('de');
  });
});

describe('Settings > General SpoolBuddy display language (#3623)', () => {
  let savedLanguage = 'en';
  let settingsWrites: Array<Record<string, unknown>> = [];
  beforeEach(async () => {
    savedLanguage = 'de';
    settingsWrites = [];
    await i18n.changeLanguage('en');
    window.history.replaceState({}, '', '/');
    server.use(
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ check_updates: false, check_printer_firmware: false, language: savedLanguage, currency: 'USD' }),
      ),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        settingsWrites.push(body);
        return HttpResponse.json({ language: savedLanguage, ...body });
      }),
      http.get('/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: false, requires_setup: false }),
      ),
    );
  });

  it('labels the picker as the SpoolBuddy display language and explains it', async () => {
    render(<SettingsPage />);

    expect(await screen.findByText('SpoolBuddy display language')).toBeInTheDocument();
    expect(
      screen.getByText(
        'The language of the SpoolBuddy displays. Each person chooses their own interface language in the account menu, or with the globe button when login is off.',
      ),
    ).toBeInTheDocument();
  });

  it('shows the saved setting, not the browser language, and writes it on change', async () => {
    const updateSettings = vi.spyOn(api, 'updateSettings');
    render(<SettingsPage />);

    const label = await screen.findByText('SpoolBuddy display language');
    const select = within(label.closest('div') as HTMLElement).getByRole('combobox') as HTMLSelectElement;
    // Browser is English, the install-wide setting is German.
    expect(i18n.language).toBe('en');
    await waitFor(() => expect(select.value).toBe('de'));

    fireEvent.change(select, { target: { value: 'fr' } });

    await waitFor(() =>
      expect(updateSettings.mock.calls.some(([body]) => body.language === 'fr')).toBe(true),
    );
    await waitFor(() => expect(settingsWrites.some((b) => b.language === 'fr')).toBe(true));
    await waitFor(() => expect(i18n.language).toBe('fr'), { timeout: 15000 });
  });
});
