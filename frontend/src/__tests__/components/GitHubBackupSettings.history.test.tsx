/**
 * Backup History must distinguish a restore from a backup (#2656).
 *
 * A restore writes a `github_backup_logs` row too — same table, same statuses,
 * `trigger: 'restore'`. The table rendered date / status / commit only, so the
 * row read as a successful backup dated now, while "Last backup" said something
 * else entirely. The column below is the only thing telling the two apart.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import { render } from '../utils';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getGitHubBackupConfig: vi.fn().mockResolvedValue({
      id: 1,
      repository_url: 'https://github.com/someone/backup',
      enabled: true,
    }),
    getGitHubBackupStatus: vi.fn().mockResolvedValue({ is_running: false, configured: true, enabled: true }),
    getGitHubBackupLogs: vi.fn(),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue({ connected: false }),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    getLocalBackups: vi.fn().mockResolvedValue([]),
    getLocalBackupStatus: vi.fn().mockResolvedValue({
      enabled: false,
      is_running: false,
      last_backup_at: null,
      last_status: null,
      last_message: null,
      next_run: null,
    }),
    checkLocalBackupPath: vi.fn().mockResolvedValue({ writable: true, path: '/data', code: 'ok' }),
  },
}));

const log = (id: number, trigger: string) => ({
  id,
  config_id: 1,
  started_at: '2026-08-02T09:00:00',
  completed_at: '2026-08-02T09:00:05',
  status: 'success',
  trigger,
  commit_sha: null,
  files_changed: 0,
  error_message: null,
});

const historyRows = async () => {
  const table = (await screen.findByText('History')).closest('div[id="card-backup-history"]');
  return within(table as HTMLElement).getAllByRole('row').slice(1); // drop the header
};

describe('GitHubBackupSettings — backup history', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('labels a restore as a restore, not a successful backup', async () => {
    vi.mocked(api.getGitHubBackupLogs).mockResolvedValue([log(1, 'restore')]);

    render(<GitHubBackupSettings />);

    const [row] = await historyRows();
    expect(within(row).getByText('Restore')).toBeInTheDocument();
  });

  it('tells the three trigger kinds apart in one history', async () => {
    vi.mocked(api.getGitHubBackupLogs).mockResolvedValue([
      log(1, 'restore'),
      log(2, 'scheduled'),
      log(3, 'manual'),
    ]);

    render(<GitHubBackupSettings />);

    const rows = await historyRows();
    expect(within(rows[0]).getByText('Restore')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Backup (scheduled)')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Backup (manual)')).toBeInTheDocument();
  });

  it('falls back to the raw trigger rather than blanking an unknown one', async () => {
    vi.mocked(api.getGitHubBackupLogs).mockResolvedValue([log(1, 'something-new')]);

    render(<GitHubBackupSettings />);

    const [row] = await historyRows();
    expect(within(row).getByText('something-new')).toBeInTheDocument();
  });
});
