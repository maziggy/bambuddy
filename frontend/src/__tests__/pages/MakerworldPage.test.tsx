/**
 * Tests for the MakerworldPage URL-paste flow.
 *
 * Covers: status-driven warning banner, resolve round-trip populates design +
 * plate list, "Already imported" badge appears when the backend reports prior
 * imports, button labels (Save / Save & Slice in <slicer>), URL-change detection,
 * inline imported-plate action buttons, the Recent imports sidebar, and
 * DOMPurify sanitising of user-authored summary HTML.
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
      {
        id: 1452154,
        profileId: 298919107,
        title: '9 cells',
        cover: '',
        materialCnt: 1,
        needAms: false,
        downloadCount: 500,
      },
      {
        id: 1452158,
        profileId: 298919564,
        title: '12 cells',
        cover: '',
        materialCnt: 2,
        needAms: true,
        downloadCount: 120,
      },
    ],
    already_imported_library_ids: [],
    ...overrides,
  };
}

// Helper: seed all the handlers a "signed-in, happy-path" render needs.
// Individual tests layer extra handlers on top via ``server.use``.
function useAuthedHandlers(opts: {
  slicer?: 'bambu_studio' | 'orcaslicer';
  recent?: Array<Record<string, unknown>>;
} = {}) {
  const slicer = opts.slicer ?? 'bambu_studio';
  const recent = opts.recent ?? [];
  server.use(
    http.get('*/makerworld/status', () => HttpResponse.json(statusWithToken)),
    http.get('*/makerworld/recent-imports', () => HttpResponse.json(recent)),
    http.get('*/library/folders', () => HttpResponse.json([])),
    http.get('*/settings/', () =>
      HttpResponse.json({
        auto_archive: true,
        save_thumbnails: true,
        preferred_slicer: slicer,
      }),
    ),
  );
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
    useAuthedHandlers();
    render(<MakerworldPage />);
    // Page header is always visible; wait for status to settle
    await screen.findByRole('heading', { name: 'MakerWorld' });
    await waitFor(() => {
      expect(screen.queryByText(/Bambu Cloud sign-in required/i)).not.toBeInTheDocument();
    });
  });

  it('renders design + plate list after the user pastes a URL', async () => {
    useAuthedHandlers();
    server.use(
      http.post('*/makerworld/resolve', async ({ request }) => {
        const body = (await request.json()) as { url: string };
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
    useAuthedHandlers();
    server.use(
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

  it('labels the per-plate import button "Save" (not "Import")', async () => {
    useAuthedHandlers();
    server.use(
      http.post('*/makerworld/resolve', () => HttpResponse.json(resolveResponse())),
    );
    render(<MakerworldPage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    await screen.findByText('Seed Starter');
    // Two plates → two Save buttons.
    const saveButtons = await screen.findAllByRole('button', { name: /^Save$/ });
    expect(saveButtons.length).toBe(2);
  });

  it('interpolates the slicer name into the slice button (Bambu Studio by default)', async () => {
    useAuthedHandlers({ slicer: 'bambu_studio' });
    server.use(
      http.post('*/makerworld/resolve', () => HttpResponse.json(resolveResponse())),
    );
    render(<MakerworldPage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    const sliceButtons = await screen.findAllByRole('button', {
      name: /Save & Slice in Bambu Studio/,
    });
    expect(sliceButtons.length).toBe(2);
  });

  it('interpolates OrcaSlicer when that is the configured preferred slicer', async () => {
    useAuthedHandlers({ slicer: 'orcaslicer' });
    server.use(
      http.post('*/makerworld/resolve', () => HttpResponse.json(resolveResponse())),
    );
    render(<MakerworldPage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    const sliceButtons = await screen.findAllByRole('button', {
      name: /Save & Slice in OrcaSlicer/,
    });
    expect(sliceButtons.length).toBe(2);
  });

  it('clears the resolved preview when the URL input is edited after resolve', async () => {
    useAuthedHandlers();
    server.use(
      http.post('*/makerworld/resolve', () => HttpResponse.json(resolveResponse())),
    );
    render(<MakerworldPage />);

    const input = await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i);
    await userEvent.type(input, 'https://makerworld.com/en/models/1400373');
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    expect(await screen.findByText('Seed Starter')).toBeInTheDocument();

    // User retypes — preview must go away so they can't accidentally submit the
    // previous model.
    await userEvent.clear(input);
    await userEvent.type(input, 'https://makerworld.com/en/models/9999999');

    await waitFor(() => {
      expect(screen.queryByText('Seed Starter')).not.toBeInTheDocument();
    });
  });

  it('renders inline imported-plate actions after a successful import', async () => {
    useAuthedHandlers();
    server.use(
      http.post('*/makerworld/resolve', () => HttpResponse.json(resolveResponse())),
      http.post('*/makerworld/import', () =>
        HttpResponse.json({
          library_file_id: 99,
          filename: 'benchy.3mf',
          folder_id: 7,
          profile_id: 298919107,
          was_existing: false,
        }),
      ),
    );
    render(<MakerworldPage />);

    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    const saveButtons = await screen.findAllByRole('button', { name: /^Save$/ });
    await userEvent.click(saveButtons[0]);

    // Inline post-import row for that plate shows the "View in File Manager"
    // and the two slicer open buttons.
    await screen.findByRole('button', { name: /View in File Manager/i });
    expect(screen.getByRole('button', { name: /Open in Bambu Studio/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open in OrcaSlicer/i })).toBeInTheDocument();
  });

  it('renders the Recent imports sidebar when the query returns items', async () => {
    useAuthedHandlers({
      recent: [
        {
          library_file_id: 11,
          filename: 'first-import.3mf',
          folder_id: 2,
          thumbnail_path: null,
          source_url: 'https://makerworld.com/models/1#profileId-2',
          created_at: '2025-01-01T12:00:00',
        },
        {
          library_file_id: 12,
          filename: 'second-import.3mf',
          folder_id: 2,
          thumbnail_path: null,
          source_url: null,
          created_at: '2025-01-02T12:00:00',
        },
      ],
    });
    render(<MakerworldPage />);

    await screen.findByText(/Recent imports/i);
    expect(await screen.findByText('first-import.3mf')).toBeInTheDocument();
    expect(screen.getByText('second-import.3mf')).toBeInTheDocument();
  });

  it('omits the Recent imports sidebar when the feed is empty', async () => {
    // Defensive — the CardHeader uses the same translation as a potential
    // inline header; only the sidebar should surface "Recent imports".
    useAuthedHandlers({ recent: [] });
    render(<MakerworldPage />);

    await screen.findByRole('heading', { name: 'MakerWorld' });
    await waitFor(() => {
      expect(screen.queryByText(/Recent imports/i)).not.toBeInTheDocument();
    });
  });

  it('sanitises the design summary HTML via DOMPurify', async () => {
    // User-authored summaries are not trusted; a ``<script>`` tag must be
    // stripped before it lands in the DOM via dangerouslySetInnerHTML.
    useAuthedHandlers();
    server.use(
      http.post('*/makerworld/resolve', () =>
        HttpResponse.json(
          resolveResponse({
            design: {
              ...resolveResponse().design,
              summary:
                '<p>Clean prose <b>here</b></p><script>window.__pwned = true;</script>',
            },
          }),
        ),
      ),
    );
    render(<MakerworldPage />);
    await userEvent.type(
      await screen.findByPlaceholderText(/https:\/\/makerworld\.com/i),
      'https://makerworld.com/en/models/1400373',
    );
    await userEvent.click(screen.getByRole('button', { name: /Resolve/i }));

    await screen.findByText('Seed Starter');
    // Safe content survives.
    expect(screen.getByText(/Clean prose/i)).toBeInTheDocument();
    // Hostile content is stripped — DOMPurify drops <script> entirely, so the
    // side-effect assignment can't have run.
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
    // And no literal ``<script>`` text leaks into the document.
    expect(document.body.innerHTML).not.toContain('window.__pwned');
  });
});
