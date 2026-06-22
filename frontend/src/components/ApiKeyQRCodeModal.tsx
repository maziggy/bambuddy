import { useEffect } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { X, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from './Button';
import { buildApiKeyQrPayload } from '../utils/apiKeyQr';

interface ApiKeyQRCodeModalProps {
  /** Raw API key string (only available in-memory right after creation). */
  apiKey: string;
  /** Base URL a client uses to reach Bambuddy. Defaults to the current origin. */
  baseUrl?: string;
  onClose: () => void;
}

export function ApiKeyQRCodeModal({ apiKey, baseUrl, onClose }: ApiKeyQRCodeModalProps) {
  const { t } = useTranslation();
  const origin = baseUrl ?? window.location.origin;
  const payload = buildApiKeyQrPayload(origin, apiKey);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-sm"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">{t('settings.apiKeyQrTitle')}</h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 flex flex-col items-center">
          <p className="text-sm text-bambu-gray mb-4 text-center">
            {t('settings.apiKeyQrCaption')}
          </p>
          <div className="bg-white p-4 rounded-lg mb-4">
            <QRCodeSVG value={payload} size={256} />
          </div>
          <div className="flex items-start gap-2 text-xs text-amber-400 mb-4">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{t('settings.apiKeyQrWarning')}</span>
          </div>
          <Button onClick={onClose} className="w-full">
            {t('common.close')}
          </Button>
        </div>
      </div>
    </div>
  );
}
