/**
 * Tests for the Scheduled Local Backup UI in GitHubBackupSettings.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockLocalBackupStatus = {
  enabled: true,
  schedule: 'daily',
  time: '03:00',
  retention: 5,
  path: '',
  default_path: '/data/backups',
  is_running: false,
  last_backup_at: null,
  last_status: null,
  last_message: null,
  last_missing_files: 0,
  next_run: '2026-04-13T03:00:00+00:00',
  timezone: 'Europe/Berlin',
};

const mockLocalBackups = [
  {
    filename: 'bambuddy-backup-20260412-120000.zip',
    size: 52428800,
    created_at: '2026-04-12T12:00:00+00:00',
  },
];

describe('GitHubBackupSettings - Scheduled Backups', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/local-backup/status', () =>
        HttpResponse.json(mockLocalBackupStatus)
      ),
      http.get('/api/v1/local-backup/backups', () =>
        HttpResponse.json(mockLocalBackups)
      ),
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json(null)
      ),
      http.get('/api/v1/github-backup/status', () =>
        HttpResponse.json({ configured: false, enabled: false, is_running: false, progress: null, last_backup_at: null, last_backup_status: null, next_scheduled_run: null })
      ),
      http.get('/api/v1/github-backup/logs', () =>
        HttpResponse.json([])
      ),
      http.get('/api/v1/cloud/status', () =>
        HttpResponse.json({ is_authenticated: false })
      ),
      http.get('/api/v1/printers', () =>
        HttpResponse.json([])
      ),
      http.put('/api/v1/settings/', () =>
        HttpResponse.json({})
      ),
    );
  });

  it('renders Scheduled Backups card title', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Scheduled Backups')).toBeInTheDocument();
    });
  });

  it('shows frequency dropdown when enabled', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Frequency')).toBeInTheDocument();
    });
  });

  it('shows retention input when enabled', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Retention')).toBeInTheDocument();
    });
  });

  it('shows backup file list', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('bambuddy-backup-20260412-120000.zip')).toBeInTheDocument();
    });
  });

  it('shows file size in MB', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText(/50\.0 MB/)).toBeInTheDocument();
    });
  });

  it('shows Run Now button', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Run Now')).toBeInTheDocument();
    });
  });

  it('shows default path when path is empty', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('/data/backups')).toBeInTheDocument();
    });
  });

  it('hides schedule controls when disabled', async () => {
    server.use(
      http.get('/api/v1/local-backup/status', () =>
        HttpResponse.json({ ...mockLocalBackupStatus, enabled: false })
      ),
    );
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Scheduled Backups')).toBeInTheDocument();
    });
    expect(screen.queryByText('Frequency')).not.toBeInTheDocument();
    expect(screen.queryByText('Run Now')).not.toBeInTheDocument();
  });

  it('hides time picker when hourly is selected', async () => {
    server.use(
      http.get('/api/v1/local-backup/status', () =>
        HttpResponse.json({ ...mockLocalBackupStatus, schedule: 'hourly' })
      ),
    );
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Frequency')).toBeInTheDocument();
    });
    expect(screen.queryByText('Time')).not.toBeInTheDocument();
  });
});

describe('GitHubBackupSettings - a backup that is running or incomplete', () => {
  const alreadyRunning = 'A backup is already running. Try again in a moment.';
  const missingFiles = (count: number) =>
    `Backup finished, but some files could not be copied (not copied: ${count}). The log lists them.`;

  // The list's icon buttons are called Download too, but only by their title.
  const downloadButton = async () => (await screen.findByText('Download')).closest('button') as HTMLButtonElement;

  beforeEach(() => {
    vi.clearAllMocks();
    URL.createObjectURL = vi.fn(() => 'blob:backup');
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    server.use(
      http.get('/api/v1/local-backup/status', () => HttpResponse.json(mockLocalBackupStatus)),
      http.get('/api/v1/local-backup/backups', () => HttpResponse.json(mockLocalBackups)),
      http.get('/api/v1/github-backup/config', () => HttpResponse.json(null)),
      http.get('/api/v1/github-backup/logs', () => HttpResponse.json([])),
      http.get('/api/v1/cloud/status', () => HttpResponse.json({ is_authenticated: false })),
      http.get('/api/v1/printers', () => HttpResponse.json([])),
    );
  });

  it('disables Download while a local backup runs', async () => {
    server.use(
      http.get('/api/v1/local-backup/status', () =>
        HttpResponse.json({ ...mockLocalBackupStatus, is_running: true })
      ),
    );
    render(<GitHubBackupSettings />);

    await waitFor(async () => {
      expect(await downloadButton()).toBeDisabled();
    });
    expect(await downloadButton()).toHaveAttribute('title', alreadyRunning);
  });

  it('leaves Download enabled when no backup runs', async () => {
    render(<GitHubBackupSettings />);

    await screen.findByText('Run Now');
    expect(await downloadButton()).toBeEnabled();
  });

  it('shows the 409 as the translated message, not its JSON', async () => {
    server.use(
      http.get('*/api/v1/settings/backup', () =>
        HttpResponse.json({ detail: 'A backup is already running' }, { status: 409 })
      ),
    );
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('Run Now');
    await user.click(await downloadButton());

    expect(await screen.findByText(alreadyRunning)).toBeInTheDocument();
    expect(screen.queryByText(/Failed to create backup/)).not.toBeInTheDocument();
  });

  it('still reports any other failed download as a failure', async () => {
    server.use(
      http.get('*/api/v1/settings/backup', () =>
        HttpResponse.json(
          { success: false, message: 'Backup failed. Check server logs for details.' },
          { status: 500 }
        )
      ),
    );
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('Run Now');
    await user.click(await downloadButton());

    expect(
      await screen.findByText('Failed to create backup: Backup failed. Check server logs for details.')
    ).toBeInTheDocument();
  });

  it('warns when a downloaded backup misses files', async () => {
    server.use(
      http.get('*/api/v1/settings/backup', () =>
        new HttpResponse('PK', {
          headers: { 'Content-Type': 'application/zip', 'X-Backup-Missing-Files': '2' },
        })
      ),
    );
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await screen.findByText('Run Now');
    await user.click(await downloadButton());

    expect(await screen.findByText(missingFiles(2))).toBeInTheDocument();
    expect(screen.queryByText('Backup downloaded')).not.toBeInTheDocument();
  });

  it('warns when Run Now made a backup that misses files', async () => {
    server.use(
      http.post('/api/v1/local-backup/run', () =>
        HttpResponse.json({
          success: true,
          message: 'Backup created, but 1 file(s) could not be copied: archive/1/gone.3mf',
          filename: 'bambuddy-backup-20260412-120000.zip',
          missing_files: 1,
        })
      ),
    );
    const user = userEvent.setup();
    render(<GitHubBackupSettings />);

    await user.click(await screen.findByText('Run Now'));

    expect(await screen.findByText(missingFiles(1))).toBeInTheDocument();
    expect(screen.queryByText('Backup completed successfully')).not.toBeInTheDocument();
  });

  it('keeps saying so next to the last backup, for a scheduled one nobody watched', async () => {
    server.use(
      http.get('/api/v1/local-backup/status', () =>
        HttpResponse.json({
          ...mockLocalBackupStatus,
          last_backup_at: '2026-04-12T03:00:00+00:00',
          last_status: 'warning',
          last_message: 'Backup created, but 4 file(s) could not be copied: archive/1/gone.3mf',
          last_missing_files: 4,
        })
      ),
    );
    render(<GitHubBackupSettings />);

    expect(await screen.findByText('Warning')).toBeInTheDocument();
    expect(screen.getByText(missingFiles(4))).toBeInTheDocument();
  });
});
