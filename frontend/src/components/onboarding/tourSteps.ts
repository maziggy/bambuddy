/**
 * Step definitions for the onboarding tour engine.
 *
 * Each step points the engine at a `data-tour="..."` anchor (mounted by the
 * earlier anchor PR) and provides the i18n keys for its title + body. Steps
 * also carry an optional `route` so the engine can navigate before the anchor
 * could possibly resolve.
 *
 * See docs/onboarding-tour-plan.md for the source-of-truth step layout. This
 * implementation ships a subset (auth → add printer → sidebar overview →
 * outro) — the deeper Phase 1.3 / 1.4 steps need a real printer wired in
 * before their anchors can render, so they're owned by a follow-up PR that
 * adds conditional skipping.
 */
export interface TourStepContext {
  /** True when Bambuddy is running with authentication on. */
  authEnabled: boolean;
  /** Total printers configured on this instance. */
  printerCount: number;
  /** Permission check for skipping permission-gated steps (e.g. MakerWorld). */
  hasPermission: (permission: string) => boolean;
}

import type { MascotPose } from './MascotIcon';

export interface TourStep {
  /** Stable identifier — persisted as `tour_in_progress:<id>` in the backend. */
  id: string;
  /** CSS selector for the anchor. Null means a centred modal with no highlight. */
  anchor: string | null;
  /** Path (with optional ?query) to navigate to before the step renders. */
  route?: string;
  /** i18n key for the step title. */
  titleKey: string;
  /** i18n key for the step body copy. */
  bodyKey: string;
  /** BB pose to display in the step modal. */
  pose?: MascotPose;
  /** Returns true when the step should be auto-skipped under the current
   *  app state. Engine advances past it without rendering anything. */
  skipIf?: (ctx: TourStepContext) => boolean;
}

