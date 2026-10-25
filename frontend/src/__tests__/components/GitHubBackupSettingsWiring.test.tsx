/**
 * Backup settings wiring: local backup restore/delete target the clicked
 * file and only after the confirm, "Backup Now"/"Run Now" hit their run
 * endpoints, the connection test uses the stored token unless a new one is
 * typed, Clear empties the backup history after a confirm, and a refused
 * delete says so.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { api, setAuthToken } from '../../api/client';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';

type Captured = { method: string; path: string; search: string };
let captured: Captured[];

const CONFIG = {
  id: 1,
  provider: 'github',
  repository_url: 'https://github.com/test/backup-repo',
  has_token: true,
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
  allow_insecure_http: false,
  last_backup_at: null,
  last_backup_status: null,
};

const BACKUPS = [
  { filename: 'bambuddy-backup-2026-10-01.zip', size: 1048576, created_at: '2026-10-01T03:00:00Z' },
  { filename: 'bambuddy backup #2.zip', size: 2097152, created_at: '2026-10-02T03:00:00Z' },
];

function record(request: Request) {
  const url = new URL(request.url);
  captured.push({ method: request.method, path: url.pathname, search: url.search });
}

const writes = () => captured.filter((c) => c.method !== 'GET');

function fileRow(filename: string): HTMLElement {
  return screen.getByText(filename).closest('div.flex.items-center.justify-between') as HTMLElement;
}

beforeEach(() => {
  captured = [];
  server.use(
    http.get('*/api/v1/github-backup/config', () => HttpResponse.json(CONFIG)),
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
    http.get('*/api/v1/github-backup/logs', () =>
      HttpResponse.json([
        {
          id: 1, config_id: 1, started_at: '2026-10-01T09:00:00', completed_at: '2026-10-01T09:00:05',
          status: 'success', trigger: 'manual', commit_sha: null, files_changed: 1, error_message: null,
        },
      ]),
    ),
    http.get('*/api/v1/local-backup/status', () =>
      HttpResponse.json({
        enabled: true, is_running: false, last_backup_at: null, last_status: null,
        last_message: null, next_run: null, path: '/data/backups',
      }),
    ),
    http.get('*/api/v1/local-backup/path-check', () => HttpResponse.json({ writable: true, path: '/data/backups', code: 'ok' })),
    http.get('*/api/v1/local-backup/backups', () => HttpResponse.json(BACKUPS)),
    http.post('*/api/v1/local-backup/backups/:name/restore', ({ request }) => {
      record(request);
      return HttpResponse.json({ success: true, message: 'restored' });
    }),
    http.delete('*/api/v1/local-backup/backups/:name', ({ request }) => {
      record(request);
      return HttpResponse.json({ success: true, message: 'deleted' });
    }),
    http.post('*/api/v1/local-backup/run', ({ request }) => {
      record(request);
      return HttpResponse.json({ success: true, message: 'ok', filename: 'x.zip' });
    }),
    http.post('*/api/v1/github-backup/run', ({ request }) => {
      record(request);
      return HttpResponse.json({ success: true, message: 'ok', files_changed: 0 });
    }),
    http.delete('*/api/v1/github-backup/logs', ({ request }) => {
      record(request);
      return HttpResponse.json({ deleted: 1, message: 'ok' });
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  setAuthToken(null);
});

