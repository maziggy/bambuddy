import { useTranslation } from 'react-i18next';
import { Barcode } from 'lucide-react';

interface ScanHintProps {
  className?: string;
}

/** Green "or scan the box barcode" affordance shown when a hardware scanner
 *  is available — a scan works from this screen without tapping anything. */
export function ScanHint({ className }: ScanHintProps) {
  const { t } = useTranslation();
  return (
    <div className={`flex items-center gap-2 text-sm text-green-400 ${className ?? ''}`}>
      <Barcode className="w-4 h-4 shrink-0" />
      <span>{t('spoolbuddy.barcode.dashHint', 'Or scan the box barcode — no tap needed')}</span>
    </div>
  );
}
