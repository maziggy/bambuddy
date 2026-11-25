/**
 * The signed-in user's personal access code for virtual printers (#1430),
 * at the top of Settings > Virtual Printer.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SlicerAccessCodeCard } from '../../components/SlicerAccessCodeCard';
import { setAuthToken } from '../../api/client';

let renewCalls = 0;

function signIn(authEnabled = true) {
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
        permissions: ['library:upload'],
        created_at: '2026-01-01T00:00:00Z',
      }),
    ),
    http.get('*/api/v1/virtual-printers/my-access-code', () => HttpResponse.json({ code: '12345678' })),
    http.post('*/api/v1/virtual-printers/my-access-code', () => {
      renewCalls += 1;
      return HttpResponse.json({ code: '87654321' });
    }),
  );
}

describe('SlicerAccessCodeCard', () => {
  beforeEach(() => {
    renewCalls = 0;
  });

  afterEach(() => {
    setAuthToken(null);
  });

  it('shows the code hidden until asked', async () => {
    signIn();
    const user = userEvent.setup();
    render(<SlicerAccessCodeCard />);

    const code = await screen.findByTestId('slicer-access-code');
    await waitFor(() => expect(code.textContent).toBe('•'.repeat(8)));

    await user.click(screen.getByRole('button', { name: 'Show code' }));

    expect(screen.getByTestId('slicer-access-code').textContent).toBe('12345678');
  });

  it('a new code replaces the old one after confirming', async () => {
    signIn();
    const user = userEvent.setup();
    render(<SlicerAccessCodeCard />);
    await screen.findByTestId('slicer-access-code');

    await user.click(screen.getByRole('button', { name: /New code/ }));
    const title = await screen.findByText('Replace your access code?');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'New code' }));

    await waitFor(() => expect(screen.getByTestId('slicer-access-code').textContent).toBe('87654321'));
    expect(renewCalls).toBe(1);
  });

  it('cancelling keeps the code', async () => {
    signIn();
    const user = userEvent.setup();
    render(<SlicerAccessCodeCard />);
    await screen.findByTestId('slicer-access-code');

    await user.click(screen.getByRole('button', { name: /New code/ }));
    await screen.findByText('Replace your access code?');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(renewCalls).toBe(0);
  });

  it('is not there while auth is off', async () => {
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
    const { container } = render(<SlicerAccessCodeCard />);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(container.textContent).not.toContain('Slicer access code');
  });
});
