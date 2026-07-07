/**
 * Sponsor-prompt toast hook. Fires once per browser session: after auth
 * resolves, hits /sponsor-prompt/check; if a trigger is eligible, displays a
 * persistent toast with a "View supporters" CTA that links to the public
 * sponsors page with a Matomo-trackable `?from=app-toast-{milestone}` param.
 *
 * The 14-day cooldown + already-seen-milestone deduplication is owned by the
 * backend service. The hook trusts the check endpoint's verdict, and the moment
 * it actually renders the toast it POSTs /dismiss to anchor the cooldown — being
 * *shown* is what arms the 14-day gate, not the user clicking the CTA. (Clicking
 * is optional; without this record-on-show, an ignored toast would never persist
 * any state and would re-fire on every fresh browser session.)
 */
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { sponsorPromptApi, type SponsorPromptCheckResponse } from '../api/client';
import { getCurrencySymbol } from '../utils/currency';

const TOAST_ID = 'sponsor-prompt';
const SESSION_SHOWN_KEY = 'sponsorPromptShown';

function _num(v: unknown, fallback = 0): number {
  return typeof v === 'number' ? v : fallback;
}

function _str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

function buildMessage(
  t: ReturnType<typeof useTranslation>['t'],
  trigger: SponsorPromptCheckResponse,
  currencyCode: string,
): string | null {
  const family = trigger.family;
  const payload = trigger.payload ?? {};
  const threshold = trigger.threshold ?? 0;
  switch (family) {
    case 'prints':
      return t('sponsors.toastPrints', { count: _num(payload.count, threshold) });
    case 'archives':
      return t('sponsors.toastArchives', { count: _num(payload.count, threshold) });
    case 'cost': {
      const total = _num(payload.total, threshold);
      const symbol = getCurrencySymbol(currencyCode);
      return t('sponsors.toastCost', { total: `${symbol}${total}` });
    }
    case 'anniversary':
      return t('sponsors.toastAnniversary');
    case 'version-update':
      return t('sponsors.toastVersionUpdate', { version: _str(payload.to) });
    default:
      return null;
  }
}

export function useSponsorPrompt(currencyCode = 'EUR') {
  const { t } = useTranslation();
  const { loading } = useAuth();
  const { showPersistentToast } = useToast();
  const firedRef = useRef(false);

  useEffect(() => {
    if (loading || firedRef.current) return;
    if (sessionStorage.getItem(SESSION_SHOWN_KEY)) {
      firedRef.current = true;
      return;
    }
    firedRef.current = true;
    sessionStorage.setItem(SESSION_SHOWN_KEY, '1');

    (async () => {
      try {
        const result = await sponsorPromptApi.check();
        if (!result.show || !result.milestone) return;
        const message = buildMessage(t, result, currencyCode);
        if (!message) return;
        showPersistentToast(TOAST_ID, message, 'info', {
          action: {
            label: t('sponsors.viewSupporters', 'View supporters'),
            href: `https://bambuddy.cool/sponsors.html?from=app-toast-${result.milestone}`,
          },
        });
        // Anchor the 14-day cooldown as soon as the toast is on screen, so an
        // ignored toast doesn't re-fire on the next browser session.
        void sponsorPromptApi.dismiss(result.milestone);
      } catch {
        // Network / 401 — silently skip; next session retries.
      }
    })();
  }, [loading, t, showPersistentToast, currencyCode]);
}
