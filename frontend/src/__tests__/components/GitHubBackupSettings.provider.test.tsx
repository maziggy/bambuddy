/**
 * Tests for the provider selection UI in GitHubBackupSettings.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { GitHubBackupSettings } from '../../components/GitHubBackupSettings';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const baseHandlers = () => [
  http.get('/api/v1/github-backup/config', () => HttpResponse.json(null)),
  http.get('/api/v1/github-backup/status', () =>
    HttpResponse.json({
      configured: false,
      enabled: false,
      is_running: false,
      progress: null,
      last_backup_at: null,
      last_backup_status: null,
      next_scheduled_run: null,
    })
  ),
  http.get('/api/v1/github-backup/logs', () => HttpResponse.json([])),
  http.get('/api/v1/local-backup/status', () =>
    HttpResponse.json({
      enabled: false,
      schedule: 'daily',
      time: '03:00',
      retention: 5,
      path: '',
      default_path: '/data/backups',
      is_running: false,
      last_backup_at: null,
      last_status: null,
      last_message: null,
      next_run: null,
    })
  ),
  http.get('/api/v1/local-backup/backups', () => HttpResponse.json([])),
  http.get('/api/v1/cloud/status', () => HttpResponse.json({ is_authenticated: false })),
  http.get('/api/v1/printers', () => HttpResponse.json([])),
  http.put('/api/v1/settings/', () => HttpResponse.json({})),
];

describe('GitHubBackupSettings - Provider Selection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(...baseHandlers());
  });

  it('renders provider dropdown with GitHub selected by default', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Git Provider')).toBeInTheDocument();
    });
    const select = screen.getByRole('combobox', { name: /git provider/i });
    expect(select).toHaveValue('github');
  });

  it('does not show instance URL field for any provider', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByText('Git Provider')).toBeInTheDocument();
    });
    expect(screen.queryByText('Instance URL')).not.toBeInTheDocument();
  });

  it('loads provider from existing config', async () => {
    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 1,
          repository_url: 'https://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'gitea',
          schedule_enabled: false,
          schedule_type: 'daily',
          backup_kprofiles: true,
          backup_cloud_profiles: true,
          backup_settings: false,
          backup_spools: false,
          backup_archives: false,
          enabled: true,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        })
      )
    );
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: /git provider/i });
      expect(select).toHaveValue('gitea');
    });
  });

  it('renders Forgejo as a separate dropdown option', async () => {
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /git provider/i })).toBeInTheDocument();
    });
    const select = screen.getByRole('combobox', { name: /git provider/i });
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toContain('gitea');
    expect(options).toContain('forgejo');
  });

  it('loads forgejo provider from existing config', async () => {
    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 2,
          repository_url: 'https://forgejo.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'forgejo',
          schedule_enabled: false,
          schedule_type: 'daily',
          backup_kprofiles: true,
          backup_cloud_profiles: true,
          backup_settings: false,
          backup_spools: false,
          backup_archives: false,
          enabled: true,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        })
      )
    );
    render(<GitHubBackupSettings />);
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: /git provider/i });
      expect(select).toHaveValue('forgejo');
    });
  });
});
