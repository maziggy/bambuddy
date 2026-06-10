"""Re-slice BB mascot poses from the character sheet.

Produces the per-pose webp files consumed by MascotIcon (the onboarding tour).

Source:  screenshots/onboarding/bb_bambuddy.webp (RGB, no alpha, opaque bg)
Output:  frontend/public/img/bb_{hero,started,walk,almost,allset,help}.webp

Two things matter and must not regress:
  - the crop excludes the caption text under each pose, so the tour modal
    never shows a sliver of "Almost there!" under the character;
  - the background is keyed out to alpha so the mascot composites cleanly
    on the dark-theme tour card.

Pose coordinates are pixel offsets in the source sheet, derived once by
column/row density analysis. If the character sheet is re-exported with
different dimensions, re-derive them rather than nudging by eye.
"""

from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "screenshots/bb images/bb_bambuddy.webp"
OUT_DIR = REPO_ROOT / "frontend/public/img"

POSE_Y = (746, 924)
POSES = {
    "started": (35, 206),
    "walk": (251, 423),
    "almost": (455, 631),
    "allset": (657, 851),
    "help": (861, 1079),
}
HERO_BOX = (50, 71, 620, 650)

# Background ramp: pixels with min-channel >= BG_FULL go fully transparent,
# pixels with min-channel <= INK stay fully opaque, in between alpha ramps
# linearly so anti-aliased outlines keep their feathering.
BG_FULL = 228
INK = 200


def keyout_to_alpha(crop: Image.Image) -> Image.Image:
    arr = np.array(crop.convert("RGB"))
    min_ch = arr.min(axis=2).astype(np.int32)
    alpha = np.clip((BG_FULL - min_ch) * 255 // (BG_FULL - INK), 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([arr, alpha]), mode="RGBA")


def save_webp_lossless(im: Image.Image, path: Path) -> None:
    im.save(path, "WEBP", lossless=True, quality=100, method=6)


def main() -> None:
    src = Image.open(SRC)
    for name, (x0, x1) in POSES.items():
        crop = src.crop((x0, POSE_Y[0], x1, POSE_Y[1]))
        out_path = OUT_DIR / f"bb_{name}.webp"
        save_webp_lossless(keyout_to_alpha(crop), out_path)
        print(f"  {out_path.name}  {crop.size}")

    hero_crop = src.crop(HERO_BOX)
    save_webp_lossless(keyout_to_alpha(hero_crop), OUT_DIR / "bb_hero.webp")
    print(f"  bb_hero.webp  {hero_crop.size}")


if __name__ == "__main__":
    main()
