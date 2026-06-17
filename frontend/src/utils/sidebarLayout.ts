export const SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY = 'sidebarHiddenSystemItems';
export const SIDEBAR_ORDER_KEY = 'sidebarOrder';
export const SIDEBAR_LAYOUT_CHANGED_EVENT = 'sidebar-layout-changed';

export function isExternalSidebarItemId(id: string): boolean {
  return id.startsWith('ext-');
}

export function getSidebarOrder(defaultOrder: string[]): string[] {
  const stored = localStorage.getItem(SIDEBAR_ORDER_KEY);
  if (!stored) return defaultOrder;

  try {
    const parsed = JSON.parse(stored);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : defaultOrder;
  } catch {
    return defaultOrder;
  }
}

export function saveSidebarOrder(order: string[]) {
  localStorage.setItem(SIDEBAR_ORDER_KEY, JSON.stringify(order));
  window.dispatchEvent(new CustomEvent(SIDEBAR_LAYOUT_CHANGED_EVENT));
}

export function getHiddenSidebarSystemItemIds(): string[] {
  const stored = localStorage.getItem(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY);
  if (!stored) return [];

  try {
    const parsed = JSON.parse(stored);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

export function saveHiddenSidebarSystemItemIds(ids: string[]) {
  localStorage.setItem(SIDEBAR_HIDDEN_SYSTEM_ITEMS_KEY, JSON.stringify(Array.from(new Set(ids))));
  window.dispatchEvent(new CustomEvent(SIDEBAR_LAYOUT_CHANGED_EVENT));
}
