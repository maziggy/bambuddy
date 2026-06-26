/**
 * Tests for the API-key QR payload builder.
 */

import { describe, it, expect } from 'vitest';
import { buildApiKeyQrPayload, API_KEY_QR_VERSION } from '../../utils/apiKeyQr';

describe('buildApiKeyQrPayload', () => {
  it('uses the bambuddy://config scheme with v first', () => {
    const payload = buildApiKeyQrPayload('https://printer.local', 'bb_abc123');
    expect(payload.startsWith(`bambuddy://config?v=${API_KEY_QR_VERSION}`)).toBe(true);
  });

  it('encodes the url and key parameters', () => {
    const payload = buildApiKeyQrPayload('https://printer.local', 'bb_abc123');
    expect(payload).toBe('bambuddy://config?v=1&url=https%3A%2F%2Fprinter.local&key=bb_abc123');
  });

  it('URL-encodes special characters in both values', () => {
    const baseUrl = 'http://host:5173/sub path';
    const key = 'bb_a+b/c=d&e';
    const payload = buildApiKeyQrPayload(baseUrl, key);

    expect(payload).toContain(`url=${encodeURIComponent(baseUrl)}`);
    expect(payload).toContain(`key=${encodeURIComponent(key)}`);
    // The raw, unencoded key must never leak into the payload.
    expect(payload).not.toContain(key);
  });

  it('round-trips the values back out of the query string', () => {
    const baseUrl = 'https://my.bambuddy.example:8443';
    const key = 'bb_ZZ/99+aa==';
    const payload = buildApiKeyQrPayload(baseUrl, key);

    const query = payload.slice(payload.indexOf('?') + 1);
    const params = new URLSearchParams(query);
    expect(params.get('v')).toBe(String(API_KEY_QR_VERSION));
    expect(params.get('url')).toBe(baseUrl);
    expect(params.get('key')).toBe(key);
  });
});
