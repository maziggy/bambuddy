/**
 * The timelapse player fits the screen whatever the video's shape (#3281).
 *
 * The video was sized by the width of the dialog alone, and the dialog had no
 * height limit. A portrait timelapse -- a 16:9 camera turned to 9:16 with the
 * External Camera rotation -- came out taller than a landscape screen, and
 * pushed the play, seek and speed controls below the bottom edge where they
 * could not be reached.
 *
 * jsdom does no layout, so these pin the limits that make it fit: the dialog
 * is never taller than the screen, and the video leaves room for the header
 * and the controls under it.
 */

import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import i18n from '../../i18n';
import { TimelapseViewer } from '../../components/TimelapseViewer';

function renderViewer() {
  return render(
    <TimelapseViewer src="/timelapse.mp4" title="Bracket - Timelapse" downloadFilename="bracket.mp4" archiveId={7} onClose={vi.fn()} />,
  );
}

describe('TimelapseViewer', () => {
  beforeAll(() => {
    // jsdom has no media playback; autoPlay must not throw.
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  });

  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('keeps the dialog within the height of the screen', () => {
    renderViewer();
    const dialog = screen.getByTestId('timelapse-viewer');
    expect(dialog.className).toMatch(/max-h-\[calc\(100dvh-2rem\)\]/);
    expect(dialog.className).toContain('overflow-y-auto');
  });

  it('limits the video height and fits it inside that box', () => {
    const { container } = renderViewer();
    const video = container.querySelector('video');
    expect(video).not.toBeNull();
    expect(video!.className).toMatch(/max-h-\[calc\(100dvh-[0-9.]+rem\)\]/);
    expect(video!.className).toContain('object-contain');
  });

  it('labels its buttons in the language of the page', async () => {
    await i18n.loadLanguages('de');
    await i18n.changeLanguage('de');
    renderViewer();

    expect(screen.getByText(i18n.t('common.edit'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('common.download'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('timelapseViewer.speed'))).toBeInTheDocument();
    expect(screen.getByTitle(i18n.t('timelapseViewer.skipBack5s'))).toBeInTheDocument();
    expect(screen.getByTitle(i18n.t('timelapseViewer.skipForward5s'))).toBeInTheDocument();
    // German really was loaded, so the checks above did not pass on English.
    expect(i18n.t('common.download')).not.toBe('Download');
  });
});
