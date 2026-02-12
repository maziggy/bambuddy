import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Droplets, Link2, Copy, Check, Settings2, ExternalLink, Package, Unlink } from 'lucide-react';

interface FilamentData {
  vendor: 'Bambu Lab' | 'Generic';
  profile: string;
  colorName: string;
  colorHex: string | null;
  kFactor: string;
  fillLevel: number | null; // null = unknown
  trayUuid?: string | null; // Bambu Lab spool UUID for Spoolman linking
  fillSource?: 'ams' | 'spoolman'; // Source of fill level data
}

interface SpoolmanConfig {
  enabled: boolean;
  onLinkSpool?: (trayUuid: string) => void;
  hasUnlinkedSpools?: boolean; // Whether there are spools available to link
  linkedSpoolId?: number | null; // Spoolman spool ID if this tray is already linked
  spoolmanUrl?: string | null; // Base URL for Spoolman (for "Open in Spoolman" link)
}

interface InventoryConfig {
  onAssignSpool?: () => void;
  onUnassignSpool?: () => void;
  assignedSpool?: { id: number; material: string; brand: string | null; color_name: string | null } | null;
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
}

/**
 * A hover card that displays filament details when hovering over AMS slots.
 * Replaces the basic browser tooltip with a styled popover.
 */
export function FilamentHoverCard({ data, children, disabled, className = '', spoolman, inventory, configureSlot }: FilamentHoverCardProps) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  const [copied, setCopied] = useState(false);
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

  // Calculate position when showing
  useEffect(() => {
    if (isVisible && triggerRef.current && cardRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      // Account for fixed header (56px) - space above should exclude header area
      const headerHeight = 56;
      const spaceAbove = triggerRect.top - headerHeight;
      const spaceBelow = window.innerHeight - triggerRect.bottom;

      // Prefer top, but flip to bottom if not enough space (accounting for header)
      if (spaceAbove < cardHeight + 12 && spaceBelow > spaceAbove) {
        setPosition('bottom');
      } else {
        setPosition('top');
      }
    }
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

  // Determine if color is light (for text contrast on swatch)
  const isLightColor = (hex: string | null): boolean => {
    if (!hex) return false;
    const cleanHex = hex.replace('#', '');
    const r = parseInt(cleanHex.slice(0, 2), 16);
    const g = parseInt(cleanHex.slice(2, 4), 16);
    const b = parseInt(cleanHex.slice(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.6;
  };

  const colorHex = data.colorHex ? `#${data.colorHex.replace('#', '')}` : null;

  return (
    <div
      ref={triggerRef}
      className={`relative ${className}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {/* Hover Card */}
      {isVisible && (
        <div
          ref={cardRef}
          className={`
            absolute left-1/2 -translate-x-1/2 z-50
            ${position === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            animate-in fade-in-0 zoom-in-95 duration-150
          `}
          style={{
            // Ensure card doesn't go off-screen horizontally
            maxWidth: 'calc(100vw - 24px)',
          }}
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
              style={{
                backgroundColor: colorHex || '#3d3d3d',
              }}
            >
              {/* Subtle gradient overlay for depth */}
              <div className="absolute inset-0 bg-gradient-to-b from-white/10 to-transparent" />

              {/* Color name on swatch */}
              <div className={`
                absolute inset-0 flex items-center justify-center
                font-semibold text-sm tracking-wide
                ${isLightColor(colorHex) ? 'text-black/80' : 'text-white/90'}
              `}>
                {data.colorName}
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
                    {data.fillLevel !== null ? `${data.fillLevel}%` : '—'}
                    {data.fillSource === 'spoolman' && data.fillLevel !== null && (
                      <span className="text-[9px] text-bambu-gray font-normal">{t('spoolman.fillSourceLabel')}</span>
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
              {spoolman?.enabled && data.trayUuid && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary space-y-2">
                  {/* Tray UUID with copy button */}
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('spoolman.spoolId')}
                    </span>
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
                  </div>

                  {/* Open in Spoolman button (when already linked) */}
                  {spoolman.linkedSpoolId && spoolman.spoolmanUrl && (
                    <a
                      href={`${spoolman.spoolmanUrl.replace(/\/$/, '')}/spool/show/${spoolman.linkedSpoolId}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-green/20 hover:bg-bambu-green/30 text-bambu-green"
                      title={t('spoolman.openInSpoolman')}
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                      {t('spoolman.openInSpoolman')}
                    </a>
                  )}

                  {/* Link Spool button (when not linked) */}
                  {!spoolman.linkedSpoolId && spoolman.onLinkSpool && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (spoolman.hasUnlinkedSpools !== false) {
                          spoolman.onLinkSpool?.(data.trayUuid!);
                        }
                      }}
                      disabled={spoolman.hasUnlinkedSpools === false}
                      className={`w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors ${
                        spoolman.hasUnlinkedSpools === false
                          ? 'bg-bambu-gray/10 text-bambu-gray cursor-not-allowed'
                          : 'bg-bambu-green/20 hover:bg-bambu-green/30 text-bambu-green'
                      }`}
                      title={spoolman.hasUnlinkedSpools === false ? t('spoolman.noUnlinkedSpools') : t('spoolman.linkToSpoolman')}
                    >
                      <Link2 className="w-3.5 h-3.5" />
                      {t('spoolman.linkToSpoolman')}
                    </button>
                  )}
                </div>
              )}

              {/* Inventory section - assign/unassign for non-Bambu spools */}
              {inventory && (data.vendor !== 'Bambu Lab' || !data.trayUuid) && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary space-y-2">
                  {inventory.assignedSpool ? (
                    <>
                      <div className="flex items-center gap-1.5">
                        <Package className="w-3 h-3 text-bambu-green" />
                        <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                          {t('inventory.assigned')}
                        </span>
                      </div>
                      <p className="text-xs text-white truncate">
                        {inventory.assignedSpool.brand ? `${inventory.assignedSpool.brand} ` : ''}
                        {inventory.assignedSpool.material}
                        {inventory.assignedSpool.color_name ? ` - ${inventory.assignedSpool.color_name}` : ''}
                      </p>
                      {inventory.onUnassignSpool && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            inventory.onUnassignSpool?.();
                          }}
                          className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-red-500/20 hover:bg-red-500/30 text-red-400"
                        >
                          <Unlink className="w-3.5 h-3.5" />
                          {t('inventory.unassignSpool')}
                        </button>
                      )}
                    </>
                  ) : inventory.onAssignSpool ? (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        inventory.onAssignSpool?.();
                      }}
                      className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/30 text-bambu-blue"
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
                      configureSlot.onConfigure?.();
                    }}
                    className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/30 text-bambu-blue"
                    title={t('ams.configureSlot')}
                  >
                    <Settings2 className="w-3.5 h-3.5" />
                    {t('ams.configure')}
                  </button>
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
        </div>
      )}
    </div>
  );
}

