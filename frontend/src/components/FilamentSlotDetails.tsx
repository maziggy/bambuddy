import type { ReactNode } from 'react';
import { getFillBarColor } from '../utils/amsHelpers';
import { isLightColor } from '../utils/colors';
import { buildFilamentBackground } from './filamentSwatchHelpers';

interface FilamentSlotDetailsProps {
  label: string;
  color: string;
  material: string;
  colorName?: string | null;
  brand?: string | null;
  fill: number | null;
  empty: boolean;
  pending?: boolean;
  children?: ReactNode;
}

/** The detailed interior shared by regular AMS, AMS-HT and external slots. */
export function FilamentSlotDetails({
  label,
  color,
  material,
  colorName,
  brand,
  fill,
  empty,
  pending = false,
  children,
}: FilamentSlotDetailsProps) {
  // A blank printer slot can still have an assigned replacement spool. Its
  // identity is useful while configuration is pending, but its level is not.
  const isPending = empty && pending;
  const showIdentity = !empty || isPending;
  const cleanColor = color.replace(/^#/, '');
  const headerColor = showIdentity && /^(?:[0-9a-f]{6}|[0-9a-f]{8})$/i.test(cleanColor)
    ? cleanColor
    : null;
  const fillLevel = !empty && fill !== null && Number.isFinite(fill) && fill >= 0
    ? Math.min(100, Math.round(fill))
    : null;
  const headerBackground = headerColor
    ? buildFilamentBackground({ rgba: headerColor, effectSize: 'bar' })
    : undefined;
  // A translucent colour mixes with both checkerboard shades, so the RGB
  // colour alone cannot choose readable label text. Give that label its own
  // opaque background while leaving the configured swatch visible around it.
  const translucentHeader = headerColor?.length === 8 && parseInt(headerColor.slice(6, 8), 16) < 255;

  return (
    <div
      className="flex w-full min-w-0 flex-col gap-1 text-left"
      style={{ minHeight: 'calc(var(--pc-t10, 10px) * 10)' }}
    >
      <div
        data-pending-config={isPending ? 'true' : undefined}
        className={`relative flex h-[var(--pc-i4,1rem)] min-w-0 items-center justify-between gap-1 rounded-sm bg-black/20 px-1 ${isPending ? 'border-2 border-dashed border-amber-500' : ''}`}
        style={headerBackground}
      >
        <span
          className={`min-w-0 truncate text-[length:var(--pc-t10,10px)] font-medium leading-tight ${translucentHeader ? 'rounded-sm px-0.5' : ''}`}
          style={{
            color: !translucentHeader && headerColor && isLightColor(headerColor) ? '#000' : '#fff',
            backgroundColor: translucentHeader ? '#111827' : undefined,
          }}
          title={label}
        >
          {label}
        </span>
        {children && <div className="flex shrink-0 items-center gap-1">{children}</div>}
      </div>
      <div className="flex min-w-0 flex-1 flex-col px-0.5 text-[length:var(--pc-t10,10px)] leading-tight">
        <div className="truncate text-[length:var(--pc-t11,11px)] font-medium text-white" title={material}>
          {material}
        </div>
        {showIdentity && colorName && (
          <div className="line-clamp-2 break-words text-bambu-gray" title={colorName}>
            {colorName}
          </div>
        )}
        <div className="mt-auto truncate pt-1 text-bambu-gray" title={showIdentity ? brand || undefined : undefined}>
          {showIdentity ? brand : null}
        </div>
        <span className="self-end text-bambu-gray tabular-nums">
          {fillLevel === null ? '—' : `${fillLevel}%`}
        </span>
      </div>
      <div className="h-1 min-w-0 overflow-hidden rounded-full bg-black/30">
        {fillLevel !== null && (
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${fillLevel}%`, backgroundColor: getFillBarColor(fillLevel) }}
          />
        )}
      </div>
    </div>
  );
}
