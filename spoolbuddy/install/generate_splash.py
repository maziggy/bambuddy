#!/usr/bin/env python3
"""Generate a polished SpoolBuddy boot splash image (1024x600).

Uses the SpoolBuddy logo with baked-in glow, radial gradient background,
subtle light rays, and vignette effects for a premium kiosk boot screen.

Usage:
    python3 generate_splash.py [output_path]

Requires: Pillow (pip install Pillow)
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

# --- Configuration ---
WIDTH, HEIGHT = 1024, 600
BG_CENTER = (55, 55, 55)  # Notably lighter center
BG_EDGE = (2, 2, 2)  # Nearly black edges
ACCENT = (0, 200, 75)  # Brighter SpoolBuddy green
ACCENT_GLOW = (0, 255, 100)  # Vivid glow core
LOGO_SCALE = 0.50  # Scale logo to 50% of canvas width
GLOW_RADIUS = 160  # Very wide glow spread
VIGNETTE_STRENGTH = 0.85  # Heavy edge darkening
RAY_COUNT = 32  # More radial light rays
RAY_OPACITY = 50  # Clearly visible rays (0-255)


def radial_gradient(size, center_color, edge_color):
    """Create a radial gradient from center to edges."""
    w, h = size
    img = Image.new("RGB", size)
    pixels = img.load()
    cx, cy = w // 2, h // 2
    max_dist = math.sqrt(cx**2 + cy**2)

    for y in range(h):
        for x in range(w):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            t = min(dist / max_dist, 1.0)
            # Ease-out curve for smoother falloff
            t = t * t
            r = int(center_color[0] + (edge_color[0] - center_color[0]) * t)
            g = int(center_color[1] + (edge_color[1] - center_color[1]) * t)
            b = int(center_color[2] + (edge_color[2] - center_color[2]) * t)
            pixels[x, y] = (r, g, b)

    return img


def create_light_rays(size, num_rays, opacity):
    """Create subtle radial light rays emanating from center."""
    w, h = size
    rays = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(rays)
    cx, cy = w // 2, h // 2
    max_radius = int(math.sqrt(cx**2 + cy**2)) + 50

    for i in range(num_rays):
        angle = (2 * math.pi * i) / num_rays
        # Vary ray width slightly for organic feel
        half_width = math.radians(1.5 + (i % 3) * 0.5)

        a1 = angle - half_width
        a2 = angle + half_width

        points = [
            (cx, cy),
            (cx + int(max_radius * math.cos(a1)), cy + int(max_radius * math.sin(a1))),
            (cx + int(max_radius * math.cos(a2)), cy + int(max_radius * math.sin(a2))),
        ]
        # Green-tinted rays
        draw.polygon(points, fill=(ACCENT[0], ACCENT[1], ACCENT[2], opacity))

    # Heavy blur to make rays soft and diffuse
    rays = rays.filter(ImageFilter.GaussianBlur(radius=30))
    return rays


def create_vignette(size, strength):
    """Create a vignette (edge darkening) mask."""
    w, h = size
    vignette = Image.new("L", size, 255)
    pixels = vignette.load()
    cx, cy = w / 2, h / 2
    max_dist = math.sqrt(cx**2 + cy**2)

    for y in range(h):
        for x in range(w):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            t = dist / max_dist
            # Ramp darkening from ~40% radius outward
            fade = max(0, (t - 0.4) / 0.6)
            fade = fade * fade  # Quadratic ease
            val = int(255 * (1 - fade * strength))
            pixels[x, y] = max(0, val)

    return vignette


def create_glow(logo_img, color, radius, intensity=1.5):
    """Create a colored glow effect from a logo's alpha channel."""
    # Extract alpha as the glow shape
    if logo_img.mode != "RGBA":
        return Image.new("RGBA", logo_img.size, (0, 0, 0, 0))

    alpha = logo_img.split()[3]

    # Create colored version of the alpha shape
    glow = Image.new("RGBA", logo_img.size, (0, 0, 0, 0))
    glow_pixels = glow.load()
    alpha_pixels = alpha.load()

    for y in range(logo_img.height):
        for x in range(logo_img.width):
            a = alpha_pixels[x, y]
            if a > 0:
                boosted = min(255, int(a * intensity))
                glow_pixels[x, y] = (color[0], color[1], color[2], boosted)

    # Blur to create the glow spread
    glow = glow.filter(ImageFilter.GaussianBlur(radius=radius))
    return glow


