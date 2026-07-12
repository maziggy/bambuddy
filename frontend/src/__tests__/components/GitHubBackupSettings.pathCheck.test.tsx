/**
 * The backup card must say when it cannot write to the output directory (#2544).
 *
 * The reporter's NAS share was read-only *to the service* (our systemd unit ships
 * ProtectSystem=strict), his shell wrote to it fine, and the UI only ever said
 * "Failed". A week of nightly backups went nowhere. The banner below is what
 * turns that into something actionable.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getGitHubBackupConfig: vi.fn().mockResolvedValue(null),
    getGitHubBackupStatus: vi.fn().mockResolvedValue({ is_running: false, configured: false, enabled: false }),
    getGitHubBackupLogs: vi.fn().mockResolvedValue([]),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue({ connected: false }),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    getLocalBackups: vi.fn().mockResolvedValue([]),
    getLocalBackupStatus: vi.fn().mockResolvedValue({
      enabled: true,
      schedule: 'daily',
      time: '07:00',
      retention: 30,
      path: '/mnt/nasbackup',
      default_path: '/app/data/backups',
      is_running: false,
      last_backup_at: null,
      last_status: null,
      last_message: null,
      next_run: null,
      timezone: 'America/New_York',
    }),
    checkLocalBackupPath: vi.fn(),
  },
}));

const SANDBOXED = {
  writable: false,
  path: '/mnt/nasbackup',
  code: 'sandboxed',
  detail: "[Errno 30] Read-only file system: '/mnt/nasbackup/.bambuddy-write-test-x'",
  remedy: 'sudo systemctl edit bambuddy.service\n\n[Service]\nReadWritePaths=/mnt/nasbackup',
  message: '/mnt/nasbackup is read-only for the Bambuddy service.',
  warning: null,
};

describe('GitHubBackupSettings — backup path check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('explains an unwritable path instead of leaving the user with an errno', async () => {
    vi.mocked(api.checkLocalBackupPath).mockResolvedValue(SANDBOXED);

    render(<GitHubBackupSettings />);

    expect(await screen.findByText(/cannot write to this directory/i)).toBeInTheDocument();
    // The cause, not just the symptom.
    expect(screen.getByText(/ProtectSystem=strict/)).toBeInTheDocument();
    // And the raw OS error is still there for anyone who wants it.
    expect(screen.getByText(/Errno 30/)).toBeInTheDocument();
  });

  it('shows the drop-in that fixes it, with the operator\'s own path in it', async () => {
    vi.mocked(api.checkLocalBackupPath).mockResolvedValue(SANDBOXED);

    render(<GitHubBackupSettings />);

    const remedy = await screen.findByText(/ReadWritePaths=\/mnt\/nasbackup/);
    expect(remedy).toBeInTheDocument();
    expect(remedy.textContent).toContain('systemctl edit bambuddy.service');
  });

  it('warns when the path is writable but lives inside the container', async () => {
    vi.mocked(api.checkLocalBackupPath).mockResolvedValue({
      writable: true,
      path: '/backups',
      code: 'ok',
      detail: null,
      remedy: 'services:\n  bambuddy:\n    volumes:\n      - /backups:/backups',
      message: '/backups is writable.',
      warning: 'container_ephemeral',
    });

    render(<GitHubBackupSettings />);

    expect(await screen.findByText(/will not survive a container restart/i)).toBeInTheDocument();
  });

  it('stays quiet when the directory is fine', async () => {
    vi.mocked(api.checkLocalBackupPath).mockResolvedValue({
      writable: true,
      path: '/mnt/nasbackup',
      code: 'ok',
      detail: null,
      remedy: null,
      message: '/mnt/nasbackup is writable.',
      warning: null,
    });

    render(<GitHubBackupSettings />);

    await waitFor(() => expect(api.checkLocalBackupPath).toHaveBeenCalled());
    expect(screen.queryByText(/cannot write to this directory/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/will not survive a container restart/i)).not.toBeInTheDocument();
  });
});
