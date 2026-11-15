import { useState } from 'react';
import type { BuildPlate } from '../api/client';
import { baseTypeIcon, plateImage } from '../utils/buildPlates';

interface PlateImageProps {
  plate: Pick<BuildPlate, 'image' | 'base_type'>;
  className?: string;
  /** Used when the plate has neither a photo nor a known base-type icon. */
  fallbackSrc?: string;
}

/**
 * A build plate's photo (#1306), falling back to its base type's icon when the
 * photo is missing or fails to load — the seeded 3D Effect plates point at
 * photos that are supplied separately and may not be installed yet.
 */
export function PlateImage({ plate, className, fallbackSrc }: PlateImageProps) {
  const [failed, setFailed] = useState(false);
  const src = (failed ? baseTypeIcon(plate.base_type) : plateImage(plate)) ?? fallbackSrc ?? null;
  if (!src) return null;
  return (
    <img
      src={src}
      alt=""
      className={className}
      onError={() => {
        if (!failed) setFailed(true);
      }}
    />
  );
}
