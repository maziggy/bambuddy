/**
 * The load / unload endpoints take optional parameters, and "optional" has to
 * mean absent rather than the string "undefined": the backend validates
 * extruder_id as 0-1 and tray_id as an addressable slot, so a stray literal is
 * a 422 on an action that used to work.
 */

import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { api } from '../../api/client';

let lastUrl = '';
const server = setupServer(
  http.post('*/printers/:id/ams/load', ({ request }) => {
    lastUrl = new URL(request.url).search;
    return HttpResponse.json({ success: true, message: 'ok' });
  }),
  http.post('*/printers/:id/ams/unload', ({ request }) => {
    lastUrl = new URL(request.url).search;
    return HttpResponse.json({ success: true, message: 'ok' });
  })
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  lastUrl = '';
});
afterAll(() => server.close());

describe('AMS load/unload query parameters', () => {
  it('omits extruder_id entirely when no hotend was chosen', async () => {
    await api.loadAmsTray(1, 5);
    expect(lastUrl).toBe('?tray_id=5');
  });

  it('sends extruder_id when a hotend was chosen', async () => {
    await api.loadAmsTray(1, 5, 1);
    expect(lastUrl).toBe('?tray_id=5&extruder_id=1');
  });

  it('sends extruder_id 0 rather than dropping it as falsy', async () => {
    await api.loadAmsTray(1, 5, 0);
    expect(lastUrl).toBe('?tray_id=5&extruder_id=0');
  });

  it('omits tray_id on an unaddressed unload', async () => {
    await api.unloadAms(1);
    expect(lastUrl).toBe('');
  });

  it('sends tray_id 0 on an unload of the first slot', async () => {
    await api.unloadAms(1, 0);
    expect(lastUrl).toBe('?tray_id=0');
  });
});