interface EmptySlotHoverCardProps {
  children: ReactNode;
  className?: string;
  configureSlot?: ConfigureSlotConfig;
}

/**
 * Wrapper for empty slots - shows "Empty" on hover with optional configure button
 */
export function EmptySlotHoverCard({ children, className = '', configureSlot }: EmptySlotHoverCardProps) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };

  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(false), 100);
  };

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return (
    <div
      className={`relative ${className}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {isVisible && (
        <div className="
          absolute left-1/2 -translate-x-1/2 bottom-full mb-2 z-50
          animate-in fade-in-0 zoom-in-95 duration-150
        ">
          <div className="
            bg-bambu-dark-secondary border border-bambu-dark-tertiary
            rounded-md shadow-lg overflow-hidden
          ">
            <div className="px-3 py-1.5 text-xs text-bambu-gray whitespace-nowrap">
              {t('ams.emptySlot')}
            </div>
            {/* Configure slot button */}
            {configureSlot?.enabled && (
              <div className="px-2 pb-2">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    configureSlot.onConfigure?.();
                  }}
                  className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded transition-colors bg-bambu-blue/20 hover:bg-bambu-blue/30 text-bambu-blue"
                  title={t('ams.configureSlot')}
                >
                  <Settings2 className="w-3.5 h-3.5" />
                  {t('ams.configure')}
                </button>
              </div>
            )}
          </div>
          <div className="
            absolute left-1/2 -translate-x-1/2 top-full w-0 h-0
            border-l-[5px] border-l-transparent
            border-r-[5px] border-r-transparent
            border-t-[5px] border-t-bambu-dark-tertiary
          " />
        </div>
      )}
    </div>
  );
}
