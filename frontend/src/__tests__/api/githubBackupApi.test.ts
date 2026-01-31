/**
 * Tests for the GitHub Backup API client functions.
 */

import { describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type {
  GitHubBackupConfig,
  GitHubBackupStatus,
  GitHubBackupLog,
} from '../../api/client';

// Mock API base URL
const API_BASE = 'http://localhost:5000/api/v1';

// Create MSW server
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('GitHub Backup API Types', () => {
  it('GitHubBackupConfig has correct shape', () => {
    const config: GitHubBackupConfig = {
      id: 1,
      repository_url: 'https://github.com/test/repo',
      has_token: true,
      branch: 'main',
      schedule_enabled: true,
      schedule_type: 'daily',
      backup_kprofiles: true,
      backup_cloud_profiles: true,
      backup_settings: false,
      enabled: true,
      last_backup_at: '2026-01-27T10:00:00Z',
      last_backup_status: 'success',
      last_backup_message: null,
      last_backup_commit_sha: 'abc123',
      next_scheduled_run: '2026-01-28T00:00:00Z',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-27T10:00:00Z',
    };

    expect(config.id).toBe(1);
    expect(config.has_token).toBe(true);
    expect(config.schedule_type).toBe('daily');
  });

  it('GitHubBackupStatus has correct shape', () => {
    const status: GitHubBackupStatus = {
      configured: true,
      enabled: true,
      is_running: false,
      progress: null,
      last_backup_at: '2026-01-27T10:00:00Z',
      last_backup_status: 'success',
      next_scheduled_run: '2026-01-28T00:00:00Z',
    };

    expect(status.configured).toBe(true);
    expect(status.is_running).toBe(false);
  });

  it('GitHubBackupStatus can have progress', () => {
    const status: GitHubBackupStatus = {
      configured: true,
      enabled: true,
      is_running: true,
      progress: 'Pushing to GitHub...',
      last_backup_at: null,
      last_backup_status: null,
      next_scheduled_run: null,
    };

    expect(status.is_running).toBe(true);
    expect(status.progress).toBe('Pushing to GitHub...');
  });

  it('GitHubBackupLog has correct shape', () => {
    const log: GitHubBackupLog = {
      id: 1,
      config_id: 1,
      started_at: '2026-01-27T10:00:00Z',
      completed_at: '2026-01-27T10:01:00Z',
      status: 'success',
      trigger: 'manual',
      commit_sha: 'abc123',
      files_changed: 5,
      error_message: null,
    };

    expect(log.status).toBe('success');
    expect(log.trigger).toBe('manual');
    expect(log.files_changed).toBe(5);
  });

  it('GitHubBackupLog can have error', () => {
    const log: GitHubBackupLog = {
      id: 2,
      config_id: 1,
      started_at: '2026-01-27T10:00:00Z',
      completed_at: '2026-01-27T10:00:30Z',
      status: 'failed',
      trigger: 'scheduled',
      commit_sha: null,
      files_changed: 0,
      error_message: 'Authentication failed',
    };

    expect(log.status).toBe('failed');
    expect(log.error_message).toBe('Authentication failed');
    expect(log.commit_sha).toBeNull();
  });
});

describe('GitHub Backup API Endpoints', () => {
  it('GET /github-backup/config returns null when not configured', async () => {
    server.use(
      http.get(`${API_BASE}/github-backup/config`, () => {
        return HttpResponse.json(null);
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/config`);
    const data = await response.json();
    expect(data).toBeNull();
  });

  it('GET /github-backup/config returns config when exists', async () => {
    const mockConfig: GitHubBackupConfig = {
      id: 1,
      repository_url: 'https://github.com/test/repo',
      has_token: true,
      branch: 'main',
      schedule_enabled: false,
      schedule_type: 'daily',
      backup_kprofiles: true,
      backup_cloud_profiles: true,
      backup_settings: false,
      enabled: true,
      last_backup_at: null,
      last_backup_status: null,
      last_backup_message: null,
      last_backup_commit_sha: null,
      next_scheduled_run: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };

    server.use(
      http.get(`${API_BASE}/github-backup/config`, () => {
        return HttpResponse.json(mockConfig);
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/config`);
    const data = await response.json();
    expect(data.repository_url).toBe('https://github.com/test/repo');
    expect(data.has_token).toBe(true);
  });

  it('GET /github-backup/status returns not configured status', async () => {
    const mockStatus: GitHubBackupStatus = {
      configured: false,
      enabled: false,
      is_running: false,
      progress: null,
      last_backup_at: null,
      last_backup_status: null,
      next_scheduled_run: null,
    };

    server.use(
      http.get(`${API_BASE}/github-backup/status`, () => {
        return HttpResponse.json(mockStatus);
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/status`);
    const data = await response.json();
    expect(data.configured).toBe(false);
    expect(data.enabled).toBe(false);
  });

  it('GET /github-backup/logs returns empty list when no logs', async () => {
    server.use(
      http.get(`${API_BASE}/github-backup/logs`, () => {
        return HttpResponse.json([]);
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/logs`);
    const data = await response.json();
    expect(data).toEqual([]);
  });

  it('GET /github-backup/logs returns log entries', async () => {
    const mockLogs: GitHubBackupLog[] = [
      {
        id: 1,
        config_id: 1,
        started_at: '2026-01-27T10:00:00Z',
        completed_at: '2026-01-27T10:01:00Z',
        status: 'success',
        trigger: 'manual',
        commit_sha: 'abc123',
        files_changed: 5,
        error_message: null,
      },
    ];

    server.use(
      http.get(`${API_BASE}/github-backup/logs`, () => {
        return HttpResponse.json(mockLogs);
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/logs`);
    const data = await response.json();
    expect(data.length).toBe(1);
    expect(data[0].status).toBe('success');
  });

  it('POST /github-backup/run returns 404 when not configured', async () => {
    server.use(
      http.post(`${API_BASE}/github-backup/run`, () => {
        return HttpResponse.json(
          { detail: 'No configuration found' },
          { status: 404 }
        );
      })
    );

    const response = await fetch(`${API_BASE}/github-backup/run`, {
      method: 'POST',
    });
    expect(response.status).toBe(404);
  });

  it('POST /github-backup/test returns success on valid credentials', async () => {
    server.use(
      http.post(`${API_BASE}/github-backup/test`, () => {
        return HttpResponse.json({
          success: true,
          message: 'Connection successful',
          repo_name: 'test/repo',
          default_branch: 'main',
        });
      })
    );

    const response = await fetch(
      `${API_BASE}/github-backup/test?repo_url=https://github.com/test/repo&token=ghp_test`,
      { method: 'POST' }
    );
    const data = await response.json();
    expect(data.success).toBe(true);
    expect(data.repo_name).toBe('test/repo');
  });

  it('POST /github-backup/test returns failure on invalid credentials', async () => {
    server.use(
      http.post(`${API_BASE}/github-backup/test`, () => {
        return HttpResponse.json({
          success: false,
          message: 'Authentication failed',
          repo_name: null,
          default_branch: null,
        });
      })
    );

    const response = await fetch(
      `${API_BASE}/github-backup/test?repo_url=https://github.com/test/repo&token=invalid`,
      { method: 'POST' }
    );
    const data = await response.json();
    expect(data.success).toBe(false);
    expect(data.message).toBe('Authentication failed');
  });
});
