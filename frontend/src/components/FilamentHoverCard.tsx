import { useState, useRef, useEffect, useLayoutEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Droplets, Copy, Check, Settings2, Package, Unlink } from 'lucide-react';
import { isLightColor, resolveSpoolColorName } from '../utils/colors';
import { buildFilamentBackground, parseStops } from './filamentSwatchHelpers';

interface FilamentData {
  vendor: 'Bambu Lab' | 'Generic';
  profile: string;
  /** Catalogue name for the loaded colour. Callers that know the material
   *  should resolve it with ``getColorName(hex, tray_sub_brands)`` -- a white
   *  Matte spool is Ivory White, not the Jade White that shares its hex
   *  (#2875). An assigned spool overrides this; see ``displayColorName``. */
  colorName: string;
  colorHex: string | null;
  kFactor: string;
  fillLevel: number | null; // null = unknown
  trayUuid?: string | null; // Bambu Lab spool UUID for Spoolman linking
  tagUid?: string | null; // Generic NFC tag UID fallback for linking
  fillSource?: 'ams' | 'spoolman' | 'inventory'; // Source of fill level data
}

interface SpoolmanConfig {
  enabled: boolean;
  onLinkSpool?: () => void;
  onUnlinkSpool?: () => void;
  linkedSpoolId?: number | null; // Spoolman spool ID if this tray is already linked
  spoolmanUrl?: string | null; // Base URL for Spoolman (for "Open in Spoolman" link)
  syncMode?: string | null; // If auto-sync is enabled, we may want to hide the unlink option for Bambu spools
}

interface InventoryConfig {
  onAssignSpool?: () => void;
  onUnassignSpool?: () => void;
  // `subtype` is part of the spool's name, not decoration: "PLA" and "PLA Wood"
  // are different filaments, and a card that prints only the material tells the
  // user their wood-filled roll is plain PLA (the display-side half of #2902).
  // `rgba` / `extra_colors` / `effect_type` are the spool's own swatch. The
  // slot's telemetry colour is a single hex and can never describe a gradient
  // or a surface effect, so a tri-colour roll read as one flat band (#2967).
  assignedSpool?: {
    id: number;
    material: string;
    subtype: string | null;
    brand: string | null;
    color_name: string | null;
    rgba?: string | null;
    extra_colors?: string | null;
    effect_type?: string | null;
    remainingWeightGrams?: number | null;
  } | null;
  isAssigned?: boolean;
}

interface ConfigureSlotConfig {
  enabled: boolean;
  onConfigure?: () => void;
}

interface FilamentHoverCardProps {
  data: FilamentData;
  children: ReactNode;
  disabled?: boolean;
  className?: string;
  spoolman?: SpoolmanConfig;
  inventory?: InventoryConfig;
  configureSlot?: ConfigureSlotConfig;
  actions?: ReactNode;
}

/**
 * A hover card that displays filament details when hovering over AMS slots.
 * Replaces the basic browser tooltip with a styled popover.
 */
