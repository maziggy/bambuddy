/**
 * Tests for api.exportBackup's reading of the server's answer.
 *
 * A refused or failed download used to reach the toast as its raw JSON body,
 * and a backup that misses files says so only in a response header, because
 * the body is the ZIP.
 */

import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { api, ApiError } from '../../api/client';

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const zip = (headers: Record<string, string> = {}) =>
  new HttpResponse('PK', {
    headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': 'attachment; filename="bambuddy-backup-20260412-120000.zip"',
      ...headers,
    },
  });

describe('api.exportBackup', () => {
  it('returns the ZIP with no missing files when the server names none', async () => {
    server.use(http.get('*/settings/backup', () => zip()));

    const result = await api.exportBackup();

    expect(result.filename).toBe('bambuddy-backup-20260412-120000.zip');
    expect(result.missingFiles).toBe(0);
  });

  it('reads how many files the backup misses from the header', async () => {
    server.use(http.get('*/settings/backup', () => zip({ 'X-Backup-Missing-Files': '3' })));

    const result = await api.exportBackup();

    expect(result.missingFiles).toBe(3);
  });

  it('turns the 409 into an ApiError carrying the detail, not the raw JSON', async () => {
    server.use(
      http.get('*/settings/backup', () =>
        HttpResponse.json({ detail: 'A backup is already running' }, { status: 409 })
      )
    );

    const error = await api.exportBackup().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe('A backup is already running');
  });

  it("uses the message of the route's own 500", async () => {
    server.use(
      http.get('*/settings/backup', () =>
        HttpResponse.json(
          { success: false, message: 'Backup failed. Check server logs for details.' },
          { status: 500 }
        )
      )
    );

    await expect(api.exportBackup()).rejects.toThrow('Backup failed. Check server logs for details.');
  });

  it('still reports a failure when the error body is not JSON', async () => {
    server.use(http.get('*/settings/backup', () => new HttpResponse('upstream exploded', { status: 502 })));

    await expect(api.exportBackup()).rejects.toThrow('Backup failed with status 502');
  });
});
