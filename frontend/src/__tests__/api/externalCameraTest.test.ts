/**
 * The camera test sends the camera URL, which may carry the camera login, in
 * its body: a query string ends up in the server's access log.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { api } from '../../api/client';

afterEach(() => server.resetHandlers());

describe('testExternalCamera', () => {
  it('sends the URL in the body, not the query', async () => {
    let seenUrl = '';
    let body: unknown = null;
    server.use(
      http.post('*/api/v1/printers/:id/camera/external/test', async ({ request }) => {
        seenUrl = request.url;
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );

    await api.testExternalCamera(3, 'rtsp://cam:pw123@10.0.0.5/s', 'rtsp');

    expect(seenUrl).not.toContain('pw123');
    expect(new URL(seenUrl).search).toBe('');
    expect(body).toEqual({ url: 'rtsp://cam:pw123@10.0.0.5/s', camera_type: 'rtsp' });
  });
});