export function FilamentHoverCard({ data, children, disabled, className = '', spoolman, inventory, configureSlot, actions }: FilamentHoverCardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  // Screen-space coordinates for the portaled card (#1336 follow-up). Using
  // a portal + position:fixed lets the popover escape sibling printer cards
  // that create their own stacking contexts on the dashboard — without this,
  // a card later in DOM order draws over the hover popover regardless of
  // z-index because z-index doesn't cross stacking-context boundaries.
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const [showUnlinkConfirm, setShowUnlinkConfirm] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleCopyUuid = () => {
    const uuid = data.trayUuid;
    if (!uuid) return;

    // Try modern clipboard API first, fallback to execCommand
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(uuid).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }).catch(() => {
        // Fallback on error
        fallbackCopy(uuid);
      });
    } else {
      fallbackCopy(uuid);
    }
  };

  const fallbackCopy = (text: string) => {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand('copy');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      console.error('Failed to copy to clipboard');
    }
    document.body.removeChild(textarea);
  };

  // Compute placement (top/bottom) + screen coordinates for the portaled
  // card. Runs on visibility change, scroll, and resize so the popover
  // tracks the trigger when the viewport moves. useLayoutEffect rather
  // than useEffect so the first paint already has the correct coords —
  // avoids a one-frame flicker at (0, 0).
  useLayoutEffect(() => {
    if (!isVisible) {
      setCoords(null);
      return;
    }
    const compute = () => {
      if (!triggerRef.current || !cardRef.current) return;
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      const cardWidth = cardRef.current.offsetWidth;
      const headerHeight = 56;
      const spaceAbove = triggerRect.top - headerHeight;
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      const placement: 'top' | 'bottom' =
        spaceAbove < cardHeight + 12 && spaceBelow > spaceAbove ? 'bottom' : 'top';
      const centerX = triggerRect.left + triggerRect.width / 2;
      const left = Math.max(8, Math.min(centerX - cardWidth / 2, window.innerWidth - cardWidth - 8));
      const top = placement === 'top' ? triggerRect.top - cardHeight - 8 : triggerRect.bottom + 8;
      setPosition(placement);
      setCoords({ top, left });
    };
    // First compute is synchronous from the layout effect; a follow-up rAF
    // re-measures after the card actually has its rendered dimensions.
    compute();
    const rafId = requestAnimationFrame(compute);
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [isVisible]);

  const handleMouseEnter = () => {
    if (disabled) return;
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    // Small delay to prevent flicker on quick mouse movements
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };

  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(false), 100);
  };

  // Dismiss the card immediately, for actions that open a dialog or navigate away.
  //
  // The card is portaled at z-[60] so it can escape sibling printer cards' stacking
  // contexts, which puts it ABOVE ConfigureAmsSlotModal and LinkSpoolModal at z-50 —
  // so a card left standing draws over the very dialog it just opened. Mouseleave is
  // the only thing that normally hides it, and a touch device never sends one after
  // the tap that opened the card, so on a tablet it stays up indefinitely (#2631).
  // Clearing the timeout is not optional: a pending show timer would re-open it.
  const dismiss = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setIsVisible(false);
  };

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  // Get fill bar color based on percentage
  const getFillColor = (fill: number): string => {
    if (fill <= 15) return '#ef4444'; // red
    if (fill <= 30) return '#f97316'; // orange
    if (fill <= 50) return '#eab308'; // yellow
    return '#22c55e'; // green
  };

  const colorHex = data.colorHex ? `#${data.colorHex.replace('#', '')}` : null;

  // An assigned spool outranks any hex lookup: it is the roll the user put in
  // this slot, named by whoever created it, and it is already shown two rows
  // below under ASSIGNED. Passing a null rgba keeps the helper to its
  // readable-name test -- a Bambu internal code like "A06-D0" is not a name
  // (#857) and must not displace the catalogue answer, which the caller has
  // already resolved with the slot's material (#2875).
  // Trimmed, so a spool saved with a whitespace-only colour name leaves the
  // swatch reading the catalogue answer instead of reading blank.
  const assignedColorName = resolveSpoolColorName(inventory?.assignedSpool?.color_name ?? null, null)?.trim();
  const displayColorName = assignedColorName || data.colorName;
  const assignedRemainingWeight = inventory?.assignedSpool?.remainingWeightGrams ?? null;

  // The header paints the spool's own swatch whenever the spool describes more
  // than one colour, or any surface effect (#2967). Telemetry cannot: a tray
  // record carries a single `tray_color` hex, so a Tri Color roll of yellow,
  // cyan and pink read as one flat band of whichever hex the slot was
  // configured with.
  //
  // Only when the spool actually says something extra. A plain single-colour
  // spool keeps the flat `backgroundColor` it has always had, so the common
  // case is untouched and the gradient machinery cannot regress it.
  const assignedSwatch = inventory?.assignedSpool ?? null;
  const swatchStops = parseStops(assignedSwatch?.extra_colors);
  const hasSwatchEffect = Boolean(assignedSwatch?.effect_type);
  // Any stop at all, not just two: `buildColorLayer` ignores `rgba` the moment
  // stops exist, so a one-stop spool renders that stop's colour and not the
  // slot hex. Honouring it here is what keeps this header agreeing with the
  // Inventory swatch, which is the whole point of sharing the builder.
  const useSpoolSwatch = Boolean(assignedSwatch) && (swatchStops.length > 0 || hasSwatchEffect);
  // Built from the spool end to end when used. Mixing the spool's stops over
  // the slot's base hex would render a gradient the user never configured if
  // the two ever disagreed.
  const spoolSwatchStyle = useSpoolSwatch
    ? buildFilamentBackground({
        effectSize: 'card',
        rgba: assignedSwatch?.rgba ?? colorHex,
        extraColors: assignedSwatch?.extra_colors,
        effectType: assignedSwatch?.effect_type,
        subtype: assignedSwatch?.subtype,
      })
    : null;
  // A single hex cannot decide legibility across several bands, and the header
  // label sits dead centre where a multi-stop background is most likely to
  // change under it. So a genuinely multi-band swatch puts the name on the same
  // scrim the vendor badge already uses rather than betting on one of the
  // stops. One stop, or an effect over one colour, leaves the base colour
  // intact -- the contrast test still has a real answer there, and scrimming
  // every effect spool would put a black pill on cards that never needed one.
  const swatchNeedsScrim = swatchStops.length > 1;
  // Which colour the contrast test should actually run against. Not always the
  // slot hex any more: once the spool's swatch is painted, a single stop
  // replaces the base entirely, and an effect-only spool paints the spool's own
  // rgba rather than the slot's. Testing the slot hex in either case would pick
  // the text colour for a background that is no longer on screen.
  const contrastBaseHex = useSpoolSwatch
    ? (swatchStops.length === 1 ? swatchStops[0] : assignedSwatch?.rgba ?? colorHex)
    : colorHex;

  return (
    <div
      ref={triggerRef}
      data-testid="filament-slot"
      className={`relative ${className}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {/* Portaled hover card — rendered into document.body so it escapes
          any ancestor stacking context. Sibling printer cards on the
          dashboard create their own stacking contexts; without the portal
          the popover gets covered by the next card even at z-[60]
          (#1336 follow-up). */}
      {isVisible && createPortal(
        <div
          ref={cardRef}
          className="fixed z-[60]"
          style={{
            top: coords?.top ?? -9999,
            left: coords?.left ?? -9999,
            maxWidth: 'calc(100vw - 24px)',
            // Hide until coords are computed to avoid a (-9999,-9999) flash.
            visibility: coords ? 'visible' : 'hidden',
          }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          {/* Card container */}
          <div className="
            w-52 bg-bambu-dark-secondary border border-bambu-dark-tertiary
            rounded-lg shadow-xl overflow-hidden
            backdrop-blur-sm
          ">
            {/* Color swatch header - the hero element */}
            <div
              className="h-12 relative overflow-hidden"
              style={
                spoolSwatchStyle
                  ? { ...spoolSwatchStyle, backgroundColor: colorHex || '#3d3d3d' }
                  : { backgroundColor: colorHex || '#3d3d3d' }
              }
            >
              {/* Subtle gradient overlay for depth */}
              <div className="absolute inset-0 bg-gradient-to-b from-white/10 to-transparent" />

              {/* Color name on swatch */}
              <div className="absolute inset-0 flex items-center justify-center">
                <span
                  className={
                    swatchNeedsScrim
                      ? 'px-2 py-0.5 rounded bg-black/60 text-white font-semibold text-sm tracking-wide'
                      : `font-semibold text-sm tracking-wide ${
                          isLightColor(contrastBaseHex) ? 'text-black/80' : 'text-white/90'
                        }`
                  }
                >
                  {displayColorName}
                </span>
              </div>

              {/* Vendor badge - solid background for visibility on any color */}
              <div className={`
                absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider
                ${data.vendor === 'Bambu Lab'
                  ? 'bg-black/60 text-white'
                  : 'bg-black/50 text-white/90'}
              `}>
                {data.vendor === 'Bambu Lab' ? 'BBL' : 'GEN'}
              </div>
            </div>

            {/* Details section */}
            <div className="p-3 space-y-2.5">
              {/* Profile name */}
              <div className="flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                  {t('ams.profile')}
                </span>
                <span className="text-xs text-white font-semibold truncate max-w-[120px]">
                  {data.profile}
                </span>
              </div>

              {/* K Factor */}
              <div className="flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                  {t('ams.kFactor')}
                </span>
                <span className="text-xs text-bambu-green font-mono font-bold">
                  {data.kFactor}
                </span>
              </div>

              {/* Fill Level */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium flex items-center gap-1">
                    <Droplets className="w-3 h-3" />
                    {t('ams.fill')}
                  </span>
                  <span className="text-xs text-white font-semibold flex items-center gap-1">
                    <span>{data.fillLevel !== null ? `${data.fillLevel}%` : '—'}</span>
                    {assignedRemainingWeight !== null && data.fillLevel !== null && (
                      <span className="text-[9px] text-bambu-gray font-normal">• {assignedRemainingWeight}g</span>
                    )}
                  </span>
                </div>
                {/* Fill bar */}
                <div className="h-1.5 bg-black/40 rounded-full overflow-hidden">
                  {data.fillLevel !== null ? (
                    <div
                      className="h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${data.fillLevel}%`,
                        backgroundColor: getFillColor(data.fillLevel),
                      }}
                    />
                  ) : (
                    <div className="h-full w-full bg-bambu-gray/30 rounded-full" />
                  )}
                </div>
              </div>

              {/* Spoolman section - only show if enabled */}
              {spoolman?.enabled && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary space-y-2">
                  {/* Tray UUID with copy button */}
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('spoolman.spoolId')}
                    </span>
                    {data.trayUuid ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCopyUuid();
                        }}
                        className="flex items-center gap-1 text-xs text-bambu-gray hover:text-white transition-colors"
                        title="Copy spool UUID"
                      >
                        <span className="font-mono text-[10px] truncate max-w-[80px]">
                          {data.trayUuid.slice(0, 8)}...
                        </span>
                        {copied ? (
                          <Check className="w-3 h-3 text-bambu-green" />
                        ) : (
                          <Copy className="w-3 h-3" />
                        )}
                      </button>
                    ) : (
                      <span className="text-[10px] text-bambu-gray">—</span>
                    )}
                  </div>

                  {/* Open in inventory button (when already linked to a Spoolman spool) */}
                  {spoolman.linkedSpoolId && (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          dismiss();
                          navigate(`/inventory?spool=${spoolman.linkedSpoolId}`);
                        }}
                        className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-green/20 hover:bg-bambu-green/40 text-bambu-green"
                        title={t('inventory.openInInventory')}
                      >
                        <Package className="w-3.5 h-3.5" />
                        {t('inventory.openInInventory')}
                      </button>

                    </>
                  )}

                  {/* Link/Unlink action buttons intentionally NOT rendered
                      here. The inventory section below already provides
                      Assign/Unassign for slot-binding (the primary user
                      flow in Spoolman mode). Showing the spoolman tag-link
                      buttons in addition surfaced two red Unlink-icon
                      buttons for what users perceive as the same action,
                      regardless of whether the labels said "Unlink Spool"
                      vs "Unassign Spool". Tag-linking remains available
                      via dedicated UI (LinkSpoolModal can be opened from
                      Spoolman settings / inventory page). */}
                </div>
              )}

              {/* Inventory section — shown for every vendor including
                  Bambu Lab (#1133). The earlier "non-Bambu only" gate
                  prevented users from manually assigning a Bambu spool
                  in inventory to an AMS slot when they didn't want to
                  re-scan via SpoolBuddy NFC. */}
              {inventory && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary space-y-2">
                  {inventory.assignedSpool ? (
                    <>
                      <div className="flex items-center gap-1.5">
                        <Package className="w-3 h-3 text-bambu-green" />
                        <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                          {t('inventory.assigned')}
                        </span>
                      </div>
                      <div className="flex items-baseline gap-1.5 min-w-0 mb-1">
                        <p className="text-xs text-white truncate">
                          {inventory.assignedSpool.brand ? `${inventory.assignedSpool.brand} ` : ''}
                          {inventory.assignedSpool.material}
                          {inventory.assignedSpool.subtype ? ` ${inventory.assignedSpool.subtype}` : ''}
                          {inventory.assignedSpool.color_name ? ` - ${inventory.assignedSpool.color_name}` : ''}
                        </p>
                        <span className="text-[10px] font-mono text-bambu-gray shrink-0">#{inventory.assignedSpool.id}</span>
                      </div>
                      {(!spoolman?.linkedSpoolId || inventory.assignedSpool!.id !== spoolman.linkedSpoolId) && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            dismiss();
                            navigate(`/inventory?spool=${inventory.assignedSpool!.id}`);
                          }}
                          className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-green/20 hover:bg-bambu-green/40 text-bambu-green"
                          title={t('inventory.openInInventory')}
                        >
                          <Package className="w-3.5 h-3.5" />
                          {t('inventory.openInInventory')}
                        </button>
                      )}
                      {inventory.onUnassignSpool && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            dismiss();
                            inventory.onUnassignSpool?.();
                          }}
                          className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-red-100 dark:bg-red-500/20 hover:bg-red-200 dark:hover:bg-red-500/40 text-red-700 dark:text-red-400"
                        >
                          <Unlink className="w-3.5 h-3.5" />
                          {t('inventory.unassignSpool')}
                        </button>
                      )}
                    </>
                  ) : inventory.onAssignSpool ? (
                    <button
                      onClick={inventory.isAssigned ? undefined : (e) => {
                        e.stopPropagation();
                        dismiss();
                        inventory.onAssignSpool?.();
                      }}
                      disabled={!!inventory.isAssigned}
                      className={`w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 text-bambu-blue ${
                        inventory.isAssigned ? 'opacity-50 cursor-not-allowed' : 'hover:bg-bambu-blue/40'
                      }`}
                    >
                      <Package className="w-3.5 h-3.5" />
                      {t('inventory.assignSpool')}
                    </button>
                  ) : null}
                </div>
              )}

              {/* Configure slot section - always show if enabled */}
              {configureSlot?.enabled && (
                <div className={`${spoolman?.enabled && data.trayUuid ? '' : 'pt-2 mt-2 border-t border-bambu-dark-tertiary'}`}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      dismiss();
                      configureSlot.onConfigure?.();
                    }}
                    className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/40 text-bambu-blue"
                    title={t('ams.configureSlot')}
                  >
                    <Settings2 className="w-3.5 h-3.5" />
                    {t('ams.configure')}
                  </button>
                </div>
              )}
              {actions && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary space-y-1">
                  {actions}
                </div>
              )}
            </div>
          </div>

          {/* Arrow pointer */}
          <div
            className={`
              absolute left-1/2 -translate-x-1/2 w-0 h-0
              border-l-[6px] border-l-transparent
              border-r-[6px] border-r-transparent
              ${position === 'top'
                ? 'top-full border-t-[6px] border-t-bambu-dark-tertiary'
                : 'bottom-full border-b-[6px] border-b-bambu-dark-tertiary'}
            `}
          />
        </div>,
        document.body,
      )}

      {/* Unlink Confirmation Dialog */}
      {showUnlinkConfirm && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center" onClick={() => setShowUnlinkConfirm(false)}>
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
          <div
            className="relative bg-bambu-dark-secondary rounded-lg shadow-xl w-full max-w-sm mx-4 border border-bambu-dark-tertiary"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 space-y-4">
              <div className="space-y-2">
                <h3 className="text-base font-semibold text-white">
                  {t('spoolman.unlinkConfirmTitle')}
                </h3>
                <p className="text-sm text-bambu-gray">
                  {t('spoolman.unlinkConfirmMessage')}
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowUnlinkConfirm(false)}
                  className="flex-1 px-3 py-2 text-sm font-medium rounded transition-colors bg-bambu-dark hover:bg-bambu-dark-tertiary text-white"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={() => {
                    spoolman?.onUnlinkSpool?.();
                    setShowUnlinkConfirm(false);
                  }}
                  className="flex-1 px-3 py-2 text-sm font-medium rounded transition-colors bg-red-100 dark:bg-red-500/20 hover:bg-red-200 dark:hover:bg-red-500/40 text-red-700 dark:text-red-400"
                >
                  {t('inventory.unassignSpool')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface EmptySlotHoverCardProps {
  children: ReactNode;
  className?: string;
  configureSlot?: ConfigureSlotConfig;
  onAssignSpool?: () => void;
  actions?: ReactNode;
  // #1322 follow-up: distinguish firmware-confirmed empty (state 9/10) from
  // a user reset where the firmware still has a spool registered. "reset"
  // surfaces the user-cleared label; undefined / "physical" keeps the
  // historical "Empty slot" wording.
  kind?: 'physical' | 'reset';
}

export function EmptySlotHoverCard({ children, className = '', configureSlot, onAssignSpool, actions, kind }: EmptySlotHoverCardProps) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  // Screen-space coords for the portaled card — same pattern as
  // FilamentHoverCard, see comment there (#1336 follow-up).
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };

  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(false), 100);
  };

  // See FilamentHoverCard.dismiss — same z-[60]-over-a-z-50-dialog problem, and the
  // same missing mouseleave on touch (#2631).
  const dismiss = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setIsVisible(false);
  };

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  useLayoutEffect(() => {
    if (!isVisible) {
      setCoords(null);
      return;
    }
    const compute = () => {
      if (!triggerRef.current || !cardRef.current) return;
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      const cardWidth = cardRef.current.offsetWidth;
      const centerX = triggerRect.left + triggerRect.width / 2;
      const left = Math.max(8, Math.min(centerX - cardWidth / 2, window.innerWidth - cardWidth - 8));
      const top = triggerRect.top - cardHeight - 8;
      setCoords({ top, left });
    };
    compute();
    const rafId = requestAnimationFrame(compute);
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [isVisible]);

  return (
    <div
      ref={triggerRef}
      className={`relative ${className}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {isVisible && createPortal(
        <div
          ref={cardRef}
          className="fixed z-[60]"
          style={{
            top: coords?.top ?? -9999,
            left: coords?.left ?? -9999,
            visibility: coords ? 'visible' : 'hidden',
          }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <div className="
            bg-bambu-dark-secondary border border-bambu-dark-tertiary
            rounded-md shadow-lg overflow-hidden
          ">
            <div className="px-3 py-1.5 text-xs text-bambu-gray whitespace-nowrap">
              {kind === 'reset' ? t('ams.emptySlotReset') : t('ams.emptySlot')}
            </div>
            {/* Configure slot button */}
            {(configureSlot?.enabled || onAssignSpool || actions) && (
              <div className="px-2 pb-2 space-y-1">
                {/* Assign before Configure, matching the filled-slot card
                    above (#2791).  The two cards are separate render paths
                    and had drifted into opposite orders, so the menu
                    reshuffled itself depending on whether the slot happened
                    to hold filament. */}
                {onAssignSpool && (
                  <button
                    onClick={(e) => { e.stopPropagation(); dismiss(); onAssignSpool(); }}
                    className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/40 text-bambu-blue"
                  >
                    <Package className="w-3.5 h-3.5" />
                    {t('inventory.assignSpool')}
                  </button>
                )}
                {configureSlot?.enabled && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      dismiss();
                      configureSlot.onConfigure?.();
                    }}
                    className="w-full flex items-center justify-start gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/40 text-bambu-blue"
                    title={t('ams.configureSlot')}
                  >
                    <Settings2 className="w-3.5 h-3.5" />
                    {t('ams.configure')}
                  </button>
                )}
                {actions && (
                  <div className="pt-1 mt-1 border-t border-bambu-dark-tertiary space-y-1">
                    {actions}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="
            absolute left-1/2 -translate-x-1/2 top-full w-0 h-0
            border-l-[5px] border-l-transparent
            border-r-[5px] border-r-transparent
            border-t-[5px] border-t-bambu-dark-tertiary
          " />
        </div>,
        document.body,
      )}
    </div>
  );
}
