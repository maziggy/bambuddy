/**
 * Tests for the provider selection UI in GitHubBackupSettings.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
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

  it('autosaves existing config changes after debounce', async () => {
    let patchBody: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 3,
          repository_url: 'https://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'gitea',
          allow_insecure_http: false,
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
      ),
      http.patch('/api/v1/github-backup/config', async ({ request }) => {
        patchBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({
          id: 3,
          repository_url: patchBody.repository_url,
          has_token: true,
          branch: patchBody.branch,
          provider: patchBody.provider,
          allow_insecure_http: patchBody.allow_insecure_http,
          schedule_enabled: patchBody.schedule_enabled,
          schedule_type: patchBody.schedule_type,
          backup_kprofiles: patchBody.backup_kprofiles,
          backup_cloud_profiles: patchBody.backup_cloud_profiles,
          backup_settings: patchBody.backup_settings,
          backup_spools: patchBody.backup_spools,
          backup_archives: patchBody.backup_archives,
          enabled: patchBody.enabled,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      })
    );

    render(<GitHubBackupSettings />);

    const branchInput = await screen.findByDisplayValue('main');
    await waitFor(() => expect(branchInput).toHaveValue('main'));

    fireEvent.change(branchInput, { target: { value: 'dev' } });

    await waitFor(() => {
      expect(patchBody).toEqual({ branch: 'dev' });
    }, { timeout: 2000 });
  });

  it('autosaves provider changes after debounce', async () => {
    let patchBody: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 4,
          repository_url: 'https://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'gitea',
          allow_insecure_http: false,
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
      ),
      http.patch('/api/v1/github-backup/config', async ({ request }) => {
        patchBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({
          id: 4,
          repository_url: patchBody.repository_url,
          has_token: true,
          branch: patchBody.branch,
          provider: patchBody.provider,
          allow_insecure_http: patchBody.allow_insecure_http,
          schedule_enabled: patchBody.schedule_enabled,
          schedule_type: patchBody.schedule_type,
          backup_kprofiles: patchBody.backup_kprofiles,
          backup_cloud_profiles: patchBody.backup_cloud_profiles,
          backup_settings: patchBody.backup_settings,
          backup_spools: patchBody.backup_spools,
          backup_archives: patchBody.backup_archives,
          enabled: patchBody.enabled,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      })
    );

    render(<GitHubBackupSettings />);

    const providerSelect = await screen.findByRole('combobox', { name: /git provider/i });
    await waitFor(() => expect(providerSelect).toHaveValue('gitea'));

    fireEvent.change(providerSelect, { target: { value: 'forgejo' } });

    await waitFor(() => {
      expect(patchBody).toEqual({ provider: 'forgejo' });
    }, { timeout: 2000 });
  });

  it('autosaves repository URL changes after debounce', async () => {
    let patchBody: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 5,
          repository_url: 'https://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'gitea',
          allow_insecure_http: false,
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
      ),
      http.patch('/api/v1/github-backup/config', async ({ request }) => {
        patchBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({
          id: 5,
          repository_url: patchBody.repository_url,
          has_token: true,
          branch: patchBody.branch,
          provider: patchBody.provider,
          allow_insecure_http: patchBody.allow_insecure_http,
          schedule_enabled: patchBody.schedule_enabled,
          schedule_type: patchBody.schedule_type,
          backup_kprofiles: patchBody.backup_kprofiles,
          backup_cloud_profiles: patchBody.backup_cloud_profiles,
          backup_settings: patchBody.backup_settings,
          backup_spools: patchBody.backup_spools,
          backup_archives: patchBody.backup_archives,
          enabled: patchBody.enabled,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      })
    );

    render(<GitHubBackupSettings />);

    const repoInput = await screen.findByDisplayValue('https://git.example.com/owner/repo');
    fireEvent.change(repoInput, { target: { value: 'https://git.example.com/owner/other-repo' } });

    await waitFor(() => {
      expect(patchBody).toEqual({
        repository_url: 'https://git.example.com/owner/other-repo',
      });
    }, { timeout: 2000 });
  });

  it('does not let pending token autosave cancel provider settings autosave', async () => {
    let patchBody: Record<string, unknown> | null = null;
    let postBody: Record<string, unknown> | null = null;

    server.use(
      http.get('/api/v1/github-backup/config', () =>
        HttpResponse.json({
          id: 6,
          repository_url: 'http://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: 'gitea',
          allow_insecure_http: true,
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
      ),
      http.patch('/api/v1/github-backup/config', async ({ request }) => {
        patchBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({
          id: 6,
          repository_url: 'http://git.example.com/owner/repo',
          has_token: true,
          branch: 'main',
          provider: patchBody.provider ?? 'gitea',
          allow_insecure_http: true,
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
        });
      }),
      http.post('/api/v1/github-backup/config', async ({ request }) => {
        postBody = await request.json() as Record<string, unknown>;
        return HttpResponse.json({
          id: 6,
          repository_url: postBody.repository_url,
          has_token: true,
          branch: postBody.branch,
          provider: postBody.provider,
          allow_insecure_http: postBody.allow_insecure_http,
          schedule_enabled: postBody.schedule_enabled,
          schedule_type: postBody.schedule_type,
          backup_kprofiles: postBody.backup_kprofiles,
          backup_cloud_profiles: postBody.backup_cloud_profiles,
          backup_settings: postBody.backup_settings,
          backup_spools: postBody.backup_spools,
          backup_archives: postBody.backup_archives,
          enabled: postBody.enabled,
          last_backup_at: null,
          last_backup_status: null,
          last_backup_message: null,
          last_backup_commit_sha: null,
          next_scheduled_run: null,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        });
      })
    );

    render(<GitHubBackupSettings />);

    const tokenInput = await screen.findByPlaceholderText('Enter new token to update');
    fireEvent.change(tokenInput, { target: { value: 'new-token' } });

    const providerSelect = await screen.findByRole('combobox', { name: /git provider/i });
    fireEvent.change(providerSelect, { target: { value: 'forgejo' } });

    await waitFor(() => {
      expect(patchBody).toEqual({ provider: 'forgejo' });
    }, { timeout: 2000 });
  });
});
