import { useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface CollapsibleProps {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  summaryClassName?: string;
}

/**
 * Lightweight disclosure used for densifying the Settings page.
 * Renders a clickable summary row and animates open/close via a simple
 * display swap (no height animation — keeps it snappy and layout-stable).
 */
export function Collapsible({
  summary,
  children,
  defaultOpen = false,
  className = '',
  summaryClassName = '',
}: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center justify-between gap-2 text-left ${summaryClassName}`}
        aria-expanded={open}
      >
        <div className="flex-1 min-w-0">{summary}</div>
        <ChevronDown
          className={`w-4 h-4 text-bambu-gray flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  );
}
