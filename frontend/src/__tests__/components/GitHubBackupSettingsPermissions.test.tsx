/**
 * The Git Restore button must respect github:restore client-side (#2656).
 *
 * All three restore endpoints are gated on GITHUB_RESTORE server-side, so a
 * user without it gets a 403 the moment the modal opens its preview. Offering
 * the button anyway is an action that cannot work.
 *
 * Scoped to the button on purpose: the backup card itself stays visible,
 * because configuring backups is a separate permission.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';
import { setAuthToken } from '../../api/client';

afterEach(() => {
  server.resetHandlers();
  setAuthToken(null);
});

/** A configured backup, which is what makes the action row render at all. */
function mockConfiguredBackup() {
  server.use(
    http.get('*/api/v1/github-backup/config', () =>
      HttpResponse.json({
        id: 1,
        provider: 'github',
        repository_url: 'https://github.com/test/repo',
        branch: 'main',
        enabled: true,
        schedule_enabled: false,
        schedule_type: 'daily',
        schedule_time: '02:00',
        backup_kprofiles: true,
        backup_cloud_profiles: false,
        backup_spools: true,
        backup_archives: true,
        backup_settings: true,
        last_backup_at: null,
        last_backup_status: null,
      }),
    ),
    http.get('*/api/v1/github-backup/status', () =>
      HttpResponse.json({
        configured: true,
        enabled: true,
        is_running: false,
        restore_running: false,
        progress: null,
        last_backup_at: null,
        last_backup_status: null,
        next_run: null,
      }),
    ),
    http.get('*/api/v1/github-backup/logs', () => HttpResponse.json([])),
  );
}

function mockUserWith(permissions: string[]) {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () =>
      HttpResponse.json({ auth_enabled: true, requires_setup: false }),
    ),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 1, username: 'operator', is_admin: false, permissions }),
    ),
  );
}

describe('GitHubBackupSettings - github:restore gate', () => {
  it('hides the Restore from Git button without the permission', async () => {
    mockConfiguredBackup();
    mockUserWith(['settings:read', 'settings:update']);

    render(<GitHubBackupSettings />);

    // Wait for the action row itself, so an absent button is a real absence
    // rather than the card simply not having rendered yet.
    await waitFor(() => expect(screen.getByRole('button', { name: /Backup Now/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Restore from Git/i })).not.toBeInTheDocument();
  });

  it('shows it when the user has github:restore', async () => {
    mockConfiguredBackup();
    mockUserWith(['settings:read', 'github:restore']);

    render(<GitHubBackupSettings />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Restore from Git/i })).toBeInTheDocument(),
    );
  });

  it('shows it when auth is disabled entirely', async () => {
    // hasPermission returns true with auth off, and it must stay that way -
    // a single-user instance has no permissions to grant.
    mockConfiguredBackup();
    server.use(
      http.get('*/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: false, requires_setup: false }),
      ),
    );

    render(<GitHubBackupSettings />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Restore from Git/i })).toBeInTheDocument(),
    );
  });
});

describe('GitHubBackupSettings - a full backup is admin-only', () => {
  // It holds every password hash and 2FA secret, and restoring one replaces
  // the users: the server refuses both to a non-administrator.
  it('offers a non-admin neither the download nor the restore of a full backup', async () => {
    mockConfiguredBackup();
    mockUserWith(['settings:read', 'settings:backup', 'settings:restore']);

    render(<GitHubBackupSettings />);

    const download = await screen.findByRole('button', { name: /^Download$/i });
    expect(download).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Restore$/i })).toBeDisabled();
    expect(screen.getAllByText('Only an administrator can change this.').length).toBeGreaterThan(0);
  });

  it('shows a non-admin the backup folder read-only', async () => {
    // Pointed into a folder the web server hands out, a full backup could be
    // fetched without signing in.
    mockConfiguredBackup();
    mockUserWith(['settings:read', 'settings:update', 'settings:backup']);
    server.use(
      http.get('*/api/v1/local-backup/status', () =>
        HttpResponse.json({
          enabled: true, schedule: 'daily', time: '03:00', retention: 5, path: '', default_path: '/data/backups',
          is_running: false, last_backup_at: null, last_status: null, last_message: null, next_run: null, timezone: 'UTC',
        }),
      ),
      http.get('*/api/v1/local-backup/backups', () => HttpResponse.json([])),
      http.get('*/api/v1/local-backup/path-check', () => HttpResponse.json({ ok: true })),
    );

    render(<GitHubBackupSettings />);

    const label = await screen.findByText('Output Path');
    const input = label.parentElement?.querySelector('input') as HTMLInputElement;
    expect(input).toBeDisabled();
  });

  it('keeps them for an admin', async () => {
    mockConfiguredBackup();
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () => HttpResponse.json({ id: 1, username: 'admin', is_admin: true, permissions: [] })),
    );

    render(<GitHubBackupSettings />);

    await waitFor(() => expect(screen.getByRole('button', { name: /^Download$/i })).toBeEnabled());
    expect(screen.getByRole('button', { name: /^Restore$/i })).toBeEnabled();
  });
});
