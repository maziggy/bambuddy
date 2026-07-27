import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface AlertModalProps {
  title: string;
  message: string;
  /** Optional secondary line shown above the message — e.g. the file the
   *  alert is about. */
  subtitle?: string;
  closeText?: string;
  variant?: 'error' | 'warning';
  onClose: () => void;
}

/**
 * A small acknowledge-only modal: title, message, single Close button.
 *
 * Use it to surface something the user must read and act on, where a toast
 * would auto-dismiss before it can be read (e.g. a slicer rejection message).
 * For confirm/cancel decisions use ConfirmModal instead.
 */
export function AlertModal({
  title,
  message,
  subtitle,
  closeText,
  variant = 'error',
  onClose,
}: AlertModalProps) {
  const { t } = useTranslation();
  const resolvedCloseText = closeText ?? t('common.close');

  // Close on Escape — matches ConfirmModal's behaviour.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const iconColor = variant === 'warning' ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400';

  return (
    <div
      // z-[120]: above other modals (z-50 / z-[110]) and the toast stack, so
      // a slice failure surfaced from app-level context is never occluded.
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-[120]"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className={`p-2 rounded-full bg-bambu-dark ${iconColor}`}>
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-white mb-1">{title}</h3>
              {subtitle && <p className="text-xs text-bambu-gray mb-2 truncate">{subtitle}</p>}
              <p className="text-bambu-gray text-sm whitespace-pre-line break-words">{message}</p>
            </div>
          </div>
          <div className="flex mt-6">
            <Button onClick={onClose} className="flex-1">
              {resolvedCloseText}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
