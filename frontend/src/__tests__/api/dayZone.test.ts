/**
 * Calendar dates go to the server with the browser's zone (DB-C12).
 *
 * The server reads a date as a day in the zone it is sent with, else in its own
 * TZ -- which, for a browser away from the server, shifted every Stats day.
 */

import { describe, it, expect, afterAll, beforeAll } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { api } from '../../api/client';

const originalTz = process.env.TZ;

beforeAll(() => {
  process.env.TZ = 'Asia/Tokyo';
});

afterAll(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

function capture(path: string, body: unknown): URL[] {
  const seen: URL[] = [];
  server.use(
    http.get(path, ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json(body);
    }),
  );
  return seen;
}

describe('date filters carry the browser zone', () => {
  it('sends it with the dates', async () => {
    const seen = capture('/api/v1/archives/slim', []);
    await api.getArchivesSlim('2026-10-17', '2026-10-17');

    expect(seen[0].searchParams.get('tz')).toBe('Asia/Tokyo');
    expect(seen[0].searchParams.get('date_from')).toBe('2026-10-17');
  });

  it('sends it for the statistics totals too', async () => {
    const seen = capture('/api/v1/archives/stats', {});
    await api.getArchiveStats({ dateFrom: '2026-10-17', dateTo: '2026-10-17' });

    expect(seen[0].searchParams.get('tz')).toBe('Asia/Tokyo');
  });

  it('leaves it out without dates', async () => {
    const seen = capture('/api/v1/archives/slim', []);
    await api.getArchivesSlim();

    expect(seen[0].searchParams.has('tz')).toBe(false);
  });
});
