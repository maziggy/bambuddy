/**
 * Unit tests for rewriteMediaSrcWithToken — the DOM walker that retrofits a
 * query token onto <img>/<video> src URLs that rendered before the token
 * arrived (regression guard for the post-login blank-thumbnails bug).
 *
 * Since #3025 it carries two tokens and picks per URL: live-camera URLs take
 * the camera stream token, everything else takes the media token.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { isCameraUrl, rewriteMediaSrcWithToken } from '../../hooks/useCameraStreamToken';

describe('rewriteMediaSrcWithToken', () => {
  let root: HTMLDivElement;

  beforeEach(() => {
    root = document.createElement('div');
    document.body.appendChild(root);
  });

  afterEach(() => {
    root.remove();
  });

  const addImg = (src: string) => {
    const img = document.createElement('img');
    img.setAttribute('src', src);
    root.appendChild(img);
    return img;
  };

  const addVideo = (src: string) => {
    const v = document.createElement('video');
    v.setAttribute('src', src);
    root.appendChild(v);
    return v;
  };

  it('appends token to /api/v1/ images that have no query string', () => {
    const img = addImg('/api/v1/library/files/42/thumbnail');
    const count = rewriteMediaSrcWithToken(root, 'abc123', null);
    expect(count).toBe(1);
    expect(img.getAttribute('src')).toBe('/api/v1/library/files/42/thumbnail?token=abc123');
  });

  it('appends token to URLs that already have a query string using & separator', () => {
    const img = addImg('/api/v1/archives/5/thumbnail?v=1700000000000');
    rewriteMediaSrcWithToken(root, 'abc123', null);
    expect(img.getAttribute('src')).toBe('/api/v1/archives/5/thumbnail?v=1700000000000&token=abc123');
  });

  it('leaves images alone that already carry the current token', () => {
    const img = addImg('/api/v1/library/files/42/thumbnail?token=abc123');
    const count = rewriteMediaSrcWithToken(root, 'abc123', null);
    expect(count).toBe(0);
    expect(img.getAttribute('src')).toBe('/api/v1/library/files/42/thumbnail?token=abc123');
  });

  it('replaces a stale token with the current one', () => {
    const img = addImg('/api/v1/library/files/42/thumbnail?token=OLD');
    rewriteMediaSrcWithToken(root, 'NEW', null);
    expect(img.getAttribute('src')).toBe('/api/v1/library/files/42/thumbnail?token=NEW');
  });

  it('replaces a stale token that sits in the middle of the query string', () => {
    const img = addImg('/api/v1/archives/5/thumbnail?token=OLD&v=1700000000000');
    rewriteMediaSrcWithToken(root, 'NEW', null);
    // Old token stripped, v preserved, new token appended.
    expect(img.getAttribute('src')).toBe('/api/v1/archives/5/thumbnail?v=1700000000000&token=NEW');
  });

  it('ignores images that do not point at /api/v1/', () => {
    const img = addImg('https://cdn.example.com/static/logo.png');
    rewriteMediaSrcWithToken(root, 'abc123', null);
    expect(img.getAttribute('src')).toBe('https://cdn.example.com/static/logo.png');
  });

  it('updates <video> elements as well', () => {
    const v = addVideo('/api/v1/printers/7/camera/stream?fps=10');
    rewriteMediaSrcWithToken(root, null, 'abc123');
    expect(v.getAttribute('src')).toBe('/api/v1/printers/7/camera/stream?fps=10&token=abc123');
  });

  it('url-encodes tokens containing special characters', () => {
    const img = addImg('/api/v1/library/files/42/thumbnail');
    rewriteMediaSrcWithToken(root, 'a b/c=d', null);
    expect(img.getAttribute('src')).toBe('/api/v1/library/files/42/thumbnail?token=a%20b%2Fc%3Dd');
  });
});

// #3025 — the two tokens are not interchangeable. A user without camera:view
// holds a media token and no camera token; sending the media token to a camera
// route (or the camera token to a thumbnail) would 401 either way.
describe('rewriteMediaSrcWithToken picks the token per URL (#3025)', () => {
  let root: HTMLDivElement;

  beforeEach(() => {
    root = document.createElement('div');
    document.body.appendChild(root);
  });

  afterEach(() => {
    root.remove();
  });

  const addImg = (src: string) => {
    const img = document.createElement('img');
    img.setAttribute('src', src);
    root.appendChild(img);
    return img;
  };

  it('gives a thumbnail the media token, not the camera token', () => {
    const img = addImg('/api/v1/library/files/42/thumbnail');
    rewriteMediaSrcWithToken(root, 'media-tok', 'camera-tok');
    expect(img.getAttribute('src')).toBe('/api/v1/library/files/42/thumbnail?token=media-tok');
  });

  it('gives a live camera stream the camera token, not the media token', () => {
    const img = addImg('/api/v1/printers/7/camera/stream?fps=10');
    rewriteMediaSrcWithToken(root, 'media-tok', 'camera-tok');
    expect(img.getAttribute('src')).toBe('/api/v1/printers/7/camera/stream?fps=10&token=camera-tok');
  });

  it('still rewrites thumbnails for a user who has no camera token at all', () => {
    const thumb = addImg('/api/v1/archives/5/thumbnail');
    const stream = addImg('/api/v1/printers/7/camera/stream?fps=10');
    const count = rewriteMediaSrcWithToken(root, 'media-tok', null);
    expect(count).toBe(1);
    expect(thumb.getAttribute('src')).toBe('/api/v1/archives/5/thumbnail?token=media-tok');
    // Left untouched rather than given a token that would not work on it.
    expect(stream.getAttribute('src')).toBe('/api/v1/printers/7/camera/stream?fps=10');
  });

  it('classifies the three camera routes as camera and the media routes as media', () => {
    expect(isCameraUrl('/api/v1/printers/1/camera/stream?fps=10')).toBe(true);
    expect(isCameraUrl('/api/v1/printers/1/camera/snapshot')).toBe(true);
    expect(isCameraUrl('/api/v1/printers/1/camera/plate-detection/references/0/thumbnail')).toBe(true);
    expect(isCameraUrl('/api/v1/library/files/42/thumbnail')).toBe(false);
    expect(isCameraUrl('/api/v1/archives/5/timelapse')).toBe(false);
    expect(isCameraUrl('/api/v1/printers/1/cover')).toBe(false);
    expect(isCameraUrl('/api/v1/external-links/3/icon')).toBe(false);
  });
});
