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

/** Whether `value` is an http(s) address, the only kind rendered as a link:
 *  anything else (`javascript:`, `data:`) would run in the page on click. The
 *  backend refuses others on write; this also covers rows saved before. */
export function isWebUrl(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    const url = new URL(value.trim());
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.host !== '';
  } catch {
    return false;
  }
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

/** Whether a product page as typed is one the backend accepts: empty, or http(s). */
export function productUrlValid(value: string): boolean {
  return !value.trim() || isWebUrl(value);
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
