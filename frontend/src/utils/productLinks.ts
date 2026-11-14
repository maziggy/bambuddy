// Where a spool's product can be bought again (#2540): each supplier
// assignment can carry the product's own page at that supplier
// (`product_url`). The supplier's `website` is only its storefront.
import type { InventorySpool, SpoolSupplierLink } from '../api/client';

/** A place to buy a product: one supplier's page for it. */
export interface BuyOption {
  supplier_id: number;
  supplier_name: string;
  product_url: string;
  supplier_article_number: string | null;
  quoted_price_per_kg: number | null;
  /** Where a spool of it was actually bought, rather than an alternative. */
  is_purchase_source: boolean;
}

// One rule for every address field, mirrored by validate_http_url in the
// backend's schemas/supplier.py (keep them in step; both are tested on the same
// inputs). Browsers fix up "https:/x" or "https:\x" into a host that the backend's
// urlsplit does not find, so the rule is checked on the text, not on URL() alone.
const HTTP_PREFIX = /^https?:\/\//i;
// Another scheme, or a malformed http one ("http:example.com"). A colon followed
// by a port ("example.com:8080/x") is a host, not a scheme.
const OTHER_SCHEME = /^[a-z][a-z0-9+.-]*:(?!\d+(?:[/?#]|$))/i;
// A host right after "//", ended by a path, query, fragment or the end. A
// backslash ends nothing.
const HTTP_AUTHORITY = /^https?:\/\/[^\s/?#\\]+(?:[/?#]|$)/i;

/** An address as the backend will store it: trimmed, `https://` added to one
 *  without a scheme ("3djake.de"), '' for an empty one; null when it is not an
 *  http(s) address at all. */
export function normalizeWebUrl(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim();
  if (!trimmed) return '';
  let candidate = trimmed;
  if (!HTTP_PREFIX.test(candidate)) {
    if (OTHER_SCHEME.test(candidate)) return null;
    candidate = `https://${candidate}`;
  }
  if (!HTTP_AUTHORITY.test(candidate)) return null;
  try {
    return new URL(candidate).hostname ? candidate : null;
  } catch {
    return null;
  }
}

/** Whether `value` is an http(s) address with its scheme, the only kind
 *  rendered as a link: anything else (`javascript:`, `data:`) would run in the
 *  page on click. The backend refuses others on write; this also covers rows
 *  saved before. */
export function isWebUrl(value: string | null | undefined): value is string {
  const trimmed = (value ?? '').trim();
  return !!trimmed && HTTP_PREFIX.test(trimmed) && normalizeWebUrl(trimmed) === trimmed;
}

function toOption(link: SpoolSupplierLink): BuyOption | null {
  if (!isWebUrl(link.product_url)) return null;
  return {
    supplier_id: link.supplier_id,
    supplier_name: link.supplier_name,
    product_url: link.product_url.trim(),
    supplier_article_number: link.supplier_article_number,
    quoted_price_per_kg: link.quoted_price_per_kg,
    is_purchase_source: link.is_purchase_source,
  };
}

/** Whether an address as typed is one the backend accepts: empty, or http(s)
 *  with or without its scheme. */
export function productUrlValid(value: string): boolean {
  return normalizeWebUrl(value) !== null;
}

/** A spool's places to buy its product again: the supplier it was bought from
 *  first, then the alternatives in their saved order. */
export function spoolBuyOptions(spool: Pick<InventorySpool, 'suppliers'>): BuyOption[] {
  const options = (spool.suppliers ?? []).map(toOption).filter((o): o is BuyOption => o !== null);
  return options.sort((a, b) => Number(b.is_purchase_source) - Number(a.is_purchase_source));
}

/** A product's places to buy it, from all its spools (archived ones too: a
 *  product used up is still bought where it was). One per supplier, from its
 *  newest spool that has a page there; suppliers a spool was bought from come
 *  first, newest first. */
export function productBuyOptions(spools: InventorySpool[]): BuyOption[] {
  const newestFirst = [...spools].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '') || b.id - a.id);
  const bySupplier = new Map<number, BuyOption>();
  for (const spool of newestFirst) {
    for (const option of spoolBuyOptions(spool)) {
      const known = bySupplier.get(option.supplier_id);
      if (!known) {
        bySupplier.set(option.supplier_id, option);
      } else if (option.is_purchase_source && !known.is_purchase_source) {
        // Bought there once is what ranks it, whatever the newest spool says.
        bySupplier.set(option.supplier_id, { ...known, is_purchase_source: true });
      }
    }
  }
  const options = [...bySupplier.values()];
  // Stable: within each half the newest-spool order stays.
  return options.sort((a, b) => Number(b.is_purchase_source) - Number(a.is_purchase_source));
}
