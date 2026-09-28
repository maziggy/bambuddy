// HMS Error Modal. Descriptions come from the backend (HMSError.description),
// which resolves them from the catalogue generated out of Bambu Studio (#2728).
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { X, AlertTriangle, AlertCircle, Info, ExternalLink, Loader2, Trash2 } from 'lucide-react';
import type { HMSError, Permission } from '../api/client';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';

interface HMSErrorModalProps {
  printerName: string;
  errors: HMSError[];
  onClose: () => void;
  printerId: number;
  hasPermission: (permission: Permission) => boolean;
  // Runout guidance for a PAUSED print (#2587). When set, AMS-runout errors are
  // re-described to name the physical slot the firmware now expects, instead of
  // the generic "insert into the same slot" text (wrong under AMS Filament Backup).
  // Slot labels are pre-formatted (e.g. "AMS-A · Slot 3"); null when the slot
  // could not be resolved → an honest "check the printer" message is shown.
  runoutGuidance?: {
    expectedSlotLabel: string | null;
    ranOutSlotLabel: string | null;
  } | null;
}

// AMS per-slot filament-runout short codes (module 0x07). These pause the print
// waiting for a specific slot — the ones #2587 re-describes. Printer-side /
// external runout (0300_8004) has no AMS slot and is deliberately excluded.
const AMS_RUNOUT_SHORT_CODES = new Set([
  '0700_8011', '0701_8011', '0702_8011', '0703_8011', '0704_8011',
  '0705_8011', '0706_8011', '0707_8011', '07FF_8011',
]);

// "MQTT command verification failed" — the firmware's authorization check
// refusing a control command. Matched on its full 16-char code: this error's
// meaning lives in attr's low half (0500) and code's high half (0001), both of
// which getShortCode() discards. Bambu's own text for it says to update Studio
// or Handy, which is no help from Bambuddy, so the modal says what it means here.
export const HMS_MQTT_VERIFY_FAILED = '0500050000010007';

type SeverityInfo = { labelKey: string; color: string; bgColor: string; Icon: typeof AlertTriangle };

// Bambu's alert levels: 1 error (the task is stopped), 2 warning (the task is
// paused), 3 notification (no impact). 0 is Bambu's "invalid" level and
// anything else is not defined, so both read as unknown rather than as
// information (#2728).
function getSeverityInfo(severity: number): SeverityInfo {
  switch (severity) {
    case 1:
      return { labelKey: 'hmsErrors.severityError', color: 'text-red-600 dark:text-red-400', bgColor: 'bg-red-100 dark:bg-red-500/20', Icon: AlertTriangle };
    case 2:
      return { labelKey: 'hmsErrors.severityWarning', color: 'text-orange-700 dark:text-orange-400', bgColor: 'bg-orange-100 dark:bg-orange-500/20', Icon: AlertTriangle };
    case 3:
      return { labelKey: 'hmsErrors.severityNotice', color: 'text-blue-700 dark:text-blue-400', bgColor: 'bg-blue-100 dark:bg-blue-500/20', Icon: Info };
    default:
      return { labelKey: 'hmsErrors.severityUnknown', color: 'text-bambu-gray', bgColor: 'bg-bambu-dark-tertiary', Icon: AlertCircle };
  }
}

// A fault that stops or pauses the task — what turns the printer card's HMS
// indicator red instead of amber.
export function isSevereHMSError(error: HMSError): boolean {
  return error.severity === 1 || error.severity === 2;
}

function getShortCode(attr: number, code: number): string {
  // Convert attr and code to short format: XXXX_YYYY
  // attr contains the module info, code contains the error number
  const module = ((attr >> 16) & 0xFFFF) || ((attr >> 8) & 0xFF) << 8 | (attr & 0xFF);
  const codeNum = code & 0xFFFF;
  return `${module.toString(16).padStart(4, '0').toUpperCase()}_${codeNum.toString(16).padStart(4, '0').toUpperCase()}`;
}

// The code as the printer screen and Bambu's wiki write it: four groups for an
// hms[] fault ("0500-0300-0002-000E"), two for a print_error ("0300-8004").
function getDisplayCode(error: HMSError): string {
  if (error.full_code && error.full_code.length === 16) return error.full_code.match(/.{4}/g)!.join('-');
  const codeNum = parseInt(error.code.replace('0x', ''), 16) || 0;
  return getShortCode(error.attr, codeNum).replace('_', '-');
}

