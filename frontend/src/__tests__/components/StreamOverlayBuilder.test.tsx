/**
 * Tests for the streaming-overlay URL builder (#1422).
 *
 * The builder's whole output is a URL, so that is what these assert: the field
 * order, what is omitted at its default, and that the preview does not open a
 * camera stream until it is asked to.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';
import { StreamOverlayBuilder } from '../../components/StreamOverlayBuilder';

const printers = [
  { id: 1, name: 'X1 Carbon', ip_address: '192.168.1.100', serial_number: '00M09A350100001', model: 'X1C' },
  { id: 2, name: 'P1S', ip_address: '192.168.1.101', serial_number: '01P00A000000002', model: 'P1S' },
];

const createdToken = {
  id: 42, user_id: 1, name: 'OBS', scope: 'overlay', lookup_prefix: '12345678',
  created_at: '2026-01-01T00:00:00Z', expires_at: '2099-01-01T00:00:00Z',
  last_used_at: null, token: null,
};

function setupSignedIn() {
  setAuthToken('test-login');
  server.use(http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })));
}

// The URL is rendered inside a <code>, so read it back the way a user would.
function shownUrl(): string {
  const code = document.querySelector('code');
  return code?.textContent ?? '';
}

describe('StreamOverlayBuilder', () => {
  afterEach(() => { setAuthToken(null); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    server.use(http.get('/api/v1/printers', () => HttpResponse.json(printers)), http.get('/api/v1/settings/overlay-logo', () => new HttpResponse(null, { status: 404 })));
  });

  it.each(['1', '2'])('provides independent orientation URLs and previews for artwork %s', async (artwork) => {
    const user = userEvent.setup();
    const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    vi.stubGlobal('isSecureContext', true);
    const { unmount } = render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    await user.selectOptions(screen.getByLabelText('Artwork'), artwork);
    const layout = screen.getByLabelText('Layout');
    expect(layout).toHaveValue('landscape');
    await user.selectOptions(layout, 'both');
    fireEvent.change(screen.getByLabelText('From colour (hex)'), { target: { value: '#ff0000' } });
    fireEvent.change(screen.getByLabelText('To colour (hex)'), { target: { value: '#0000ff' } });
    if (artwork === '2') fireEvent.change(screen.getByRole('slider', { name: /Background transparency/ }), { target: { value: '65' } });
    expect(document.querySelectorAll('iframe')).toHaveLength(0);
    await user.selectOptions(screen.getByLabelText('Printer'), '2');
    await user.type(screen.getByLabelText(/token/i), 'bblt_example');
    await user.selectOptions(screen.getByLabelText('Text size'), 'large');
    await user.click(screen.getByLabelText('Nozzle'));
    await user.click(screen.getByLabelText('Camera feed'));
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    for (const orientation of ['Landscape', 'Portrait']) {
      const group = within(screen.getByRole('group', { name: `${orientation} URL` }));
      const url = new URL(group.getByRole('link', { name: 'Open' }).getAttribute('href') ?? '');
      expect(url.pathname).toBe('/overlay/2');
      expect(url.searchParams.get('progressFrom')).toBe('#ff0000');
      expect(url.searchParams.get('progressTo')).toBe('#0000ff');
      expect(url.searchParams.get('backgroundTransparency')).toBe(artwork === '2' ? '65' : null);
      expect(url.searchParams.get('layout')).toBe(orientation === 'Portrait' ? 'portrait' : null);
      expect(url.searchParams.get('token')).toBe('bblt_example');
      expect(group.getByText(/token=\*\*\*\*/)).not.toHaveTextContent('bblt_example');
      expect(url.searchParams.get('size')).toBe('large');
      expect(url.searchParams.get('camera')).toBe('false');
      expect(url.searchParams.get('show')).toContain('nozzle');
      expect(url.searchParams.get('artwork')).toBe(artwork === '2' ? '2' : null);
      await waitFor(() => expect(screen.getByTitle(`${orientation} preview`)).toHaveAttribute('src', url.href));
      await user.click(group.getByRole('button', { name: 'Copy' }));
      expect(copy).toHaveBeenLastCalledWith(url.href);
    }
    await user.click(screen.getByLabelText('Camera feed'));
    await user.selectOptions(screen.getByLabelText('Text size'), 'small');
    const fps = screen.getByLabelText('Frame rate');
    await user.clear(fps);
    await user.type(fps, '5');
    await waitFor(() => {
      for (const iframe of document.querySelectorAll('iframe')) {
        const params = new URL(iframe.src).searchParams;
        expect(params.get('camera')).toBeNull();
        expect(params.get('size')).toBe('small');
        expect(params.get('fps')).toBe('5');
      }
    });
    await user.selectOptions(layout, 'portrait');
    expect(document.querySelectorAll('iframe')).toHaveLength(1);
    expect(screen.queryByTitle('Landscape preview')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Hide preview' }));
    expect(document.querySelectorAll('iframe')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    unmount();
    expect(document.querySelectorAll('iframe')).toHaveLength(0);
    copy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('imports portrait sources and resets to landscape when layout is absent', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    const input = screen.getByLabelText('Existing overlay URL');
    await user.type(input, 'https://obs.local/overlay/2?layout=portrait&token=bblt_portrait');
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(screen.getByLabelText('Layout')).toHaveValue('portrait');
    expect(new URL(shownUrl()).searchParams.get('layout')).toBe('portrait');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    expect(new URL(screen.getByTitle('Portrait preview').getAttribute('src')!).searchParams.get('token')).toBe('bblt_portrait');
    await user.type(input, 'https://obs.local/overlay/1?token=bblt_landscape');
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(screen.getByLabelText('Layout')).toHaveValue('landscape');
    expect(new URL(shownUrl()).searchParams.has('layout')).toBe(false);
    expect(screen.queryByTitle('Portrait preview')).not.toBeInTheDocument();
  });

  it('accepts an existing raw token without requiring saved-token recovery', async () => {
    const user = userEvent.setup();
    const view = render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    const manual = screen.getByLabelText('Manual token');
    expect(manual).toHaveAttribute('type', 'password');
    await user.type(manual, 'bblt_existing');
    expect(shownUrl()).not.toContain('bblt_existing');
    expect(shownUrl()).toContain('token=****');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    expect(new URL(screen.getByTitle('Landscape preview').getAttribute('src')!).searchParams.get('token')).toBe('bblt_existing');
    await user.click(screen.getByRole('button', { name: 'Show token' }));
    expect(manual).toHaveAttribute('type', 'text');
    expect(shownUrl()).toContain('token=bblt_existing');
    view.unmount();
    render(<StreamOverlayBuilder />);
    expect(screen.getByLabelText('Manual token')).toHaveValue('');
    expect(shownUrl()).not.toContain('bblt_existing');
  });

  it('imports locally, masks credentials, restores settings and keeps the current origin', async () => {
    const user = userEvent.setup();
    const storageWrites = vi.spyOn(Storage.prototype, 'setItem');
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    const input = screen.getByLabelText('Existing overlay URL');
    expect(input).toHaveAttribute('type', 'password');
    await user.type(input, 'https://other.example/overlay/2?token=bblt_imported&show=nozzle,status&size=large&fps=5&artwork=2&camera=false');
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(input).toHaveValue('');
    expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_imported');
    expect(shownUrl()).not.toContain('bblt_imported');
    expect(new URL(shownUrl()).origin).toBe(window.location.origin);
    expect(screen.getByLabelText('Printer')).toHaveValue('2');
    expect(screen.getByLabelText('Text size')).toHaveValue('large');
    expect(screen.getByLabelText('Artwork')).toHaveValue('2');
    expect(screen.getByLabelText('Camera feed')).not.toBeChecked();
    expect(new URL(shownUrl()).searchParams.get('show')).toBe('status,nozzle');
    expect(new URL(shownUrl()).searchParams.get('fps')).toBe('5');
    expect(document.querySelector('iframe')).toBeNull();
    expect(storageWrites).not.toHaveBeenCalledWith(expect.anything(), expect.stringContaining('bblt_imported'));
    await user.type(input, 'http://old.example/overlay/1');
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(screen.getByLabelText('Manual token')).toHaveValue('');
    expect(screen.getByLabelText('Text size')).toHaveValue('medium');
    expect(screen.getByLabelText('Artwork')).toHaveValue('1');
    expect(screen.getByLabelText('Camera feed')).toBeChecked();
    expect(new URL(shownUrl()).searchParams.has('fps')).toBe(false);
  });

  it.each([
    'javascript:alert(1)', 'https://example.com/overlay/nope',
    'https://example.com/overlay/0', 'https://example.com/overlay/9007199254740992',
    'https://user:password@example.com/overlay/2',
    'https://example.com/overlay/2?token=a&token=b',
  ])('rejects invalid or unsupported imports without changing settings: %s', async (value) => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    await user.type(screen.getByLabelText('Manual token'), 'bblt_keep');
    const original = shownUrl();
    await user.type(screen.getByLabelText('Existing overlay URL'), value);
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(shownUrl()).toBe(original);
    expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_keep');
  });

  it.each([
    ['999', '30'], ['0', '1'], ['-5', '1'], ['5.9', '5'], ['20fps', '20'], ['bad', null], ['', null],
  ])('imports fps=%s with runtime clamping and ignores unknown configuration', async (fps, expected) => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    fireEvent.change(screen.getByLabelText('Existing overlay URL'), { target: { value:
      `https://other.example/overlay/2?fps=${fps}&show=status,unknown&size=huge&unrelated=ignored` } });
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const url = new URL(shownUrl());
    expect(url.searchParams.get('fps')).toBe(expected);
    expect(url.searchParams.get('show')).toBe('status');
    expect(screen.getByLabelText('Text size')).toHaveValue('medium');
    expect(url.searchParams.has('unrelated')).toBe(false);
  });

  it('imports branding and transparency and resets missing values on the next import', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    fireEvent.change(screen.getByLabelText('Existing overlay URL'), { target: { value:
      'https://other.example/overlay/2?artwork=2&backgroundTransparency=150&logo=1&progressFrom=%23aabbcc&progressTo=%23112233' } });
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    const params = new URL(shownUrl()).searchParams;
    expect(params.get('backgroundTransparency')).toBe('100');
    expect(params.get('logo')).toBe('1');
    expect(params.get('progressFrom')).toBe('#aabbcc');
    expect(params.get('progressTo')).toBe('#112233');
    expect(screen.getByLabelText('From colour (hex)')).toHaveValue('#aabbcc');
    expect(screen.getByLabelText('To colour (hex)')).toHaveValue('#112233');
    fireEvent.change(screen.getByLabelText('Existing overlay URL'), { target: { value:
      'https://other.example/overlay/2?artwork=2&progressFrom=invalid&progressTo=%23112233' } });
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(shownUrl()).not.toMatch(/backgroundTransparency|logo=|progressFrom|progressTo/);
    expect(screen.getByLabelText('From colour (hex)')).toHaveValue('#00ae42');
  });

  it('locks import during a logo upload so its completion cannot overwrite imported branding', async () => {
    let finish = () => {};
    const gate = new Promise<void>((resolve) => { finish = resolve; });
    let started = false;
    server.use(http.post('/api/v1/settings/overlay-logo', async () => {
      started = true;
      await gate;
      return HttpResponse.json({ status: 'ok' });
    }));
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    fireEvent.change(screen.getByLabelText('Existing overlay URL'), { target: { value:
      'https://other.example/overlay/2?progressFrom=%23aabbcc&progressTo=%23112233' } });
    expect(screen.getByRole('button', { name: 'Import URL' })).toBeEnabled();
    await user.upload(screen.getByLabelText('Upload logo'), new File(['png'], 'logo.png', { type: 'image/png' }));
    await waitFor(() => expect(started).toBe(true));
    expect(screen.getByLabelText('Existing overlay URL')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Import URL' })).toBeDisabled();
    await act(async () => { finish(); await gate; });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Import URL' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Import URL' }));
    expect(new URL(shownUrl()).searchParams.has('logo')).toBe(false);
    expect(new URL(shownUrl()).searchParams.get('progressFrom')).toBe('#aabbcc');
    expect(screen.getByLabelText('From colour (hex)')).toHaveValue('#aabbcc');
  });

  it('shows a server creation error, unlocks controls, and allows successful repeated creation', async () => {
    setupSignedIn();
    server.use(http.post('*/api/v1/auth/tokens', () => HttpResponse.json({ detail: 'Token limit reached' }, { status: 400 })));
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await user.click(await screen.findByRole('button', { name: 'Create overlay token' }));
    await user.type(screen.getByLabelText('Token name'), 'OBS');
    await user.click(screen.getByRole('button', { name: 'Create', exact: true }));
    expect(await screen.findByText('Token limit reached')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
    expect(screen.getByLabelText('Manual token')).toBeEnabled();
    expect(screen.getByLabelText('Existing overlay URL')).toBeEnabled();
    server.use(http.post('*/api/v1/auth/tokens', () => HttpResponse.json({ ...createdToken, token: 'bblt_retry' }, { status: 201 })));
    await user.click(screen.getByRole('button', { name: 'Create', exact: true }));
    await waitFor(() => expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_retry'));
    await user.click(screen.getByRole('button', { name: 'Create overlay token' }));
    await user.type(screen.getByLabelText('Token name'), 'OBS again');
    server.use(http.post('*/api/v1/auth/tokens', () => HttpResponse.json({ ...createdToken, token: 'bblt_second' }, { status: 201 })));
    await user.click(screen.getByRole('button', { name: 'Create', exact: true }));
    await waitFor(() => expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_second'));
  });

  it('starts on the first printer with the overlay defaults', async () => {
    render(<StreamOverlayBuilder />);

    await waitFor(() => {
      expect(shownUrl()).toContain('/overlay/1');
    });
    // The same set parseConfig() defaults to, so the builder's starting point
    // and a bare /overlay/1 render the same overlay. Emitted in the overlay's
    // own top-to-bottom field order rather than parseConfig's listing order —
    // ?show= is read with includes(), so order is free to be the stable one.
    expect(shownUrl()).toContain('show=filename%2Cstatus%2Cprogress%2Clayers%2Ceta');
    // Defaults are omitted rather than spelled out — a shorter URL to paste.
    expect(shownUrl()).not.toContain('layout=');
    expect(shownUrl()).not.toContain('size=');
    expect(shownUrl()).not.toContain('fps=');
    expect(shownUrl()).not.toContain('camera=');
    expect(shownUrl()).not.toContain('token=');
  });

  it('keeps the model opt-in and updates the URL and preview when toggled', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    const model = await screen.findByLabelText('Printer model');
    expect(model).not.toBeChecked();
    const originalUrl = shownUrl();
    await user.click(model);
    expect(new URL(shownUrl()).searchParams.get('show')).toBe('model,filename,status,progress,layers,eta');
    await user.click(screen.getByLabelText('Printer name'));
    expect(new URL(shownUrl()).searchParams.get('show')).toBe('printer,model,filename,status,progress,layers,eta');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl()));

    await user.click(model);
    await user.click(screen.getByLabelText('Printer name'));
    expect(shownUrl()).toBe(originalUrl);
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', originalUrl));
  });

  it('opts into artwork in the URL and preview and restores the original URL', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    const artwork = await screen.findByLabelText('Artwork');
    expect(artwork).toHaveValue('1');
    const original = shownUrl();
    expect(new URL(original).searchParams.has('artwork')).toBe(false);
    await user.selectOptions(artwork, 'Version 2');
    expect(new URL(shownUrl()).searchParams.get('artwork')).toBe('2');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl()));
    await user.selectOptions(artwork, 'Classic');
    expect(shownUrl()).toBe(original);
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', original));
  });

  it('only offers background transparency for Version 2 and preserves its selection', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    const artwork = await screen.findByLabelText('Artwork');
    expect(screen.queryByRole('slider', { name: /Background transparency/ })).not.toBeInTheDocument();
    await user.selectOptions(artwork, 'Version 2');
    const slider = screen.getByRole('slider', { name: /Background transparency/ });
    expect(slider).toHaveValue('0');
    expect(shownUrl()).not.toContain('backgroundTransparency');
    fireEvent.change(slider, { target: { value: '65' } });
    expect(new URL(shownUrl()).searchParams.get('backgroundTransparency')).toBe('65');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl()));
    await user.selectOptions(artwork, 'Classic');
    expect(screen.queryByRole('slider', { name: /Background transparency/ })).not.toBeInTheDocument();
    expect(shownUrl()).not.toContain('backgroundTransparency');
    await user.selectOptions(artwork, 'Version 2');
    expect(screen.getByRole('slider', { name: /Background transparency/ })).toHaveValue('65');
  });

  it('switches printer', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await screen.findByRole('option', { name: 'P1S' });
    await user.selectOptions(screen.getByLabelText('Printer'), '2');

    await waitFor(() => expect(shownUrl()).toContain('/overlay/2'));
  });

  it('adds a temperature field the URL did not have', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await waitFor(() => expect(screen.getByLabelText('Nozzle')).toBeInTheDocument());
    await user.click(screen.getByLabelText('Nozzle'));

    await waitFor(() =>
      expect(shownUrl()).toContain('show=filename%2Cstatus%2Cprogress%2Clayers%2Ceta%2Cnozzle'),
    );
  });

  it('emits fields in the overlay order, not the order they were clicked', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    // "Printer name" is first in the overlay's own top-to-bottom order, so
    // ticking it last must still put it at the front. Otherwise the same
    // selection would produce a different URL depending on click order, and a
    // scene file would stop being comparable to the one next to it.
    await waitFor(() => expect(screen.getByLabelText('Printer name')).toBeInTheDocument());
    await user.click(screen.getByLabelText('Printer name'));

    await waitFor(() => expect(shownUrl()).toContain('show=printer%2Cfilename'));
  });

  it('drops a field when its box is cleared', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await waitFor(() => expect(screen.getByLabelText('Layer count')).toBeInTheDocument());
    await user.click(screen.getByLabelText('Layer count'));

    await waitFor(() => expect(shownUrl()).not.toContain('layers'));
    expect(shownUrl()).toContain('progress');
  });

  it('emits camera=false when the camera feed is switched off', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await waitFor(() => expect(screen.getByLabelText('Camera feed')).toBeInTheDocument());
    await user.click(screen.getByLabelText('Camera feed'));

    await waitFor(() => expect(shownUrl()).toContain('camera=false'));
  });

  it('emits size and fps only when they differ from the defaults', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await waitFor(() => expect(screen.getByLabelText('Text size')).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Text size'), 'large');
    await waitFor(() => expect(shownUrl()).toContain('size=large'));

    await user.selectOptions(screen.getByLabelText('Text size'), 'medium');
    await waitFor(() => expect(shownUrl()).not.toContain('size='));
  });

  it('uses a newly created overlay token directly without retrieval', async () => {
    setupSignedIn();
    server.use(
      http.post('*/api/v1/auth/tokens', async ({ request }) => {
        expect(await request.json()).toEqual({ name: 'OBS', scope: 'overlay', expires_in_days: 90 });
        return HttpResponse.json({ ...createdToken, token: 'bblt_saved' }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await user.click(await screen.findByRole('button', { name: 'Create overlay token' }));
    await user.selectOptions(screen.getByLabelText('Printer'), '2');
    await user.type(screen.getByLabelText('Token name'), 'OBS');
    expect(screen.getByLabelText('Scope')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Create', exact: true }));
    await screen.findByText(/This URL contains a token/);
    expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_saved');
    await user.click(screen.getByRole('button', { name: 'Show token' }));
    expect(shownUrl()).toContain('/overlay/2?');
    expect(shownUrl()).toContain('token=bblt_saved');
  });

  it('locks conflicting controls until a delayed token is available', async () => {
    setupSignedIn();
    let finish = () => {};
    const gate = new Promise<void>((resolve) => { finish = resolve; });
    let started = false;
    server.use(http.post('*/api/v1/auth/tokens', async () => {
      started = true;
      await gate;
      return HttpResponse.json({ ...createdToken, token: 'bblt_late' }, { status: 201 });
    }));
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);
    await user.click(await screen.findByRole('button', { name: 'Create overlay token' }));
    await user.type(screen.getByLabelText('Existing overlay URL'), 'https://other.example/overlay/2?token=bblt_import');
    expect(screen.getByRole('button', { name: 'Import URL' })).toBeEnabled();
    await user.type(screen.getByLabelText('Token name'), 'OBS');
    await user.click(screen.getByRole('button', { name: 'Create', exact: true }));
    await waitFor(() => expect(started).toBe(true));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.getByLabelText('Manual token')).toBeDisabled();
    expect(screen.getByLabelText('Existing overlay URL')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Import URL' })).toBeDisabled();
    await act(async () => { finish(); await gate; });
    await screen.findByText('Token created');
    expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_late');
    expect(screen.getByLabelText('Manual token')).toBeEnabled();
    expect(screen.getByLabelText('Existing overlay URL')).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Show token' }));
    expect(shownUrl()).toContain('token=bblt_late');
    await user.click(screen.getByRole('button', { name: 'Create overlay token' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByLabelText('Manual token')).toHaveValue('bblt_late');
  });

  it('does not offer token creation without a signed-in user', async () => {
    render(<StreamOverlayBuilder />);
    await screen.findByRole('option', { name: 'P1S' });
    expect(screen.queryByRole('button', { name: 'Create overlay token' })).not.toBeInTheDocument();
  });

  it.each([true, false])('copies the full masked URL with secure context = %s', async (secure) => {
    setupSignedIn();
    const user = userEvent.setup();
    vi.stubGlobal('isSecureContext', secure);
    let copied = '';
    if (secure) {
      vi.spyOn(navigator.clipboard, 'writeText').mockImplementation(async (value) => { copied = value; });
    } else {
      Object.defineProperty(document, 'execCommand', { configurable: true, value: () => {
        copied = document.querySelector('textarea')?.value ?? '';
        return true;
      } });
    }
    render(<StreamOverlayBuilder />);
    await user.type(screen.getByLabelText('Manual token'), 'bblt_saved');
    await screen.findByText(/This URL contains a token/);
    await user.click(screen.getByRole('button', { name: 'Copy', exact: true }));
    expect(new URL(copied).searchParams.get('token')).toBe('bblt_saved');
    expect(shownUrl()).not.toContain('bblt_saved');
  });

  it('opens no camera stream until the preview is asked for', async () => {
    const user = userEvent.setup();
    render(<StreamOverlayBuilder />);

    await waitFor(() => expect(screen.getByText('Show preview')).toBeInTheDocument());
    // An always-on preview would hold a subscriber on the printer's single
    // camera connection for as long as the settings tab stays open.
    expect(document.querySelector('iframe')).toBeNull();

    await user.click(screen.getByText('Show preview'));

    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull());
    expect(document.querySelector('iframe')?.getAttribute('src')).toContain('/overlay/1');
  });

  it('still builds a URL when the printer list cannot be loaded', async () => {
    server.use(http.get('/api/v1/printers', () => HttpResponse.json({ detail: 'nope' }, { status: 500 })));
    render(<StreamOverlayBuilder />);

    // Falls back to printer 1 rather than rendering /overlay/null — the number
    // is the one thing the user can fix by hand in the URL.
    await waitFor(() => expect(shownUrl()).toContain('/overlay/1'));
  });
});

it('adds validated gradient colours to the URL and resets to the default', async () => {
    render(<StreamOverlayBuilder />);
    const from = await screen.findByLabelText('From colour (hex)');
    fireEvent.change(from, { target: { value: '#ff0000' } });
    fireEvent.change(screen.getByLabelText('To colour (hex)'), { target: { value: '#0000ff' } });
    expect(new URL(shownUrl()).searchParams.get('progressFrom')).toBe('#ff0000');
    expect(new URL(shownUrl()).searchParams.get('progressTo')).toBe('#0000ff');
    fireEvent.change(from, { target: { value: 'invalid' } });
    expect(new URL(shownUrl()).searchParams.get('progressFrom')).toBe('#ff0000');
    fireEvent.click(screen.getByRole('button', { name: 'Reset colours' }));
    expect(shownUrl()).not.toContain('progressFrom');
    expect(shownUrl()).not.toContain('progressTo');
  });

it('uploads a logo, includes it in the URL, and removes it from the preview', async () => {
  const user = userEvent.setup();
  let saved = false;
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:logo-preview');
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  server.use(
    http.get('/api/v1/printers', () => HttpResponse.json(printers)),
    http.get('/api/v1/settings/overlay-logo', () => saved ? new HttpResponse(new Blob(['png'], { type: 'image/png' })) : new HttpResponse(null, { status: 404 })),
    http.post('/api/v1/settings/overlay-logo', () => { saved = true; return HttpResponse.json({ status: 'ok' }); }),
    http.delete('/api/v1/settings/overlay-logo', () => { saved = false; return HttpResponse.json({ status: 'ok' }); }),
  );
  try {
    render(<StreamOverlayBuilder />);
    await user.upload(await screen.findByLabelText('Upload logo'), new File(['png'], 'logo.png', { type: 'image/png' }));
    expect(await screen.findByRole('img', { name: 'Custom logo' })).toHaveAttribute('src', 'blob:logo-preview');
    expect(new URL(shownUrl()).searchParams.get('logo')).toBe('1');
    await user.click(screen.getByRole('button', { name: 'Show preview' }));
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl()));
    const previousPreview = screen.getByTitle('Landscape preview');
    const previousUrl = shownUrl();
    await user.upload(screen.getByLabelText('Upload logo'), new File(['new png'], 'replacement.png', { type: 'image/png' }));
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).not.toBe(previousPreview));
    expect(shownUrl()).toBe(previousUrl);
    expect(await screen.findByRole('img', { name: 'Custom logo' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.queryByRole('img', { name: 'Custom logo' })).not.toBeInTheDocument());
    expect(new URL(shownUrl()).searchParams.has('logo')).toBe(false);
    await waitFor(() => expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl()));
  } finally {
    create.mockRestore();
    revoke.mockRestore();
  }
});

