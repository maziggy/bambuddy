import { useQuery } from '@tanstack/react-query';
import { api, type UiSettings } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

/**
 * The install settings a page needs to render, for any signed-in user.
 *
 * GET /settings needs settings:read, and a user without it got a 403 that left
 * every value at its fallback. Such a user reads the subset served without
 * that permission instead; everyone else keeps the full settings, under the
 * same query key as before.
 */
export function useUiSettings(options: { enabled?: boolean; staleTime?: number } = {}) {
  const { hasPermission } = useAuth();
  const readsAll = hasPermission('settings:read');
  return useQuery<UiSettings>({
    queryKey: readsAll ? ['settings'] : ['ui-settings'],
    queryFn: readsAll ? () => api.getSettings() : () => api.getUiSettings(),
    ...options,
  });
}

/** Whether G-code snippets are set up, from either form of the settings. */
export function hasGcodeSnippets(settings: UiSettings | undefined): boolean {
  return !!(settings?.gcode_snippets || settings?.has_gcode_snippets);
}

/**
 * Whether a label printer is set up (#3612). /settings/ui-flags derives the
 * flag; the full settings carry type and address, so either form answers.
 */
export function isLabelPrinterConfigured(settings: UiSettings | undefined): boolean {
  if (!settings) return false;
  if (typeof settings.label_printer_configured === 'boolean') return settings.label_printer_configured;
  return !!settings.label_printer_type && !!settings.label_printer_host;
}
