/**
 * The full-page G-code preview.
 *
 * This used to be an iframe onto a vendored copy of PrettyGCode, and most of
 * the page was machinery for detecting when a proxy refused the embed. It now
 * renders Bambuddy's own toolpath viewer directly, so what is worth testing is
 * that the right file reaches it -- including the plate, which a multi-plate
 * archive needs or the viewer silently shows a different plate than the one
 * that was picked.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { GCodeViewerPage } from '../../pages/GCodeViewerPage';

// The viewer itself needs WebGL, which jsdom has no answer for. Its own
// behaviour is covered by the parser and renderer tests; here we only care
// which URL the page hands it.
vi.mock('../../components/GcodeToolpathViewer', () => ({
  GcodeToolpathViewer: ({ gcodeUrl, filamentColors }: { gcodeUrl: string; filamentColors?: string[] }) => (
    <div data-testid="toolpath-viewer" data-url={gcodeUrl} data-colors={(filamentColors ?? []).join(',')} />
  ),
}));

function visit(search: string) {
  window.history.pushState({}, '', `/gcode-viewer${search}`);
  render(<GCodeViewerPage />);
}

const viewerUrl = () => screen.getByTestId('toolpath-viewer').getAttribute('data-url');

const plate = (index: number, color: string) => ({
  index,
  name: null,
  objects: [],
  has_thumbnail: false,
  thumbnail_url: null,
  print_time_seconds: null,
  filament_used_grams: null,
  filaments: [{ slot_id: 1, type: 'PLA', color, used_grams: 1, used_meters: 1 }],
});

const TWO_PLATES = {
  file_id: 7,
  filename: 'two.gcode.3mf',
  is_multi_plate: true,
  plates: [plate(1, '#ff0000'), plate(2, '#00ff00')],
};

describe('GCodeViewerPage', () => {
  const originalUrl = window.location.href;

  beforeEach(() => {
    window.history.pushState({}, '', '/gcode-viewer');
  });

  afterEach(() => {
    window.history.pushState({}, '', originalUrl);
  });

  it('previews an archive', () => {
    visit('?archive=82');
    expect(viewerUrl()).toContain('/archives/82/gcode');
  });

  it('previews a library file', () => {
    visit('?library_file=7');
    expect(viewerUrl()).toContain('/library/files/7/gcode');
  });

  it('carries the plate through for a multi-plate source', () => {
    // Dropping this shows whichever plate the backend defaults to rather than
    // the one the user picked, with nothing on screen to say so.
    visit('?archive=82&plate=3');
    expect(viewerUrl()).toContain('plate=3');
  });

  it('omits the plate parameter when there is no plate', () => {
    visit('?archive=82');
    expect(viewerUrl()).not.toContain('plate=');
  });

  it('says so when no file was given rather than rendering an empty viewer', () => {
    visit('');
    expect(screen.queryByTestId('toolpath-viewer')).not.toBeInTheDocument();
    expect(screen.getByText(/No file was given/i)).toBeInTheDocument();
  });

  it('offers a way back to where the file came from', () => {
    visit('?archive=82');
    expect(screen.getByRole('button', { name: /Back to Print Archives/i })).toBeInTheDocument();
  });

  it('names the file manager when the source was a library file', () => {
    visit('?library_file=7');
    expect(screen.getByRole('button', { name: /Back to File Manager/i })).toBeInTheDocument();
  });
});

describe('GCodeViewerPage — plate switcher', () => {
  const originalUrl = window.location.href;
  afterEach(() => window.history.pushState({}, '', originalUrl));

  it('offers every plate of a multi-plate file and shows the one being previewed', async () => {
    // Nothing that opens this page from the File Manager passes a plate, so
    // without a switcher the other plates of a sliced multi-plate 3MF were
    // simply unreachable.
    server.use(http.get('/api/v1/library/files/:id/plates', () => HttpResponse.json(TWO_PLATES)));

    window.history.pushState({}, '', '/gcode-viewer?library_file=7');
    render(<GCodeViewerPage />);

    const first = await screen.findByRole('button', { name: /Plate 1/ });
    const second = screen.getByRole('button', { name: /Plate 2/ });
    // No plate in the URL means the backend serves the lowest-numbered one,
    // which is what the switcher has to agree with.
    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(second).toHaveAttribute('aria-pressed', 'false');
  });

  it('asks for the plate that was picked', async () => {
    server.use(http.get('/api/v1/library/files/:id/plates', () => HttpResponse.json(TWO_PLATES)));

    const user = userEvent.setup();
    window.history.pushState({}, '', '/gcode-viewer?library_file=7');
    render(<GCodeViewerPage />);

    await user.click(await screen.findByRole('button', { name: /Plate 2/ }));

    await waitFor(() => expect(viewerUrl()).toContain('plate=2'));
    // The choice lives in the URL, so the view survives a reload or a share.
    expect(window.location.search).toContain('plate=2');
  });

  it('stays out of the way for a single-plate file', async () => {
    server.use(
      http.get('/api/v1/library/files/:id/plates', () =>
        HttpResponse.json({ ...TWO_PLATES, is_multi_plate: false, plates: [plate(1, '#ff0000')] }),
      ),
    );

    window.history.pushState({}, '', '/gcode-viewer?library_file=7');
    render(<GCodeViewerPage />);

    await waitFor(() => expect(screen.getByTestId('toolpath-viewer')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Plate 1/ })).not.toBeInTheDocument();
  });
});

describe('GCodeViewerPage — filament colours', () => {
  const originalUrl = window.location.href;
  afterEach(() => window.history.pushState({}, '', originalUrl));

  it('indexes library-file colours by tool number, not slot number', async () => {
    // slot_id is 1-based and the G-code's T numbers are 0-based; indexing
    // straight by slot puts every colour one filament out, so a two-material
    // print comes out with the wrong body colour.
    server.use(
      http.get('/api/v1/library/files/:id/plates', () =>
        HttpResponse.json({
          file_id: 7,
          filename: 'duck.gcode.3mf',
          is_multi_plate: false,
          plates: [
            {
              index: 1,
              name: null,
              objects: [],
              has_thumbnail: false,
              thumbnail_url: null,
              print_time_seconds: null,
              filament_used_grams: null,
              filaments: [
                { slot_id: 1, type: 'PLA', color: '#ffffff', used_grams: 1, used_meters: 1 },
                { slot_id: 2, type: 'PLA', color: '#000000', used_grams: 1, used_meters: 1 },
              ],
            },
          ],
        }),
      ),
    );

    window.history.pushState({}, '', '/gcode-viewer?library_file=7');
    render(<GCodeViewerPage />);

    await waitFor(() =>
      expect(screen.getByTestId('toolpath-viewer')).toHaveAttribute('data-colors', '#ffffff,#000000'),
    );
  });

  it('colours from the plate being previewed, not the first one', async () => {
    // Plate 2's toolpath rendered in plate 1's colours is the same wrong-plate
    // mistake one layer up: both have to follow the `plate` parameter.
    server.use(http.get('/api/v1/library/files/:id/plates', () => HttpResponse.json(TWO_PLATES)));

    window.history.pushState({}, '', '/gcode-viewer?library_file=7&plate=2');
    render(<GCodeViewerPage />);

    await waitFor(() => expect(screen.getByTestId('toolpath-viewer')).toHaveAttribute('data-colors', '#00ff00'));
  });

  it('previews without colours when the plate metadata carries none', async () => {
    server.use(
      http.get('/api/v1/library/files/:id/plates', () =>
        HttpResponse.json({ file_id: 7, filename: 'x', is_multi_plate: false, plates: [] }),
      ),
    );

    window.history.pushState({}, '', '/gcode-viewer?library_file=7');
    render(<GCodeViewerPage />);

    // The preview must still render; colours are a bonus, not a prerequisite.
    expect(screen.getByTestId('toolpath-viewer')).toBeInTheDocument();
  });
});