it('debounces continuous preview changes and cancels a pending reload when hidden', async () => {
  server.use(
    http.get('/api/v1/printers', () => HttpResponse.json(printers)),
    http.get('/api/v1/settings/overlay-logo', () => new HttpResponse(null, { status: 404 })),
  );
  render(<StreamOverlayBuilder />);
  await screen.findByRole('option', { name: 'X1 Carbon' });
  fireEvent.change(screen.getByLabelText('Artwork'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Show preview' }));
  const original = screen.getByTitle('Landscape preview');
  vi.useFakeTimers();
  try {
    fireEvent.change(screen.getByLabelText('From colour'), { target: { value: '#ff0000' } });
    act(() => vi.advanceTimersByTime(200));
    fireEvent.change(screen.getByRole('slider', { name: /Background transparency/ }), { target: { value: '65' } });
    act(() => vi.advanceTimersByTime(299));
    expect(screen.getByTitle('Landscape preview')).toBe(original);
    expect(original).not.toHaveAttribute('src', shownUrl());
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByTitle('Landscape preview')).not.toBe(original);
    expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl());

    fireEvent.change(screen.getByLabelText('From colour'), { target: { value: '#0000ff' } });
    fireEvent.click(screen.getByRole('button', { name: 'Hide preview' }));
    act(() => vi.advanceTimersByTime(300));
    expect(screen.queryByTitle('Landscape preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show preview' }));
    expect(screen.getByTitle('Landscape preview')).toHaveAttribute('src', shownUrl());
    fireEvent.click(screen.getByRole('button', { name: 'Hide preview' }));
  } finally {
    vi.useRealTimers();
  }
});

it.each([
  [400, { detail: 'Logo must be a static PNG or WebP image' }, 'Logo must be a static PNG or WebP image'],
  [413, { detail: { message: 'Logo must be at most 2 MiB' } }, 'Logo must be at most 2 MiB'],
  [502, null, 'HTTP 502'],
])('shows the server upload error for status %s', async (status, body, message) => {
  server.use(
    http.get('/api/v1/printers', () => HttpResponse.json(printers)),
    http.get('/api/v1/settings/overlay-logo', () => new HttpResponse(null, { status: 404 })),
    http.post('/api/v1/settings/overlay-logo', () => body ? HttpResponse.json(body, { status }) : new HttpResponse('Bad gateway', { status })),
  );
  const user = userEvent.setup();
  render(<StreamOverlayBuilder />);
  await user.upload(screen.getByLabelText('Upload logo'), new File(['invalid'], 'logo.png', { type: 'image/png' }));
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(new URL(shownUrl()).searchParams.has('logo')).toBe(false);
  expect(screen.getByLabelText('Upload logo')).toBeEnabled();
});
