// How a post-print outcome verdict reached an archive (#1898). The backend
// stamps it wherever a verdict is written, so a "good" badge nobody remembers
// clicking can say where it came from — the live case being the plate-clear
// default answering a prompt before the operator reached the phone.
//
// 'reaction' is written by the Telegram reaction handler (#3046), which lands
// separately; it is mapped here so it reads correctly the day it does.

import type { VerdictSource } from '../api/client';

const SOURCE_KEYS: Record<VerdictSource, string> = {
  dialog: 'confirmOutcome.sources.dialog',
  link: 'confirmOutcome.sources.link',
  plate_clear: 'confirmOutcome.sources.plateClear',
  printer_card: 'confirmOutcome.sources.printerCard',
  api: 'confirmOutcome.sources.api',
  reaction: 'confirmOutcome.sources.reaction',
};

/**
 * i18n key for a verdict source, or null when the source is unknown — archives
 * answered before this shipped carry no source and must show no hint at all.
 */
export function verdictSourceKey(source: string | null | undefined): string | null {
  if (!source) return null;
  return SOURCE_KEYS[source as VerdictSource] ?? null;
}
