import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ShoppingBag } from 'lucide-react';
import type { BuyOption } from '../utils/productLinks';

// "Buy" for a spool or a product (#2540): opens the product's page at its
// supplier. One place: a link. Several: a menu of them, the supplier it was
// bought from first. Nothing to open: nothing rendered. Clicks stay here, so
// the row or card around it does not open too. The menu is portalled and
// fixed-positioned: the tables it sits in scroll sideways and would cut it off.
const MENU_WIDTH = 180;
const EDGE = 8;

export function BuyButton({
  options,
  currencySymbol,
  className = 'p-1.5 rounded text-bambu-gray hover:text-bambu-green hover:bg-bambu-dark-tertiary transition-colors',
  iconClassName = 'w-4 h-4',
}: {
  options: BuyOption[];
  currencySymbol?: string;
  className?: string;
  iconClassName?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top?: number; bottom?: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    // Right edge under the button's, kept inside the window; above it when
    // there is more room there than below.
    const left = Math.max(EDGE, Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - EDGE));
    const above = window.innerHeight - rect.bottom < 200 && rect.top > window.innerHeight - rect.bottom;
    setPos(above ? { bottom: window.innerHeight - rect.top + 4, left } : { top: rect.bottom + 4, left });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener('mousedown', onDocClick);
    // The menu does not follow its button when something scrolls or resizes.
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  if (options.length === 0) return null;

  if (options.length === 1) {
    const [only] = options;
    return (
      <a
        href={only.product_url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        className={`inline-flex items-center ${className}`}
        title={t('inventory.suppliers.buyAt', { name: only.supplier_name })}
        aria-label={t('inventory.suppliers.buyAt', { name: only.supplier_name })}
      >
        <ShoppingBag className={iconClassName} />
      </a>
    );
  }

  return (
    <div className="inline-flex" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        ref={buttonRef}
        onClick={() => setOpen((prev) => !prev)}
        className={className}
        title={t('inventory.suppliers.buy')}
        aria-label={t('inventory.suppliers.buy')}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <ShoppingBag className={iconClassName} />
      </button>
      {open && pos && createPortal(
        <div
          role="menu"
          ref={menuRef}
          style={{ position: 'fixed', top: pos.top, bottom: pos.bottom, left: pos.left, minWidth: MENU_WIDTH }}
          className="z-[100] py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg"
        >
          {options.map((option) => (
            <a
              key={option.supplier_id}
              role="menuitem"
              href={option.product_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
              className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm text-white hover:bg-bambu-dark-tertiary"
            >
              <span className={option.is_purchase_source ? 'text-bambu-green' : undefined}>{option.supplier_name}</span>
              {option.quoted_price_per_kg != null && currencySymbol && (
                <span className="text-xs text-bambu-gray">
                  {t('inventory.suppliers.pricePerKg', {
                    price: `${currencySymbol}${option.quoted_price_per_kg.toFixed(2)}`,
                  })}
                </span>
              )}
            </a>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