// Same rule as the backend's _hms_fault_counts, which decides notifications and
// the MQTT relay.
function counts(error: HMSError): boolean {
  if (!(error.severity >= 1)) return false;
  if ((error.actions?.length ?? 0) > 0) return true;
  const isHmsNotice = error.full_code?.length === 16 && error.severity === 3;
  return !!error.description && !isHmsNotice;
}

// The faults that count: the printer card's badge and pip, the camera wall,
// the bulk toolbar's problem state. A fault counts if it offers firmware
// actions — an uncatalogued but actionable fault such as H2C 0500_809C must
// surface so its buttons can render — or if Bambu publishes text for it
// (HMSError.description, resolved by the backend from the catalogue generated
// out of Bambu Studio). Two kinds don't count: codes Bambu lists without text or
// not at all, such as the post-cancel 0C00_001B echo, which would otherwise hold
// a "1 problem" badge on a printer that has none (see
// PrintersPageBucketing.test.ts); and hms[] notices (level 3) without actions,
// such as "the top cover is open", which a printer can hold through a whole
// print. A print_error prompt at the same level (0xCxxx) still counts, as it
// always has (#2728).
export function filterKnownHMSErrors(errors: HMSError[]): HMSError[] {
  return errors.filter(counts);
}

// The faults the printer reported that don't count. The modal still lists them,
// collapsed and with whatever text Bambu has for them, so a fault the printer
// is holding is never invisible (#2728).
export function filterUncountedHMSErrors(errors: HMSError[]): HMSError[] {
  return errors.filter((error) => !counts(error));
}

function getHMSHomeUrl(): string {
  return `https://wiki.bambulab.com/en/hms/home`;
}