def generate_splash(output_path):
    """Generate the final splash image."""
    print(f"Generating {WIDTH}x{HEIGHT} splash image...")

    # 1. Radial gradient background
    print("  Creating radial gradient background...")
    canvas = radial_gradient((WIDTH, HEIGHT), BG_CENTER, BG_EDGE)

    # 2. Light rays
    print("  Adding light rays...")
    rays = create_light_rays((WIDTH, HEIGHT), RAY_COUNT, RAY_OPACITY)
    canvas.paste(
        Image.alpha_composite(
            Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)),
            rays,
        ),
        (0, 0),
        rays,
    )

    # 3. Load and scale logo
    print("  Loading SpoolBuddy logo...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logo_paths = [
        os.path.join(script_dir, "..", "..", "frontend", "public", "spoolbuddy_logo_dark.png"),
        os.path.join(script_dir, "..", "..", "frontend", "public", "img", "spoolbuddy_logo_dark.png"),
    ]

    logo = None
    for p in logo_paths:
        resolved = os.path.normpath(p)
        if os.path.exists(resolved):
            logo = Image.open(resolved).convert("RGBA")
            print(f"  Loaded logo from {resolved}")
            break

    if logo is None:
        print("  ERROR: Could not find spoolbuddy_logo_dark.png")
        sys.exit(1)

    # Scale logo to target width
    target_w = int(WIDTH * LOGO_SCALE)
    scale = target_w / logo.width
    target_h = int(logo.height * scale)
    logo = logo.resize((target_w, target_h), Image.LANCZOS)

    # Center position (shift up slightly for visual balance)
    logo_x = (WIDTH - target_w) // 2
    logo_y = (HEIGHT - target_h) // 2 - 10

    # 4. Glow behind logo (two layers: wide diffuse + tight bright)
    print("  Rendering glow effects...")

    # Wide diffuse glow — very prominent
    glow_wide = create_glow(logo, ACCENT, radius=GLOW_RADIUS, intensity=3.0)
    glow_canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_canvas.paste(glow_wide, (logo_x, logo_y), glow_wide)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow_canvas)

    # Medium glow layer for extra punch
    glow_mid = create_glow(logo, ACCENT_GLOW, radius=GLOW_RADIUS // 2, intensity=2.5)
    glow_canvas_mid = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_canvas_mid.paste(glow_mid, (logo_x, logo_y), glow_mid)
    canvas = Image.alpha_composite(canvas, glow_canvas_mid)

    # Tighter bright glow core
    glow_tight = create_glow(logo, ACCENT_GLOW, radius=GLOW_RADIUS // 4, intensity=2.0)
    glow_canvas2 = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_canvas2.paste(glow_tight, (logo_x, logo_y), glow_tight)
    canvas = Image.alpha_composite(canvas, glow_canvas2)

    # 5. Composite logo on top
    print("  Compositing logo...")
    canvas.paste(logo, (logo_x, logo_y), logo)

    # 6. Apply vignette
    print("  Applying vignette...")
    vignette = create_vignette((WIDTH, HEIGHT), VIGNETTE_STRENGTH)
    canvas_rgb = canvas.convert("RGB")

    # Multiply canvas by vignette mask
    r, g, b = canvas_rgb.split()
    r = Image.composite(r, Image.new("L", (WIDTH, HEIGHT), 0), vignette)
    g = Image.composite(g, Image.new("L", (WIDTH, HEIGHT), 0), vignette)
    b = Image.composite(b, Image.new("L", (WIDTH, HEIGHT), 0), vignette)
    canvas = Image.merge("RGB", (r, g, b))

    # 7. Save
    canvas.save(output_path, "PNG", optimize=True)
    file_size = os.path.getsize(output_path) / 1024
    print(f"  Saved to {output_path} ({file_size:.0f} KB)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "splash.png")
    generate_splash(out)
