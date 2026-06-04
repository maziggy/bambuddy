/**
 * Tests for InstallAppButton - the in-app PWA install trigger (#1460).
 */

import { describe, it, expect, vi } from 'vitest';
import { act } from 'react';
import { render, screen, waitFor } from '../utils';
import userEvent from '@testing-library/user-event';
import { InstallAppButton } from '../../components/InstallAppButton';

// Scoped so the assertion ignores unrelated buttons (e.g. a toast's dismiss).
const INSTALL_BUTTON = { name: 'Install app' } as const;

/** Build a fake beforeinstallprompt event with a controllable userChoice. */
function makeInstallPromptEvent(outcome: 'accepted' | 'dismissed') {
  const event = new Event('beforeinstallprompt') as Event & {
    prompt: () => Promise<void>;
    userChoice: Promise<{ outcome: string; platform: string }>;
  };
  event.prompt = vi.fn().mockResolvedValue(undefined);
  event.userChoice = Promise.resolve({ outcome, platform: 'web' });
  return event;
}

describe('InstallAppButton', () => {
  it('renders nothing until the browser fires beforeinstallprompt', () => {
    render(<InstallAppButton />);
    expect(screen.queryByRole('button', INSTALL_BUTTON)).toBeNull();
  });

  it('shows the install button once beforeinstallprompt fires', async () => {
    render(<InstallAppButton />);
    await act(async () => {
      window.dispatchEvent(makeInstallPromptEvent('accepted'));
    });
    expect(await screen.findByRole('button', INSTALL_BUTTON)).toBeInTheDocument();
  });

  it('fires the captured prompt on click and hides itself afterwards', async () => {
    const user = userEvent.setup();
    const event = makeInstallPromptEvent('accepted');
    render(<InstallAppButton />);

    await act(async () => {
      window.dispatchEvent(event);
    });
    await user.click(await screen.findByRole('button', INSTALL_BUTTON));

    expect(event.prompt).toHaveBeenCalledTimes(1);
    // A captured prompt can only be used once, so the button must disappear.
    await waitFor(() =>
      expect(screen.queryByRole('button', INSTALL_BUTTON)).toBeNull()
    );
  });

  it('hides itself even when the user dismisses the prompt', async () => {
    const user = userEvent.setup();
    const event = makeInstallPromptEvent('dismissed');
    render(<InstallAppButton />);

    await act(async () => {
      window.dispatchEvent(event);
    });
    await user.click(await screen.findByRole('button', INSTALL_BUTTON));

    await waitFor(() =>
      expect(screen.queryByRole('button', INSTALL_BUTTON)).toBeNull()
    );
  });

  it('hides the button when the app reports it was installed', async () => {
    render(<InstallAppButton />);
    await act(async () => {
      window.dispatchEvent(makeInstallPromptEvent('accepted'));
    });
    expect(await screen.findByRole('button', INSTALL_BUTTON)).toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(new Event('appinstalled'));
    });
    await waitFor(() =>
      expect(screen.queryByRole('button', INSTALL_BUTTON)).toBeNull()
    );
  });
});