export function HMSErrorModal({ printerName, errors, onClose, printerId, hasPermission, runoutGuidance }: HMSErrorModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const clearMutation = useMutation({
    mutationFn: () => api.clearHMSErrors(printerId),
    onSuccess: () => {
      showToast(t('hmsErrors.clearSuccess'), 'success');
      onClose();
    },
    onError: () => {
      showToast(t('hmsErrors.clearFailed'), 'error');
    },
  });

  // The faults that count, exactly as the badge counts them, then the ones the
  // printer reported that don't, listed collapsed underneath.
  const knownErrors = filterKnownHMSErrors(errors);
  const uncountedErrors = filterUncountedHMSErrors(errors);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // printerStatusMutation with optimistic update
  const activateActionMutation = useMutation({
    mutationFn: (data: {
      action: string,
      print_error: string,
      job_id: string | null,
    }) => api.executeHMSAction(printerId, {
      action: data.action,
      print_error: data.print_error,
      job_id: data.job_id,
    }),
    onSuccess: () => {
      // Scope the invalidation to THIS printer. The prefix form
      // `['printerStatus']` would refresh every printer card on the page,
      // which is wasteful when only one printer's state actually changed.
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
      showToast(t('hmsErrors.actionSuccess', 'Action sent to printer'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      showToast(
        `${t('hmsErrors.actionFailed', 'Failed to send action')}: ${error.message}`,
        'error',
      );
    },
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg shadow-xl max-w-lg w-full max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-orange-600 dark:text-orange-400" />
            <h2 className="text-lg font-semibold text-white">{t('hmsErrors.title', { name: printerName })}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {knownErrors.length === 0 && uncountedErrors.length === 0 ? (
            <div className="text-center py-8 text-bambu-gray">
              <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>{t('hmsErrors.noErrors')}</p>
            </div>
          ) : (
            <div className="space-y-3">
              {knownErrors.map((error, index) => {
                const { labelKey, color, bgColor, Icon } = getSeverityInfo(error.severity);
                const codeNum = parseInt(error.code.replace('0x', ''), 16) || 0;
                const shortCode = getShortCode(error.attr, codeNum);
                // Runout guidance (#2587): for an AMS per-slot runout on a paused
                // print, name the slot the firmware now expects rather than the
                // misleading generic "insert into the same slot" text.
                // An actionable fault can count without text; it still gets a line.
                let description = error.description || t('hmsErrors.unknownCode');
                // The text and remedy are Bambuddy's, not Bambu's — their text says
                // "update Studio or Handy", which is no help to someone printing
                // from Bambuddy. Same override shape as the runout guidance below.
                const isVerifyFailed = error.full_code === HMS_MQTT_VERIFY_FAILED;
                if (isVerifyFailed) description = t('hmsErrors.mqttVerifyFailedDescription');
                const remedy = isVerifyFailed ? t('hmsErrors.mqttVerifyFailedRemedy') : null;
                if (runoutGuidance && AMS_RUNOUT_SHORT_CODES.has(shortCode)) {
                  if (runoutGuidance.expectedSlotLabel && runoutGuidance.ranOutSlotLabel) {
                    description = t('hmsErrors.runoutExpectedSlot', {
                      expected: runoutGuidance.expectedSlotLabel,
                      ranOut: runoutGuidance.ranOutSlotLabel,
                    });
                  } else if (runoutGuidance.expectedSlotLabel) {
                    description = t('hmsErrors.runoutExpectedSlotOnly', {
                      expected: runoutGuidance.expectedSlotLabel,
                    });
                  } else {
                    description = t('hmsErrors.runoutSlotUnknown');
                  }
                }
                const hmsHomeUrl = getHMSHomeUrl();
                const displayCode = getDisplayCode(error);

                return (
                  <div
                    key={`${error.code}-${index}`}
                    className={`p-4 rounded-lg ${bgColor} border border-white/10`}
                  >
                    <div className="flex items-start gap-3">
                      <Icon className={`w-5 h-5 ${color} flex-shrink-0 mt-0.5`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`font-mono text-sm ${color}`}>[{displayCode}]</span>
                          <span className={`text-xs px-2 py-0.5 rounded-full ${bgColor} ${color}`}>
                            {t(labelKey)}
                          </span>
                        </div>
                        <p className="text-sm text-bambu-gray mb-2">{description}</p>
                        {remedy && <p className="text-sm text-bambu-gray mb-2">{remedy}</p>}
                        {error.actions && error.actions.length > 0 && (
                          <div className="flex flex-wrap gap-2 my-2">
                            {error.actions.map((action) => {
                              const pendingVars = activateActionMutation.variables;
                              const isThisPending =
                                activateActionMutation.isPending
                                && pendingVars?.action === action
                                && pendingVars?.print_error === (error.full_code || shortCode.replace('_', ''));
                              return (
                                <button
                                  key={action}
                                  onClick={() => {
                                    // full_code is the firmware-matching key (16
                                    // chars for hms[]-array faults, 8 chars for
                                    // print_error). Fall back to the 8-char
                                    // shortCode for older backends that haven't
                                    // populated it. See #1830.
                                    activateActionMutation.mutate({
                                      action,
                                      print_error: error.full_code || shortCode.replace('_', ''),
                                      job_id: error.job_id ?? null,
                                    });
                                  }}
                                  // Static hover/active classes — Tailwind's JIT
                                  // can't resolve `hover:${var}` template
                                  // literals, so the previous severity-tinted
                                  // hover never reached the compiled CSS and
                                  // the action buttons read as inert badges.
                                  // White-on-tint reads as a clear affordance
                                  // against any severity-coloured container.
                                  disabled={!hasPermission('printers:control') || activateActionMutation.isPending}
                                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-white/10 hover:bg-white/20 active:bg-white/30 text-white border border-white/20 hover:border-white/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
                                >
                                  {isThisPending && <Loader2 className="w-4 h-4 animate-spin" />}
                                  {t(`hmsErrors.actions.${action}`, action)}
                                </button>
                              );
                            })}
                          </div>
                        )}
                        <a
                          href={hmsHomeUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-bambu-green hover:underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          {t('hmsErrors.viewOnWiki')}
                        </a>
                      </div>
                    </div>
                  </div>
                );
              })}
              {uncountedErrors.length > 0 && (
                <details className="rounded-lg border border-white/10 p-3" data-testid="hms-uncounted">
                  <summary className="cursor-pointer text-sm text-bambu-gray">
                    {t('hmsErrors.uncountedSummary', { n: uncountedErrors.length })}
                  </summary>
                  <p className="mt-2 text-xs text-bambu-gray">{t('hmsErrors.uncountedHint')}</p>
                  <ul className="mt-2 space-y-2">
                    {uncountedErrors.map((error, index) => (
                      <li key={`${error.full_code || error.code}-${index}`} className="text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-bambu-gray">[{getDisplayCode(error)}]</span>
                          <span className="text-bambu-gray">{t(getSeverityInfo(error.severity).labelKey)}</span>
                        </div>
                        {error.description && <p className="mt-0.5 text-bambu-gray">{error.description}</p>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-bambu-dark-tertiary flex items-center justify-between gap-3">
          <p className="text-xs text-bambu-gray">
            {t('hmsErrors.clearInstructions')}
          </p>
          {errors.length > 0 && (
            <button
              onClick={() => clearMutation.mutate()}
              disabled={!hasPermission('printers:control') || clearMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-500/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
            >
              {clearMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              {t('hmsErrors.clearErrors')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
