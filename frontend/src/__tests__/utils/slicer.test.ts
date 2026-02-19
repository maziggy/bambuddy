/**
 * Tests for the slicer utility functions.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { openInSlicer, detectPlatform, buildDownloadUrl } from '../../utils/slicer';

describe('slicer utility', () => {
  let clickSpy: ReturnType<typeof vi.fn>;
  let appendSpy: ReturnType<typeof vi.fn>;
  let removeSpy: ReturnType<typeof vi.fn>;
  let createdLink: HTMLAnchorElement;

  beforeEach(() => {
    clickSpy = vi.fn();
    appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation((node) => {
      createdLink = node as HTMLAnchorElement;
      return node;
    });
    removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);

    // Mock click on created elements
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(clickSpy);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('detectPlatform', () => {
    it('detects Windows', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)');
      expect(detectPlatform()).toBe('windows');
    });

    it('detects macOS', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)');
      expect(detectPlatform()).toBe('macos');
    });

    it('detects Linux', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (X11; Linux x86_64)');
      expect(detectPlatform()).toBe('linux');
    });
  });

  describe('openInSlicer', () => {
    it('uses bambustudio:// protocol on Windows for bambu_studio', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0)');
      openInSlicer('http://localhost:8000/file.3mf', 'bambu_studio');

      expect(appendSpy).toHaveBeenCalled();
      expect(createdLink.href).toContain('bambustudio://open?file=');
      expect(createdLink.href).toContain('http://localhost:8000/file.3mf');
      expect(clickSpy).toHaveBeenCalled();
      expect(removeSpy).toHaveBeenCalled();
    });

    it('uses bambustudioopen:// protocol on macOS for bambu_studio', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Macintosh; Intel Mac OS X)');
      openInSlicer('http://localhost:8000/file.3mf', 'bambu_studio');

      expect(createdLink.href).toContain('bambustudioopen://');
    });

    it('uses bambustudio://open?file= on Linux for bambu_studio', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (X11; Linux x86_64)');
      openInSlicer('http://localhost:8000/file.3mf', 'bambu_studio');

      expect(createdLink.href).toContain('bambustudio://open?file=');
    });

    it('uses orcaslicer:// protocol for orcaslicer on all platforms', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Macintosh; Intel Mac OS X)');
      openInSlicer('http://localhost:8000/file.3mf', 'orcaslicer');

      expect(createdLink.href).toContain('orcaslicer://');
      expect(createdLink.href).toContain('open?file=');
    });

    it('does not encode the file URL for orcaslicer', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0)');
      const url = 'http://localhost:8000/api/v1/archives/1/file/My Model.3mf';
      openInSlicer(url, 'orcaslicer');

      // The href should contain the raw URL (browser may normalize it but it should not be double-encoded)
      expect(createdLink.href).toContain('orcaslicer://open?file=');
      // Should NOT contain %253A (double-encoded colon)
      expect(createdLink.href).not.toContain('%253A');
    });

    it('defaults to bambu_studio when no slicer specified', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0)');
      openInSlicer('http://localhost:8000/file.3mf');

      expect(createdLink.href).toContain('bambustudio://');
    });

    it('creates and removes a temporary link element', () => {
      vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0)');
      openInSlicer('http://localhost:8000/file.3mf', 'bambu_studio');

      expect(appendSpy).toHaveBeenCalledOnce();
      expect(clickSpy).toHaveBeenCalledOnce();
      expect(removeSpy).toHaveBeenCalledOnce();
    });
  });

  describe('buildDownloadUrl', () => {
    it('prepends window.location.origin', () => {
      const result = buildDownloadUrl('/api/v1/archives/1/file/test.3mf');
      expect(result).toBe(`${window.location.origin}/api/v1/archives/1/file/test.3mf`);
    });
  });
});
