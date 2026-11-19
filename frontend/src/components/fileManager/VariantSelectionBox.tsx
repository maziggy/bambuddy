import { Minus } from 'lucide-react';

// The tick box of a variant group: checked when every version in the folder is
// selected, indeterminate when only some are (#3628). A real button, so it can
// be reached and toggled from the keyboard; its click is its own and does not
// reach the row behind it.
export function VariantSelectionBox({
  selectedCount,
  total,
  label,
  onToggle,
  className = '',
  size = 'w-5 h-5',
  tabIndex,
}: {
  selectedCount: number;
  total: number;
  label: string;
  onToggle: () => void;
  className?: string;
  size?: string;
  tabIndex?: number;
}) {
  const all = total > 0 && selectedCount === total;
  const some = selectedCount > 0 && !all;
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={all ? true : some ? 'mixed' : false}
      aria-label={label}
      tabIndex={tabIndex}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      onDoubleClick={(e) => e.stopPropagation()}
      className={`${size} rounded border-2 flex-shrink-0 flex items-center justify-center p-0 transition-all ${
        all || some ? 'bg-bambu-green border-bambu-green' : 'border-bambu-gray/50'
      } ${className}`}
    >
      {all && <div className="w-2 h-2 bg-white rounded-sm" />}
      {some && <Minus className="w-3 h-3 text-white" strokeWidth={3} />}
    </button>
  );
}
