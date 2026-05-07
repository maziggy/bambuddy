// Settings search registry.
//
// Each settings card/section registers itself at module-import time by calling
// `registerSettingsSearch(...)` at module scope (NOT inside a component).
// SettingsPage reads the accumulated registry to power its cross-tab search.
//
// Convention: co-locate the registration call with the component/section that
// owns the `anchor` id. When you add a new settings card, add one call here
// next to it — no central index to forget to update.

export type SettingsSearchTab =
  | 'general'
  | 'plugs'
  | 'notifications'
  | 'queue'
  | 'filament'
  | 'network'
  | 'apikeys'
  | 'virtual-printer'
  | 'spoolbuddy'
  | 'users'
  | 'backup'
  | 'failure-detection';

export type SettingsSearchSubTab = 'users' | 'email' | 'ldap' | 'oidc' | 'twofa' | 'security';

export type UsersSubTab = SettingsSearchSubTab;

export interface SettingsSearchEntry {
  /** i18n key for the label. Resolved with t() at render time. */
  labelKey: string;
  /** Fallback label if the i18n key is missing. */
  labelFallback?: string;
  tab: SettingsSearchTab;
  subTab?: SettingsSearchSubTab;
  /** Space-separated extra search terms (lowercase). */
  keywords: string;
  /** DOM id attached to the target card — used for scrollIntoView. */
  anchor: string;
}

const entries = new Map<string, SettingsSearchEntry>();

export function registerSettingsSearch(entry: SettingsSearchEntry): void {
  entries.set(entry.anchor, entry);
}

export function getSettingsSearchEntries(): SettingsSearchEntry[] {
  return Array.from(entries.values());
}
