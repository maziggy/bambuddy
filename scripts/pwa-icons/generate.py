#!/usr/bin/env python3
"""Regenerate Bambuddy's PWA / home-screen icons from the original logo artwork.

The logo (frontend/public/img/favicon.png, 512x512) is placed unchanged on a
dark tile with padding; it is only ever scaled down, never redrawn.

Writes the PNGs into frontend/public/img by rendering SVG in a headless
Chromium-based browser (Chrome, Chromium or Edge).

Usage: python3 scripts/pwa-icons/generate.py [path-to-chrome]
"""

import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT_DIR = ROOT / "frontend/public/img"

BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
]

LOGO = ROOT / "frontend/public/img/favicon.png"
# Centre of the logo's visible artwork within its 512x512 canvas.
MARK_CX, MARK_CY = 256, 254

BACKGROUND = """
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3a3d3b"/>
      <stop offset="1" stop-color="#1b1d1c"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.38" r="0.62">
      <stop offset="0" stop-color="#00ae42" stop-opacity="0.18"/>
      <stop offset="1" stop-color="#00ae42" stop-opacity="0"/>
    </radialGradient>
  </defs>
"""


def mark_body() -> str:
    data = base64.b64encode(LOGO.read_bytes()).decode()
    return f'<image width="512" height="512" href="data:image/png;base64,{data}"/>'


def icon_svg(size: int, mark_frac: float, rounded: bool) -> str:
    """mark_frac: width of the 512 mark relative to the icon edge."""
    scale = size * mark_frac / 512
    tx = size / 2 - MARK_CX * scale
    ty = size / 2 - MARK_CY * scale
    rx = size * 0.2237 if rounded else 0  # iOS-like squircle approximation
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
{BACKGROUND}
  <rect width="{size}" height="{size}" rx="{rx}" fill="url(#bg)"/>
  <rect width="{size}" height="{size}" rx="{rx}" fill="url(#glow)"/>
  <g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f})">{mark_body()}</g>
</svg>
"""


# name, size, mark fraction, rounded corners
ICONS = [
    # iOS masks the square itself; keep the mark well inside (~72%).
    ("apple-touch-icon.png", 180, 0.72, False),
    # purpose "any": rounded tile with transparent corners.
    ("android-chrome-192x192.png", 192, 0.70, True),
    ("android-chrome-512x512.png", 512, 0.70, True),
    # purpose "maskable": full-bleed, mark inside the 80% safe-zone circle.
    ("maskable-192x192.png", 192, 0.56, False),
    ("maskable-512x512.png", 512, 0.56, False),
]


def find_browser(arg: str | None) -> str:
    for cand in ([arg] if arg else []) + BROWSERS:
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return cand
    sys.exit("No Chromium-based browser found; pass its path as the first argument.")


def render(browser: str, svg: str, size: int, out: Path, tmp: Path) -> None:
    src = tmp / "icon.svg"
    src.write_text(svg)
    html = tmp / "icon.html"
    html.write_text(
        '<html><body style="margin:0;background:transparent">'
        f'<img src="icon.svg" width="{size}" height="{size}" style="display:block"></body></html>'
    )
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            f"--screenshot={out}",
            html.as_uri(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    browser = find_browser(sys.argv[1] if len(sys.argv) > 1 else None)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name, size, frac, rounded in ICONS:
            out = tmp / name
            render(browser, icon_svg(size, frac, rounded), size, out, tmp)
            shutil.copyfile(out, OUT_DIR / name)
            print(f"wrote {name} ({size}x{size})")


if __name__ == "__main__":
    main()
