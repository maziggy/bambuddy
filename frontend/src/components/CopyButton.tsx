import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, Check } from 'lucide-react';

interface CopyButtonProps {
  value: string;
  /** i18n key for the resting tooltip. */
  titleKey?: string;
  /** i18n key for the tooltip while the tick is showing. */
  copiedTitleKey?: string;
  className?: string;
  iconClassName?: string;
}

/**
 * Copy-to-clipboard button with the plain-HTTP fallback (#1174).
 *
 * Lifted out of PrinterInfoModal when the Docker update instructions needed
 * the same control (#2664). The fallback is the whole reason this is shared
 * rather than re-written per call site: navigator.clipboard is gated behind
 * the secure-context requirement, so on a LAN install reached over plain HTTP
 * — which is most Bambuddy installs — the API is simply undefined, and a
 * naive implementation swallows the failure with no tick and nothing copied.
 */
export function CopyButton({
  value,
  titleKey = 'printers.copyToClipboard',
  copiedTitleKey = 'printers.copied',
  className = 'ml-2 p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors',
  iconClassName = 'w-3.5 h-3.5',
}: CopyButtonProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        // Legacy execCommand path via an off-screen textarea, matching the
        // pattern used by CameraTokensPage's plaintext-token modal.
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        try {
          ta.select();
          const ok = document.execCommand('copy');
          if (!ok) return;
        } finally {
          document.body.removeChild(ta);
        }
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Both paths failed (no clipboard API, no execCommand). Leave the icon
      // unchanged so the user knows nothing was copied.
    }
  };

  return (
    <button
      onClick={handleCopy}
      className={className}
      title={copied ? t(copiedTitleKey) : t(titleKey)}
    >
      {copied ? <Check className={`${iconClassName} text-bambu-green`} /> : <Copy className={iconClassName} />}
    </button>
  );
}
