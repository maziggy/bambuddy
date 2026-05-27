/**
 * Tests for the clipboard / file-download helpers.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { copyTextToClipboard, downloadTextFile } from '../../utils/clipboard';

describe('copyTextToClipboard', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses navigator.clipboard in a secure context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);

    const ok = await copyTextToClipboard('hello');

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
    vi.unstubAllGlobals();
  });

  it('falls back to execCommand when clipboard write rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('blocked'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);
    const execCommand = vi.fn().mockReturnValue(true);
    // jsdom does not implement execCommand — supply it for the fallback path.
    (document as unknown as { execCommand: unknown }).execCommand = execCommand;

    const ok = await copyTextToClipboard('lan-fallback');

    expect(ok).toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
    vi.unstubAllGlobals();
  });
});

describe('downloadTextFile', () => {
  it('triggers a download with the given filename', () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:fake');
    const revokeObjectURL = vi.fn();
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;

    let clickedHref = '';
    let clickedDownload = '';
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clickedHref = this.href;
        clickedDownload = this.download;
      });

    downloadTextFile('cert-body', 'bambuddy-virtual-printer-ca.crt', 'application/x-pem-file');

    expect(clickSpy).toHaveBeenCalled();
    expect(clickedHref).toContain('blob:fake');
    expect(clickedDownload).toBe('bambuddy-virtual-printer-ca.crt');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake');

    clickSpy.mockRestore();
  });
});
