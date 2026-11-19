/**
 * Connect slicer (#3602): what a user without Settings access enters in their
 * slicer, the account menu that leads there and the admin's instructions.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ConnectSlicerPage } from '../../pages/ConnectSlicerPage';
import { AccountMenu } from '../../components/AccountMenu';
import { ConnectSlicerInstructionsCard } from '../../components/ConnectSlicerInstructionsCard';
import { setAuthToken, type VPConnectInfo } from '../../api/client';

function signIn(permissions: string[] = ['library:upload'], authEnabled = true) {
  setAuthToken('test-token-1', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: authEnabled, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({
        id: 9,
        username: 'student',
        is_active: true,
        is_admin: false,
        groups: [],
        permissions,
        created_at: '2026-01-01T00:00:00Z',
      }),
    ),
    http.get('*/api/v1/virtual-printers/my-access-code', () => HttpResponse.json({ code: '12345678' })),
    http.get('*/api/v1/virtual-printers/ca-certificate', () =>
      HttpResponse.json({ pem: '-----BEGIN CERTIFICATE-----', fingerprint_sha256: 'AA:BB', not_valid_after: '2036-01-01' }),
    ),
  );
}

function connectInfo(info: Partial<VPConnectInfo>) {
  server.use(
    http.get('*/api/v1/virtual-printers/connect-info', () =>
      HttpResponse.json({ auth_enabled: true, instructions: '', printers: [], ...info }),
    ),
  );
}

const PRINTER = {
  model: 'BL-P001',
  model_name: 'X1C',
  serial: '00M09A391800001',
  target_printer_name: null,
  running: true,
};

describe('ConnectSlicerPage', () => {
  afterEach(() => setAuthToken(null));

  it('shows what to enter for each virtual printer the user may send to', async () => {
    signIn();
    connectInfo({
      printers: [
        { ...PRINTER, id: 1, name: 'Files', mode: 'library', address: '10.0.0.5' },
        { ...PRINTER, id: 2, name: 'Lab queue', mode: 'queue', address: '10.0.0.6', target_printer_name: 'Lab X1C', running: false },
        { ...PRINTER, id: 3, name: 'Any X1C', mode: 'queue', address: '10.0.0.7' },
      ],
    });
    render(<ConnectSlicerPage />);

    expect(await screen.findByText('Files')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
    expect(screen.getAllByText('00M09A391800001')).toHaveLength(3);
    expect(screen.getByText('Files you send are saved to the File Manager.')).toBeInTheDocument();
    expect(screen.getByText('Files you send are queued for Lab X1C.')).toBeInTheDocument();
    expect(screen.getByText('Files you send are queued for the first free X1C.')).toBeInTheDocument();
    expect(screen.getByText('Your slicer shows the live status of Lab X1C.')).toBeInTheDocument();
    expect(screen.getAllByText('Not running')).toHaveLength(1);
    // The user's own code is on the page.
    expect(await screen.findByTestId('slicer-access-code')).toBeInTheDocument();
  });

  it('says so when there is nothing to send to', async () => {
    signIn();
    connectInfo({ printers: [] });
    render(<ConnectSlicerPage />);

    expect(await screen.findByText('There is no virtual printer you can send to. Ask your administrator.')).toBeInTheDocument();
  });

  it("renders the admin's instructions as markdown, without raw HTML", async () => {
    signIn();
    connectInfo({ instructions: '**Connect to the VPN** first <img src="x" alt="injected">' });
    render(<ConnectSlicerPage />);

    const instructions = await screen.findByTestId('connect-slicer-instructions');
    expect(instructions.querySelector('strong')?.textContent).toBe('Connect to the VPN');
    expect(instructions.querySelector('img')).toBeNull();
  });

  it('with auth off, points to the virtual printer settings instead of a personal code', async () => {
    signIn(['settings:read'], false);
    connectInfo({ auth_enabled: false });
    render(<ConnectSlicerPage />);

    expect(await screen.findByText(/Personal access codes need user authentication/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Virtual Printer settings' })).toHaveAttribute(
      'href',
      '/settings?tab=virtual-printer',
    );
    expect(screen.queryByTestId('slicer-access-code')).toBeNull();
  });
});

describe('AccountMenu', () => {
  it('leads to the password change and to the Connect slicer page', async () => {
    const onChangePassword = vi.fn();
    const user = userEvent.setup();
    render(<AccountMenu username="student" onChangePassword={onChangePassword} />);

    await user.click(screen.getByRole('button', { name: 'Account' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('student');
    await user.click(screen.getByRole('button', { name: 'Change Password' }));
    expect(onChangePassword).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Account' }));
    await user.click(screen.getByRole('button', { name: 'Connect slicer' }));
    expect(window.location.pathname).toBe('/connect-slicer');
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    render(<AccountMenu username="student" onChangePassword={() => {}} />);

    await user.click(screen.getByRole('button', { name: 'Account' }));
    expect(screen.getByRole('combobox', { name: 'Language' })).toHaveFocus();
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: 'Account' })).toHaveFocus();
  });
});

describe('ConnectSlicerInstructionsCard', () => {
  afterEach(() => setAuthToken(null));

  it('saves the instructions as a setting', async () => {
    signIn(['settings:read', 'settings:update']);
    let saved: unknown = null;
    server.use(
      http.put('*/api/v1/settings/', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    render(<ConnectSlicerInstructionsCard />);

    const box = await screen.findByLabelText('Instructions for users');
    await waitFor(() => expect(box).not.toBeDisabled());
    await user.type(box, 'VPN first');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(saved).toEqual({ virtual_printer_connect_instructions: 'VPN first' }));
  });

  it('is read-only without settings:update', async () => {
    signIn(['settings:read']);
    render(<ConnectSlicerInstructionsCard />);

    await waitFor(() => expect(screen.getByLabelText('Instructions for users')).toBeDisabled());
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });
});
