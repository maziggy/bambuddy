import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShoppingBag } from 'lucide-react';
import type { BuyOption } from '../utils/productLinks';

// "Buy" for a spool or a product (#2540): opens the product's page at its
// supplier. One place: a link. Several: a menu of them, the supplier it was
// bought from first. Nothing to open: nothing rendered. Clicks stay here, so
// the row or card around it does not open too.
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
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
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
    <div className="relative inline-flex" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className={className}
        title={t('inventory.suppliers.buy')}
        aria-label={t('inventory.suppliers.buy')}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <ShoppingBag className={iconClassName} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 z-50 min-w-[180px] py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg"
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
        </div>
      )}
    </div>
  );
}
