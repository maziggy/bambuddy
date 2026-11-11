/**
 * Where a spool's product can be bought again (#2540).
 */
import { describe, it, expect } from 'vitest';
import type { InventorySpool, SpoolSupplierLink } from '../../api/client';
import { isWebUrl, normalizeWebUrl, productBuyOptions, productUrlValid, spoolBuyOptions } from '../../utils/productLinks';

const link = (supplier_id: number, over: Partial<SpoolSupplierLink> = {}): SpoolSupplierLink => ({
  id: supplier_id,
  supplier_id,
  supplier_name: `Shop ${supplier_id}`,
  supplier_article_number: null,
  quoted_price_per_kg: null,
  is_purchase_source: false,
  product_url: `https://shop${supplier_id}.example/p`,
  ...over,
});

const spool = (id: number, created_at: string, suppliers: SpoolSupplierLink[]) =>
  ({ id, created_at, suppliers }) as unknown as InventorySpool;

describe('isWebUrl', () => {
  it.each(['https://shop.example/p?x=1', 'http://shop.example', ' https://shop.example/p '])('takes %s', (url) => {
    expect(isWebUrl(url)).toBe(true);
  });

  it.each(['javascript:alert(1)', 'data:text/html,x', 'ftp://shop.example', 'shop.example/p', '', null, undefined, 'http:example.com', 'https:/example.com'])(
    'refuses %s',
    (url) => {
      expect(isWebUrl(url)).toBe(false);
    },
  );

  it('accepts an empty product page as typed, but not a bad one', () => {
    expect(productUrlValid('  ')).toBe(true);
    expect(productUrlValid('javascript:x')).toBe(false);
  });
});

// The same inputs as the backend's TestAddresses (test_supplier_product_url_2540.py):
// both sides apply one rule.
describe('normalizeWebUrl', () => {
  it.each([
    ['https://shop.example/p?x=1', 'https://shop.example/p?x=1'],
    ['  http://shop.example/x  ', 'http://shop.example/x'],
    ['HTTPS://Shop.example/x', 'HTTPS://Shop.example/x'],
    ['', ''],
    ['   ', ''],
    ['3djake.de', 'https://3djake.de'],
    ['  shop.example/x?a=1 ', 'https://shop.example/x?a=1'],
    ['localhost:8080/x', 'https://localhost:8080/x'],
    ['shop.example:8080', 'https://shop.example:8080'],
  ])('takes %j as %j', (input, expected) => {
    expect(normalizeWebUrl(input)).toBe(expected);
  });

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'ftp://shop.example/x',
    'http://',
    '//shop.example/x',
    'mailto:a@b.c',
    // The browser's URL() fixes these up to a host; the backend finds none.
    'http:example.com',
    'https:/example.com',
    String.raw`https:\example.com`,
    String.raw`https://\example.com`,
    'https:///example.com',
    String.raw`https://example.com\path`,
    'https://:8080/x',
    'https://user@/x',
    'https://exa mple.com',
  ])('refuses %j', (input) => {
    expect(normalizeWebUrl(input)).toBeNull();
    expect(productUrlValid(input)).toBe(false);
  });

  it('lets an address without a scheme through the pre-check', () => {
    expect(productUrlValid('3djake.de')).toBe(true);
  });
});

describe('spoolBuyOptions', () => {
  it('lists only pages that are web addresses, where it was bought first', () => {
    const options = spoolBuyOptions({
      suppliers: [
        link(1),
        link(2, { product_url: null }),
        link(3, { product_url: 'javascript:alert(1)' }),
        link(4, { is_purchase_source: true }),
      ],
    });

    expect(options.map((o) => o.supplier_id)).toEqual([4, 1]);
  });
});

describe('productBuyOptions', () => {
  it('takes one page per supplier, from its newest spool', () => {
    const options = productBuyOptions([
      spool(1, '2026-01-01T00:00:00Z', [link(1, { product_url: 'https://shop1.example/old' })]),
      spool(2, '2026-03-01T00:00:00Z', [link(1, { product_url: 'https://shop1.example/new' })]),
    ]);

    expect(options).toHaveLength(1);
    expect(options[0].product_url).toBe('https://shop1.example/new');
  });

  it('ranks a supplier a spool was bought from first, even if a newer spool did not say so', () => {
    const options = productBuyOptions([
      spool(1, '2026-01-01T00:00:00Z', [link(2, { is_purchase_source: true })]),
      spool(2, '2026-03-01T00:00:00Z', [link(1), link(2)]),
    ]);

    expect(options.map((o) => [o.supplier_id, o.is_purchase_source])).toEqual([
      [2, true],
      [1, false],
    ]);
  });

  it('has nothing for a product without pages', () => {
    expect(productBuyOptions([spool(1, '2026-01-01T00:00:00Z', [link(1, { product_url: null })])])).toEqual([]);
  });
});
