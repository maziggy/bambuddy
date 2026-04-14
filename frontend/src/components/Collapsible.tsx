import { useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface CollapsibleProps {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  summaryClassName?: string;
  /** When provided, the component is controlled — parent owns the open state. */
  open?: boolean;
  /** Called when the user clicks the toggle. Use with `open` for controlled mode. */
  onToggle?: (open: boolean) => void;
}

/**
 * Lightweight disclosure widget.
 * Renders a clickable summary row and conditionally displays children.
 *
 * The toggle region is a plain <div> with role="button" so that the summary
 * slot may safely contain interactive elements (buttons, links) without
 * nesting a <button> inside a <button>.
 *
 * Supports both uncontrolled (internal state) and controlled (`open`/`onToggle`) modes.
 */
export function Collapsible({
  summary,
  children,
  defaultOpen = false,
  className = '',
  summaryClassName = '',
  open: controlledOpen,
  onToggle,
}: CollapsibleProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : internalOpen;

  const handleToggle = () => {
    const next = !isOpen;
    if (!isControlled) setInternalOpen(next);
    onToggle?.(next);
  };

  return (
    <div className={className}>
      <div
        role="button"
        tabIndex={0}
        onClick={handleToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleToggle(); } }}
        className={`w-full flex items-center justify-between gap-2 text-left cursor-pointer ${summaryClassName}`}
        aria-expanded={isOpen}
      >
        <div className="flex-1 min-w-0">{summary}</div>
        <ChevronDown
          className={`w-4 h-4 text-bambu-gray flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`}
        />
      </div>
      {isOpen && <div className="mt-3">{children}</div>}
    </div>
  );
}
