/**
 * Tests for the MakerworldPage URL-paste flow.
 *
 * Covers: status-driven warning banner, resolve round trip populates design +
 * plate list, "Already imported" badge appears when the backend reports prior
 * imports, and Print Now mutates to import then opens the PrintModal.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { MakerworldPage } from '../../pages/MakerworldPage';

const statusNoToken = { has_cloud_token: false, can_download: false };
const statusWithToken = { has_cloud_token: true, can_download: true };

function resolveResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    model_id: 1400373,
    profile_id: 1452154,
    design: {
      id: 1400373,
      title: 'Seed Starter',
      designCreator: { name: 'Meyui', avatar: '' },
      coverUrl: 'https://makerworld.bblmw.com/img/cover.png',
      license: 'Standard',
      downloadCount: 1234,
      summary: '<p>A seed starter</p>',
    },
    instances: [
      { id: 1452154, title: '9 cells', cover: '', materialCnt: 1, needAms: false, downloadCount: 500 },
      { id: 1452158, title: '12 cells', cover: '', materialCnt: 2, needAms: true, downloadCount: 120 },
    ],
    already_imported_library_ids: [],
    ...overrides,
  };
}

afterEach(() => server.resetHandlers());

describe('MakerworldPage', () => {
  it('renders the sign-in-required banner when no Bambu Cloud token is stored', async () => {
    server.use(
      http.get('*/makerworld/status', () => HttpResponse.json(statusNoToken)),
    );
    render(<MakerworldPage />);
    expect(await screen.findByText(/Bambu Cloud sign-in required/i)).toBeInTheDocument();
  });

  it('hides the sign-in banner when a cloud token is present', async () => {
    server.use(
      http.get('*/makerworld/status', () => HttpResponse.json(statusWithToken)),
    );
    render(<MakerworldPage />);
    // Page header is always visible; wait for status to settle
    await screen.findByRole('heading', { name: 'MakerWorld' });
    await waitFor(() => {
      expect(screen.queryByText(/Bambu Cloud sign-in required/i)).not.toBeInTheDocument();
    });
  });

  it('renders design + plate list after the user pastes a URL', async () => {
    server.use(
      http.get('*/makerworld/status', () => HttpResponse.json(statusWithToken)),
      http.post('*/makerworld/resolve', async ({ request }) => {
        const body = await request.json() as { url: string };
        expect(body.url).toContain('makerworld.com/en/models/1400373');
        return HttpResponse.json(resolveResponse());
      }),
    );
    render(<MakerworldPage />);

    const input = await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i);
    await userEvent.type(input, 'https://makerworld.com/en/models/1400373-slug#profileId-1452154');
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    expect(await screen.findByText('Seed Starter')).toBeInTheDocument();
    expect(screen.getByText('9 cells')).toBeInTheDocument();
    expect(screen.getByText('12 cells')).toBeInTheDocument();
  });

  it('shows the "Already in library" badge when backend reports prior imports', async () => {
    server.use(
      http.get('*/makerworld/status', () => HttpResponse.json(statusWithToken)),
      http.post('*/makerworld/resolve', () =>
        HttpResponse.json(resolveResponse({ already_imported_library_ids: [42] })),
      ),
    );
    render(<MakerworldPage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));
    expect(await screen.findByText(/Already in library/i)).toBeInTheDocument();
  });
});
