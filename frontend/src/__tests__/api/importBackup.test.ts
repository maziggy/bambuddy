/**
 * Tests for api.importBackup's handling of a refused restore.
 *
 * A restore the server declines is an HTTPException, so its body is
 * `{detail}` — not the `{success, message}` a successful restore returns.
 * importBackup used to hand that body straight back, which left `success`
 * undefined (falsy, so the UI took the failure branch) and `message`
 * undefined with it: the operator got an empty error toast and no idea why.
 *
 * It matters most for exactly the case it was found in — a backup this version
 * cannot import. That refusal names the columns and both version numbers, and
 * all of it was being dropped on the floor.
 */

import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { api } from '../../api/client';

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const backupFile = () => new File(['(a backup)'], 'backup.zip', { type: 'application/zip' });

describe('api.importBackup', () => {
  it('passes a successful restore through unchanged', async () => {
    server.use(
      http.post('*/settings/restore', () =>
        HttpResponse.json({ success: true, message: 'Backup restored successfully.' })
      )
    );

    const result = await api.importBackup(backupFile());

    expect(result.success).toBe(true);
    expect(result.message).toBe('Backup restored successfully.');
  });

  it('turns a refusal into a failure carrying the reason', async () => {
    const detail =
      'This backup cannot be restored by this version of Bambuddy. It carries no value for ' +
      '1 column(s) this version requires and cannot default: cost_centers.name.';
    server.use(http.post('*/settings/restore', () => HttpResponse.json({ detail }, { status: 400 })));

    const result = await api.importBackup(backupFile());

    expect(result.success).toBe(false);
    expect(result.message).toBe(detail);
  });

  it('reports a failure even when the error body is not JSON', async () => {
    server.use(
      http.post('*/settings/restore', () => new HttpResponse('upstream exploded', { status: 502 }))
    );

    const result = await api.importBackup(backupFile());

    expect(result.success).toBe(false);
    expect(result.message).toBe('');
  });
});
