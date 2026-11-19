import { Minus } from 'lucide-react';

// The tick box of a variant group: checked when every visible version is
// selected, indeterminate when only some are (#3628).
export function VariantSelectionBox({
  selectedCount,
  total,
  label,
  className = '',
  size = 'w-5 h-5',
}: {
  selectedCount: number;
  total: number;
  label: string;
  className?: string;
  size?: string;
}) {
  const all = total > 0 && selectedCount === total;
  const some = selectedCount > 0 && !all;
  return (
    <div
      role="checkbox"
      aria-checked={all ? true : some ? 'mixed' : false}
      aria-label={label}
      className={`${size} rounded border-2 flex-shrink-0 flex items-center justify-center transition-all ${
        all || some ? 'bg-bambu-green border-bambu-green' : 'border-bambu-gray/50'
      } ${className}`}
    >
      {all && <div className="w-2 h-2 bg-white rounded-sm" />}
      {some && <Minus className="w-3 h-3 text-white" strokeWidth={3} />}
    </div>
  );
}
