/**
 * The no-3MF banner has to say the right thing (#2780).
 *
 * It used to have one wording for every cause: "Store sent files on external
 * storage" is off in the slicer, go and turn it on. On H2-series and P2S that
 * is wrong twice over -- the setting is already on, and turning it on again
 * changes nothing, because the printer keeps the sliced file on internal
 * storage that FTPS does not serve at all. #2780's reporter followed that
 * advice, and #1170's before them.
 *
 * A third cause joined them in #1820: a print started from the printer's own
 * screen, where no slicer was involved at all and both of the wordings above
 * describe a step the operator never took.
 *
 * So these assert the wording actually shown, not just that a banner rendered.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { server } from '../mocks/server';

/** Serve the endpoint under test; everything else can be empty. */
function mockWarning(body: { has_fallback: boolean; reason: string | null }) {
  server.use(
    http.get('/api/v1/archives/no-3mf-warning', () => HttpResponse.json(body)),
    http.get('/api/v1/archives/', () => HttpResponse.json([])),
    http.get('/api/v1/archives/stats', () => HttpResponse.json({})),
    http.get('/api/v1/archives/tags', () => HttpResponse.json([])),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/projects/', () => HttpResponse.json([])),
  );
}

describe('ArchivesPage no-3MF banner', () => {
  beforeEach(() => {
    // The banner is dismissed one-shot via localStorage, and a leftover flag
    // would make every assertion below pass vacuously.
    localStorage.clear();
  });

  it('keeps the slicer-setting wording when the cause is unknown', async () => {
    mockWarning({ has_fallback: true, reason: null });

    render(<ArchivesPage />);

    expect(
      await screen.findByText(/couldn't be archived with thumbnails/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Store sent files on external storage/i)).toBeInTheDocument();
    expect(screen.getByText('See install step 4')).toBeInTheDocument();
  });

  it('explains internal storage instead of blaming the setting', async () => {
    mockWarning({ has_fallback: true, reason: 'internal_storage' });

    render(<ArchivesPage />);

    expect(
      await screen.findByText(/stayed on the printer's internal storage/i),
    ).toBeInTheDocument();
    // The specific regression: this reason must NOT send the user to step 4.
    expect(screen.queryByText('See install step 4')).not.toBeInTheDocument();
    expect(screen.getByText('Why this happens')).toBeInTheDocument();
  });

  it('does not blame a slicer that was never involved', async () => {
    // #1820: a print started from the printer's own screen sends nothing, so
    // both the generic wording ("turn the setting on") and the internal-storage
    // wording ("use Send with External") describe a step that never happened.
    mockWarning({ has_fallback: true, reason: 'internal_history' });

    render(<ArchivesPage />);

    expect(
      await screen.findByText(/started from a file already on the printer/i),
    ).toBeInTheDocument();
    expect(screen.queryByText('See install step 4')).not.toBeInTheDocument();
    expect(screen.queryByText(/Store sent files on external storage/i)).not.toBeInTheDocument();
    expect(screen.getByText('Why this happens')).toBeInTheDocument();
  });

  it('names the empty slot, and offers no link because there is nothing to read', async () => {
    mockWarning({ has_fallback: true, reason: 'no_external_storage' });

    render(<ArchivesPage />);

    expect(await screen.findByText(/no storage in the printer/i)).toBeInTheDocument();
    expect(screen.queryByText('See install step 4')).not.toBeInTheDocument();
    expect(screen.queryByText('Why this happens')).not.toBeInTheDocument();
  });

  it('shows nothing at all when no print fell back', async () => {
    mockWarning({ has_fallback: false, reason: null });

    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.queryByText(/couldn't be archived/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/internal storage/i)).not.toBeInTheDocument();
  });

  it('renders a title for every reason rather than an untranslated key', async () => {
    // The variant suffix is built by string concatenation, so a typo in one
    // locale key surfaces as a raw "archives.no3mfBanner.titleX" on screen
    // instead of failing anything.
    for (const reason of [null, 'internal_storage', 'no_external_storage', 'internal_history']) {
      localStorage.clear();
      mockWarning({ has_fallback: true, reason });

      const { unmount } = render(<ArchivesPage />);

      // Wait for the banner itself, so a variant that rendered nothing at all
      // can't satisfy the "no raw key" assertion vacuously.
      await screen.findByRole('button', { name: 'Dismiss this notice' });
      expect(screen.queryByText(/archives\.no3mfBanner\./)).not.toBeInTheDocument();
      unmount();
    }
  });
});