export const TOUR_STEPS: TourStep[] = [
  // Phase 1.1 "Lock the front door first" used to live here. Removed — the
  // /setup page already prompts for the auth choice on fresh installs, and
  // users who deliberately chose no-auth should not be nudged to enable it.
  // The plan's Step 1.1 content remains for design reference only.
  {
    id: 'add-printer',
    anchor: '[data-tour="add-printer-button"]',
    route: '/',
    titleKey: 'onboarding.addPrinter.title',
    bodyKey: 'onboarding.addPrinter.body',
    pose: 'almost',
    skipIf: (ctx) => ctx.printerCount > 0,
  },
  {
    id: 'verify-connection',
    anchor: '[data-tour="printer-status-pill"]',
    route: '/',
    titleKey: 'onboarding.verifyConnection.title',
    bodyKey: 'onboarding.verifyConnection.allGreen',
    pose: 'almost',
    // No printer? Nothing to verify.
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  // Phase 1.4 — card sub-tour. Each step highlights one part of the printer
  // card. All five share the same title and skip when no printer exists.
  {
    id: 'card-status',
    anchor: '[data-tour="printer-status-row"]',
    route: '/',
    titleKey: 'onboarding.tourCard.title',
    bodyKey: 'onboarding.tourCard.status',
    pose: 'walk',
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  {
    id: 'card-ams',
    anchor: '[data-tour="printer-ams-row"]',
    route: '/',
    titleKey: 'onboarding.tourCard.title',
    bodyKey: 'onboarding.tourCard.ams',
    pose: 'walk',
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  {
    id: 'card-camera',
    anchor: '[data-tour="printer-camera"]',
    route: '/',
    titleKey: 'onboarding.tourCard.title',
    bodyKey: 'onboarding.tourCard.camera',
    pose: 'walk',
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  {
    id: 'card-controls',
    anchor: '[data-tour="printer-controls"]',
    route: '/',
    titleKey: 'onboarding.tourCard.title',
    bodyKey: 'onboarding.tourCard.controls',
    pose: 'walk',
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  {
    id: 'card-customize',
    anchor: '[data-tour="printer-customize"]',
    route: '/',
    titleKey: 'onboarding.tourCard.title',
    bodyKey: 'onboarding.tourCard.customize',
    pose: 'help',
    skipIf: (ctx) => ctx.printerCount === 0,
  },
  {
    id: 'add-spool',
    anchor: '[data-tour="add-spool-button"]',
    route: '/inventory',
    titleKey: 'onboarding.addSpool.title',
    bodyKey: 'onboarding.addSpool.intro',
    pose: 'help',
  },
  {
    id: 'bambu-cloud',
    anchor: '[data-tour="bambu-cloud-sync"]',
    route: '/profiles',
    titleKey: 'onboarding.bambuCloud.title',
    bodyKey: 'onboarding.bambuCloud.body',
    pose: 'walk',
  },
  {
    id: 'sidebar-queue',
    anchor: '[data-tour="sidebar-queue"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.queue',
    pose: 'walk',
  },
  {
    id: 'sidebar-archives',
    anchor: '[data-tour="sidebar-archives"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.archives',
    pose: 'walk',
  },
  {
    id: 'sidebar-stats',
    anchor: '[data-tour="sidebar-stats"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.stats',
    pose: 'walk',
  },
  {
    id: 'sidebar-maintenance',
    anchor: '[data-tour="sidebar-maintenance"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.maintenance',
    pose: 'walk',
  },
  {
    id: 'sidebar-files',
    anchor: '[data-tour="sidebar-files"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.files',
    pose: 'walk',
  },
  {
    id: 'sidebar-projects',
    anchor: '[data-tour="sidebar-projects"]',
    titleKey: 'onboarding.sidebar.title',
    bodyKey: 'onboarding.sidebar.projects',
    pose: 'walk',
  },
  // Phase 3 — power features behind the implicit "Interested?" gate (which
  // today is just the Skip button). Each step targets a Settings sub-card
  // and navigates to the right tab.
  {
    id: 'vp',
    anchor: '[data-tour="vp-card"]',
    route: '/settings?tab=virtual-printer',
    titleKey: 'onboarding.vp.title',
    bodyKey: 'onboarding.vp.body',
    pose: 'help',
  },
  {
    id: 'slicer-api',
    anchor: '[data-tour="slicer-api-card"]',
    route: '/settings?tab=queue',
    titleKey: 'onboarding.slicerApi.title',
    bodyKey: 'onboarding.slicerApi.body',
    pose: 'walk',
  },
  {
    id: 'makerworld',
    anchor: '[data-tour="sidebar-makerworld"]',
    titleKey: 'onboarding.makerworld.title',
    bodyKey: 'onboarding.makerworld.body',
    pose: 'walk',
    // Permission-gated nav entry — if the user does not have makerworld:view,
    // the sidebar item itself is hidden and the anchor would not resolve.
    skipIf: (ctx) => !ctx.hasPermission('makerworld:view'),
  },
  {
    id: 'obico',
    anchor: '[data-tour="obico-card"]',
    route: '/settings?tab=failure-detection',
    titleKey: 'onboarding.obico.title',
    bodyKey: 'onboarding.obico.body',
    pose: 'help',
  },
  {
    id: 'integrations',
    anchor: '[data-tour="integrations-card"]',
    route: '/settings?tab=network',
    titleKey: 'onboarding.integrations.title',
    bodyKey: 'onboarding.integrations.body',
    pose: 'walk',
  },
  {
    id: 'notifications',
    anchor: '[data-tour="sidebar-notifications"]',
    titleKey: 'onboarding.notifications.title',
    bodyKey: 'onboarding.notifications.body',
    pose: 'walk',
  },
  // Phase 4 — multi-user setup. Only relevant when auth is on.
  {
    id: 'users',
    anchor: '[data-tour="add-user-button"]',
    route: '/settings?tab=users',
    titleKey: 'onboarding.users.title',
    bodyKey: 'onboarding.users.body',
    pose: 'help',
    skipIf: (ctx) => !ctx.authEnabled,
  },
  {
    id: 'groups',
    anchor: '[data-tour="groups-section"]',
    route: '/settings?tab=users',
    titleKey: 'onboarding.groups.title',
    bodyKey: 'onboarding.groups.body',
    pose: 'walk',
    skipIf: (ctx) => !ctx.authEnabled,
  },
  {
    id: 'sso',
    anchor: '[data-tour="sso-section"]',
    route: '/settings?tab=users',
    titleKey: 'onboarding.sso.title',
    bodyKey: 'onboarding.sso.body',
    pose: 'help',
    skipIf: (ctx) => !ctx.authEnabled,
  },
  {
    id: 'outro',
    anchor: null,
    titleKey: 'onboarding.outro.title',
    bodyKey: 'onboarding.outro.system',
    pose: 'allset',
  },
];

/** Returns the step index for a status string like `tour_in_progress:<id>`. */
export function stepIndexFromStatus(status: string | null): number {
  if (!status) return -1;
  const prefix = 'tour_in_progress:';
  if (!status.startsWith(prefix)) return -1;
  const id = status.slice(prefix.length);
  return TOUR_STEPS.findIndex((s) => s.id === id);
}

/** Builds the status string for a given step index. */
export function statusForStep(index: number): string {
  const step = TOUR_STEPS[index];
  if (!step) throw new Error(`Invalid tour step index: ${index}`);
  return `tour_in_progress:${step.id}`;
}
