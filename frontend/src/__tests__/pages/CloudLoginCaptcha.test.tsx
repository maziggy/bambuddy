/**
 * Bambu Cloud sign-in when Bambu is challenging the network with a CAPTCHA (#2790).
 *
 * The backend answers `reason: 'captcha'`, meaning no credential will be
 * accepted until the challenge clears and there is nothing in Bambuddy that can
 * answer it. A toast is the wrong shape for that: it names a problem the user
 * cannot act on and then disappears. The reporter saw Bambu's own sentence,
 * "We need you to confirm you are not a robot", flash by with no challenge
 * behind it and filed it as a bug.
 */

import { describe, it, expect } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useTranslation } from 'react-i18next';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { LoginForm } from '../../pages/ProfilesPage';
import { server } from '../mocks/server';

function Harness() {
  const { t } = useTranslation();
  return <LoginForm onSuccess={() => {}} t={t} />;
}

async function submitCredentials() {
  const user = userEvent.setup();
  await user.type(await screen.findByPlaceholderText('your@email.com'), 'user@example.com');
  await user.type(screen.getByPlaceholderText('••••••••'), 'hunter2');
  await user.click(screen.getByRole('button', { name: /login/i }));
  return user;
}

describe('Bambu Cloud sign-in blocked by a CAPTCHA', () => {
  it('explains the challenge in place and offers the token route', async () => {
    server.use(
      http.post('/api/v1/cloud/login', () =>
        HttpResponse.json({
          success: false,
          needs_verification: false,
          reason: 'captcha',
          message: 'We need you to confirm you are not a robot',
        }),
      ),
    );

    render(<Harness />);
    const user = await submitCredentials();

    const panel = await screen.findByRole('alert');
    expect(panel).toHaveTextContent(/Bambu Cloud is asking for a CAPTCHA/i);
    // The two things the reporter had no way to find out.
    expect(panel).toHaveTextContent(/email and password are not the problem/i);
    expect(panel).toHaveTextContent(/clears by itself within a few hours/i);

    // Bambu's raw sentence is never what the user is left holding.
    expect(screen.queryByText('We need you to confirm you are not a robot')).not.toBeInTheDocument();

    // The one action that does work is one click away.
    await user.click(within(panel).getByRole('button', { name: /use access token instead/i }));
    expect(await screen.findByPlaceholderText('eyJ...')).toBeInTheDocument();
    expect(screen.queryByText(/Bambu Cloud is asking for a CAPTCHA/i)).not.toBeInTheDocument();
  });

  it('leaves an ordinary rejection as a toast', async () => {
    server.use(
      http.post('/api/v1/cloud/login', () =>
        HttpResponse.json({ success: false, needs_verification: false, message: 'Login failed' }),
      ),
    );

    render(<Harness />);
    await submitCredentials();

    await waitFor(() => expect(screen.getByText('Login failed')).toBeInTheDocument());
    expect(screen.queryByText(/Bambu Cloud is asking for a CAPTCHA/i)).not.toBeInTheDocument();
  });
});