describe('GitHubBackupSettings wiring', () => {
  it('restore of a local backup asks first, then POSTs restore for the clicked file (URL-encoded)', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('bambuddy backup #2.zip');
    await user.click(within(fileRow('bambuddy backup #2.zip')).getByTitle('Restore'));
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: /^restore backup$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: 'POST',
      path: '/api/v1/local-backup/backups/bambuddy%20backup%20%232.zip/restore',
    });
  });

  it('cancelling the restore confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('bambuddy-backup-2026-10-01.zip');
    await user.click(within(fileRow('bambuddy-backup-2026-10-01.zip')).getByTitle('Restore'));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole('button', { name: /^restore backup$/i })).not.toBeInTheDocument());
    expect(writes()).toHaveLength(0);
  });

  it('delete of a local backup asks first, then DELETEs the clicked file; cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('bambuddy-backup-2026-10-01.zip');
    await user.click(within(fileRow('bambuddy-backup-2026-10-01.zip')).getByTitle('Delete'));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(writes()).toHaveLength(0);

    await user.click(within(fileRow('bambuddy-backup-2026-10-01.zip')).getByTitle('Delete'));
    // The confirm modal's button (the row button has a title, the modal's does not)
    const confirm = screen.getAllByRole('button', { name: /^delete$/i }).find((b) => !b.hasAttribute('title'));
    await user.click(confirm as HTMLElement);

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: 'DELETE',
      path: '/api/v1/local-backup/backups/bambuddy-backup-2026-10-01.zip',
    });
  });

  it('Run Now triggers the local backup', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.click(await screen.findByRole('button', { name: /run now/i }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/local-backup/run' });
  });

  it('Backup Now triggers the Git backup', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.click(await screen.findByRole('button', { name: /backup now/i }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/github-backup/run' });
  });

  it('Test with the token field blank uses the stored token', async () => {
    const stored = vi.spyOn(api, 'testGitHubStoredConnection').mockResolvedValue({ success: true, message: 'ok', is_private: true } as never);
    const typed = vi.spyOn(api, 'testGitHubConnection').mockResolvedValue({ success: true, message: 'ok', is_private: true } as never);
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.click(await screen.findByRole('button', { name: /^test$/i }));
    await waitFor(() => expect(stored).toHaveBeenCalledTimes(1));
    expect(typed).not.toHaveBeenCalled();
  });

  it('Test with a newly typed token tests those credentials, not the stored ones', async () => {
    const stored = vi.spyOn(api, 'testGitHubStoredConnection').mockResolvedValue({ success: true, message: 'ok', is_private: true } as never);
    const typed = vi.spyOn(api, 'testGitHubConnection').mockResolvedValue({ success: true, message: 'ok', is_private: true } as never);
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.type(await screen.findByPlaceholderText(/enter new token to update/i), 'test-token-value-1');
    await user.click(screen.getByRole('button', { name: /^test$/i }));

    await waitFor(() => expect(typed).toHaveBeenCalledTimes(1));
    expect(typed).toHaveBeenCalledWith('https://github.com/test/backup-repo', 'test-token-value-1', 'github');
    expect(stored).not.toHaveBeenCalled();
  });

  it('Clear in the backup history asks first, then deletes all logs (keep_last=0); cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    const history = (await screen.findByText('History')).closest('div[id="card-backup-history"]') as HTMLElement;
    await user.click(within(history).getByRole('button', { name: /^clear$/i }));
    const dialog = () => screen.getByText('Clear backup history?').closest('div.fixed') as HTMLElement;
    await user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText('Clear backup history?')).toBeNull();
    expect(writes()).toEqual([]);

    await user.click(within(history).getByRole('button', { name: /^clear$/i }));
    await user.click(within(dialog()).getByRole('button', { name: /^clear$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/github-backup/logs', search: '?keep_last=0' });
  });

  it.each([
    ['refused (200, success false)', () => HttpResponse.json({ success: false, message: 'Invalid backup file' }), 'Invalid backup file'],
    ['failed (500)', () => HttpResponse.json({ detail: 'Disk is read-only' }, { status: 500 }), 'Disk is read-only'],
  ])('a %s local backup delete is reported', async (_label, reply, text) => {
    server.use(http.delete('*/api/v1/local-backup/backups/:name', reply));
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.click(within(await waitFor(() => fileRow('bambuddy-backup-2026-10-01.zip'))).getByTitle('Delete'));
    const confirm = screen.getAllByRole('button', { name: /^delete$/i }).find((b) => !b.hasAttribute('title'));
    await user.click(confirm!);

    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it('a non-admin gets no Restore (or Download) button on local backups', async () => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({ id: 2, username: 'operator', is_admin: false, permissions: ['settings:read', 'settings:update'] }),
      ),
    );
    render(<GitHubBackupSettings />);

    await screen.findByText('bambuddy-backup-2026-10-01.zip');
    // Wait for the auth state to settle (admin is assumed while auth status loads)
    await waitFor(() =>
      expect(within(fileRow('bambuddy-backup-2026-10-01.zip')).queryByTitle('Restore')).not.toBeInTheDocument(),
    );
    expect(within(fileRow('bambuddy-backup-2026-10-01.zip')).queryByTitle('Download')).not.toBeInTheDocument();
  });

});
