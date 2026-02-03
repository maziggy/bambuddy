#!/usr/bin/env python3
"""
Create a professional network architecture diagram for Bambuddy Virtual Printer Proxy Mode.
Following the Signal Flow design philosophy.
"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# Canvas dimensions
WIDTH = 1400
HEIGHT = 700

# Colors - Signal Flow palette
BG_COLOR = (18, 18, 22)  # Near black
CONTAINER_BG = (28, 28, 35)  # Slightly lighter
CONTAINER_BORDER = (50, 50, 60)  # Subtle border
BAMBU_GREEN = (0, 174, 66)  # #00AE42
BAMBU_GREEN_DIM = (0, 120, 45)  # Dimmer green for accents
TEXT_PRIMARY = (240, 240, 245)  # Near white
TEXT_SECONDARY = (140, 140, 150)  # Gray
TEXT_LABEL = (100, 100, 110)  # Darker gray for small labels
INTERNET_COLOR = (80, 80, 95)  # Cloud color
TLS_BADGE_BG = (35, 55, 45)  # Dark green for TLS badges
LOCK_COLOR = BAMBU_GREEN

# Font paths
FONT_DIR = Path("/opt/claude/.claude/plugins/cache/anthropic-agent-skills/document-skills/f23222824449/skills/canvas-design/canvas-fonts")

def load_fonts():
    """Load fonts for the diagram."""
    fonts = {}
    try:
        fonts['title'] = ImageFont.truetype(str(FONT_DIR / "InstrumentSans-Bold.ttf"), 28)
        fonts['heading'] = ImageFont.truetype(str(FONT_DIR / "InstrumentSans-Bold.ttf"), 18)
        fonts['label'] = ImageFont.truetype(str(FONT_DIR / "InstrumentSans-Regular.ttf"), 14)
        fonts['small'] = ImageFont.truetype(str(FONT_DIR / "InstrumentSans-Regular.ttf"), 12)
        fonts['port'] = ImageFont.truetype(str(FONT_DIR / "JetBrainsMono-Bold.ttf"), 13)
        fonts['port_small'] = ImageFont.truetype(str(FONT_DIR / "JetBrainsMono-Regular.ttf"), 11)
        fonts['tls'] = ImageFont.truetype(str(FONT_DIR / "JetBrainsMono-Bold.ttf"), 10)
    except Exception as e:
        print(f"Font loading error: {e}")
        # Fallback to default
        fonts['title'] = ImageFont.load_default()
        fonts['heading'] = ImageFont.load_default()
        fonts['label'] = ImageFont.load_default()
        fonts['small'] = ImageFont.load_default()
        fonts['port'] = ImageFont.load_default()
        fonts['port_small'] = ImageFont.load_default()
        fonts['tls'] = ImageFont.load_default()
    return fonts

def draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy

    if fill:
        # Fill
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.ellipse([x1, y1, x1 + 2*radius, y1 + 2*radius], fill=fill)
        draw.ellipse([x2 - 2*radius, y1, x2, y1 + 2*radius], fill=fill)
        draw.ellipse([x1, y2 - 2*radius, x1 + 2*radius, y2], fill=fill)
        draw.ellipse([x2 - 2*radius, y2 - 2*radius, x2, y2], fill=fill)

    if outline:
        # Outline
        draw.arc([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=outline, width=width)
        draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=width)
        draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=width)

def draw_lock_icon(draw, x, y, size, color):
    """Draw a simple lock icon."""
    # Lock body
    body_w = size * 0.7
    body_h = size * 0.5
    body_x = x - body_w / 2
    body_y = y + size * 0.1
    draw_rounded_rect(draw, [body_x, body_y, body_x + body_w, body_y + body_h], 2, fill=color)

    # Lock shackle (arc)
    shackle_w = size * 0.45
    shackle_h = size * 0.4
    shackle_x = x - shackle_w / 2
    shackle_y = y - size * 0.25
    draw.arc([shackle_x, shackle_y, shackle_x + shackle_w, shackle_y + shackle_h],
             180, 360, fill=color, width=2)

def draw_computer_icon(draw, x, y, size, color):
    """Draw a simple computer/monitor icon."""
    # Monitor
    mon_w = size * 0.8
    mon_h = size * 0.55
    mon_x = x - mon_w / 2
    mon_y = y - size * 0.35
    draw_rounded_rect(draw, [mon_x, mon_y, mon_x + mon_w, mon_y + mon_h], 3, outline=color, width=2)

    # Screen inner
    inner_margin = 4
    draw_rounded_rect(draw, [mon_x + inner_margin, mon_y + inner_margin,
                             mon_x + mon_w - inner_margin, mon_y + mon_h - inner_margin],
                      2, fill=color)

    # Stand
    stand_w = size * 0.2
    stand_h = size * 0.15
    draw.rectangle([x - stand_w/2, mon_y + mon_h, x + stand_w/2, mon_y + mon_h + stand_h], fill=color)

    # Base
    base_w = size * 0.4
    draw.rectangle([x - base_w/2, mon_y + mon_h + stand_h, x + base_w/2, mon_y + mon_h + stand_h + 3], fill=color)

def draw_server_icon(draw, x, y, size, color):
    """Draw a simple server icon."""
    unit_h = size * 0.25
    gap = 4
    w = size * 0.75

    for i in range(3):
        uy = y - size * 0.4 + i * (unit_h + gap)
        draw_rounded_rect(draw, [x - w/2, uy, x + w/2, uy + unit_h], 3, outline=color, width=2)
        # LED dots
        draw.ellipse([x + w/2 - 12, uy + unit_h/2 - 2, x + w/2 - 8, uy + unit_h/2 + 2], fill=color)

def draw_printer_icon(draw, x, y, size, color):
    """Draw a Bambu Lab style 3D printer icon."""
    # Main body (cube-like)
    body_w = size * 0.75
    body_h = size * 0.7
    body_x = x - body_w / 2
    body_y = y - size * 0.35

    # Outer frame with thicker border
    draw_rounded_rect(draw, [body_x, body_y, body_x + body_w, body_y + body_h], 6, outline=color, width=2)

    # Inner window/chamber
    win_margin = 8
    draw_rounded_rect(draw, [body_x + win_margin, body_y + win_margin,
                             body_x + body_w - win_margin, body_y + body_h - 16],
                      4, outline=color, width=1)

    # Print bed line
    bed_y = body_y + body_h - 12
    draw.line([body_x + 12, bed_y, body_x + body_w - 12, bed_y], fill=color, width=2)

    # Extruder/toolhead
    ext_w = 16
    ext_h = 8
    ext_y = body_y + 18
    draw_rounded_rect(draw, [x - ext_w/2, ext_y, x + ext_w/2, ext_y + ext_h], 2, fill=color)

    # Small printed object on bed
    obj_w = 12
    obj_h = 10
    draw_rounded_rect(draw, [x - obj_w/2, bed_y - obj_h, x + obj_w/2, bed_y], 2, fill=color)

def draw_cloud_icon(draw, x, y, size, color):
    """Draw a simple cloud icon."""
    # Main cloud body using overlapping circles
    r1 = size * 0.25
    r2 = size * 0.2
    r3 = size * 0.18

    # Center circle
    draw.ellipse([x - r1, y - r1 * 0.8, x + r1, y + r1 * 0.8], fill=color)
    # Left circle
    draw.ellipse([x - r1 - r2 * 0.7, y - r2 * 0.3, x - r1 + r2 * 0.7, y + r2 * 1.1], fill=color)
    # Right circle
    draw.ellipse([x + r1 * 0.3 - r2 * 0.5, y - r2 * 0.4, x + r1 * 0.3 + r2 * 1.2, y + r2 * 1.0], fill=color)
    # Top circle
    draw.ellipse([x - r3 * 0.5, y - r1 - r3 * 0.3, x + r3 * 1.2, y - r1 + r3 * 0.9], fill=color)

def draw_arrow(draw, x1, y1, x2, y2, color, width=2):
    """Draw a line with arrow head."""
    draw.line([x1, y1, x2, y2], fill=color, width=width)

    # Arrow head
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    arrow_len = 10
    arrow_angle = math.pi / 6

    ax1 = x2 - arrow_len * math.cos(angle - arrow_angle)
    ay1 = y2 - arrow_len * math.sin(angle - arrow_angle)
    ax2 = x2 - arrow_len * math.cos(angle + arrow_angle)
    ay2 = y2 - arrow_len * math.sin(angle + arrow_angle)

    draw.polygon([(x2, y2), (ax1, ay1), (ax2, ay2)], fill=color)

def draw_bidirectional_arrow(draw, x1, y1, x2, y2, color, width=2):
    """Draw a bidirectional arrow."""
    import math

    # Shorten line slightly to make room for arrowheads
    angle = math.atan2(y2 - y1, x2 - x1)
    offset = 8

    lx1 = x1 + offset * math.cos(angle)
    ly1 = y1 + offset * math.sin(angle)
    lx2 = x2 - offset * math.cos(angle)
    ly2 = y2 - offset * math.sin(angle)

    draw.line([lx1, ly1, lx2, ly2], fill=color, width=width)

    # Arrow heads
    arrow_len = 8
    arrow_angle = math.pi / 6

    # Right arrow
    ax1 = x2 - arrow_len * math.cos(angle - arrow_angle)
    ay1 = y2 - arrow_len * math.sin(angle - arrow_angle)
    ax2 = x2 - arrow_len * math.cos(angle + arrow_angle)
    ay2 = y2 - arrow_len * math.sin(angle + arrow_angle)
    draw.polygon([(x2, y2), (ax1, ay1), (ax2, ay2)], fill=color)

    # Left arrow
    ax1 = x1 + arrow_len * math.cos(angle - arrow_angle)
    ay1 = y1 + arrow_len * math.sin(angle - arrow_angle)
    ax2 = x1 + arrow_len * math.cos(angle + arrow_angle)
    ay2 = y1 + arrow_len * math.sin(angle + arrow_angle)
    draw.polygon([(x1, y1), (ax1, ay1), (ax2, ay2)], fill=color)

def draw_tls_badge(draw, x, y, fonts, color=TLS_BADGE_BG, text_color=BAMBU_GREEN):
    """Draw a TLS badge."""
    badge_w = 42
    badge_h = 18
    draw_rounded_rect(draw, [x - badge_w/2, y - badge_h/2, x + badge_w/2, y + badge_h/2],
                      4, fill=color, outline=BAMBU_GREEN_DIM, width=1)

    # Lock icon
    draw_lock_icon(draw, x - 12, y - 2, 10, text_color)

    # TLS text
    draw.text((x + 2, y), "TLS", font=fonts['tls'], fill=text_color, anchor="lm")

def create_diagram():
    """Create the main diagram."""
    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    fonts = load_fonts()

    # Title
    title = "VIRTUAL PRINTER PROXY MODE"
    draw.text((WIDTH // 2, 35), title, font=fonts['title'], fill=BAMBU_GREEN, anchor="mm")

    # Subtitle
    subtitle = "Secure remote printing through Bambuddy"
    draw.text((WIDTH // 2, 62), subtitle, font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")

    # === LAYOUT ===
    # Three main sections: Remote | Internet | Local

    section_y = 320

    # Remote section (left)
    remote_x = 180
    remote_box = [40, 120, 320, 520]

    # Internet section (center)
    internet_x = 510

    # Bambuddy section (center-right)
    bambuddy_x = 700
    bambuddy_box = [560, 140, 840, 500]

    # Local section (right)
    local_x = 1050
    printer_x = 1220
    local_box = [920, 120, 1360, 520]

    # === REMOTE NETWORK ZONE ===
    draw_rounded_rect(draw, remote_box, 12, fill=CONTAINER_BG, outline=CONTAINER_BORDER, width=1)
    draw.text((180, 140), "REMOTE NETWORK", font=fonts['label'], fill=TEXT_LABEL, anchor="mm")

    # Slicer icon and label
    draw_computer_icon(draw, remote_x, section_y - 40, 70, BAMBU_GREEN)
    draw.text((remote_x, section_y + 30), "Bambu Studio", font=fonts['heading'], fill=TEXT_PRIMARY, anchor="mm")
    draw.text((remote_x, section_y + 52), "or OrcaSlicer", font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")

    # Ports on remote side
    draw.text((remote_x, section_y + 100), "Connects to Bambuddy", font=fonts['small'], fill=TEXT_LABEL, anchor="mm")
    draw.text((remote_x, section_y + 120), "FTP :9990  MQTT :8883", font=fonts['port_small'], fill=TEXT_SECONDARY, anchor="mm")

    # === INTERNET CLOUD ===
    draw_cloud_icon(draw, internet_x, section_y, 80, INTERNET_COLOR)
    draw.text((internet_x, section_y + 55), "Internet", font=fonts['label'], fill=TEXT_LABEL, anchor="mm")

    # === BAMBUDDY SERVER ===
    draw_rounded_rect(draw, bambuddy_box, 12, fill=CONTAINER_BG, outline=BAMBU_GREEN_DIM, width=2)
    draw.text((bambuddy_x, 165), "BAMBUDDY SERVER", font=fonts['label'], fill=BAMBU_GREEN, anchor="mm")

    # Server icon
    draw_server_icon(draw, bambuddy_x, section_y - 50, 70, BAMBU_GREEN)
    draw.text((bambuddy_x, section_y + 20), "TLS Proxy", font=fonts['heading'], fill=TEXT_PRIMARY, anchor="mm")

    # Incoming ports (left side of Bambuddy)
    draw.text((bambuddy_x, section_y + 70), "LISTEN PORTS", font=fonts['small'], fill=TEXT_LABEL, anchor="mm")
    draw_rounded_rect(draw, [bambuddy_x - 55, section_y + 85, bambuddy_x + 55, section_y + 130],
                      6, fill=(35, 35, 45), outline=CONTAINER_BORDER, width=1)
    draw.text((bambuddy_x, section_y + 98), "FTP", font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")
    draw.text((bambuddy_x, section_y + 115), "9990", font=fonts['port'], fill=BAMBU_GREEN, anchor="mm")

    draw_rounded_rect(draw, [bambuddy_x - 55, section_y + 140, bambuddy_x + 55, section_y + 185],
                      6, fill=(35, 35, 45), outline=CONTAINER_BORDER, width=1)
    draw.text((bambuddy_x, section_y + 153), "MQTT", font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")
    draw.text((bambuddy_x, section_y + 170), "8883", font=fonts['port'], fill=BAMBU_GREEN, anchor="mm")

    # === LOCAL NETWORK ZONE ===
    draw_rounded_rect(draw, local_box, 12, fill=CONTAINER_BG, outline=CONTAINER_BORDER, width=1)
    draw.text((1140, 140), "LOCAL NETWORK", font=fonts['label'], fill=TEXT_LABEL, anchor="mm")

    # "LAN Mode" badge
    draw_rounded_rect(draw, [1100, 155, 1180, 175], 4, fill=TLS_BADGE_BG, outline=BAMBU_GREEN_DIM, width=1)
    draw.text((1140, 165), "LAN Mode", font=fonts['tls'], fill=BAMBU_GREEN, anchor="mm")

    # Printer icon
    draw_printer_icon(draw, printer_x, section_y - 40, 80, BAMBU_GREEN)
    draw.text((printer_x, section_y + 35), "Bambu Lab", font=fonts['heading'], fill=TEXT_PRIMARY, anchor="mm")
    draw.text((printer_x, section_y + 55), "Printer", font=fonts['heading'], fill=TEXT_PRIMARY, anchor="mm")

    # Target ports
    draw.text((printer_x, section_y + 100), "Printer Ports", font=fonts['small'], fill=TEXT_LABEL, anchor="mm")
    draw.text((printer_x, section_y + 120), "FTP :990  MQTT :8883", font=fonts['port_small'], fill=TEXT_SECONDARY, anchor="mm")

    # Proxy target label
    draw_rounded_rect(draw, [local_x - 60, section_y - 80, local_x + 60, section_y - 50],
                      6, fill=(35, 35, 45), outline=CONTAINER_BORDER, width=1)
    draw.text((local_x, section_y - 65), "Target IP", font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")

    # === CONNECTION ARROWS ===

    # Remote to Internet
    draw_bidirectional_arrow(draw, 325, section_y, 460, section_y, BAMBU_GREEN_DIM, 2)

    # TLS badge between remote and internet
    draw_tls_badge(draw, 392, section_y - 20, fonts)

    # Internet to Bambuddy
    draw_bidirectional_arrow(draw, 555, section_y, 620, section_y, BAMBU_GREEN_DIM, 2)

    # Bambuddy to Local
    draw_bidirectional_arrow(draw, 780, section_y, 920, section_y, BAMBU_GREEN_DIM, 2)

    # TLS badge between Bambuddy and printer
    draw_tls_badge(draw, 850, section_y - 20, fonts)

    # Local network arrow to printer
    draw_bidirectional_arrow(draw, 990, section_y, 1130, section_y, BAMBU_GREEN_DIM, 2)

    # === BOTTOM INFO ===
    info_y = 560

    # Flow description
    draw.text((WIDTH // 2, info_y), "← Slicer traffic encrypted and relayed through Bambuddy to your printer →",
              font=fonts['small'], fill=TEXT_SECONDARY, anchor="mm")

    # Key features
    features_y = 600
    features = [
        "End-to-end TLS encryption",
        "No cloud dependency",
        "Uses printer's access code"
    ]

    spacing = 280
    start_x = WIDTH // 2 - spacing

    for i, feature in enumerate(features):
        fx = start_x + i * spacing
        # Bullet
        draw.ellipse([fx - 80, features_y - 3, fx - 74, features_y + 3], fill=BAMBU_GREEN)
        draw.text((fx - 68, features_y), feature, font=fonts['small'], fill=TEXT_SECONDARY, anchor="lm")

    # Bambuddy branding
    draw.text((WIDTH // 2, HEIGHT - 30), "bambuddy.cool", font=fonts['small'], fill=TEXT_LABEL, anchor="mm")

    return img

def main():
    """Generate and save the diagram."""
    img = create_diagram()

    output_path = Path("/opt/claude/projects/bambuddy/docs/images/proxy-mode-diagram.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img.save(output_path, "PNG", dpi=(150, 150))
    print(f"Diagram saved to: {output_path}")

    # Also save to frontend docs
    frontend_path = Path("/opt/claude/projects/bambuddy/frontend/docs/proxy-mode-diagram.png")
    frontend_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(frontend_path, "PNG", dpi=(150, 150))
    print(f"Also saved to: {frontend_path}")

if __name__ == "__main__":
    main()
