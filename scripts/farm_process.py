#!/opt/bambuddy/venv/bin/python3
"""
farm_loop.py  --  Bambu P1S farm loop GCODE post-processor
===========================================================

End sequence based on FactorianDesigns Print Automation Custom Codes V2
(End_Code_Automatic_Pushoff_P1_V2.txt).

INPUT: always a .gcode.3mf exported from BambuStudio (plate export).

Two modes:

  NORMAL MODE  (default)
    Strips the stock end GCODE and replaces it with the farm loop
    end sequence (retract → safe pos → AMS retract → cooldown → push sweeps).
    Outputs a _farmed.gcode.3mf ready to send to the printer.

  TEST MODE  (--test)
    Validates push motion without printing. Strips everything after
    M204 S10000 in the machine start block, homes, dwells 10s,
    then runs the farm loop end sequence.
    Place a pre-printed object on the bed before starting.

USAGE
-----
  Normal:
    python farm_loop.py input.gcode.3mf [options]

  Test:
    python farm_loop.py input.gcode.3mf --test [options]

OPTIONS
-------
  -o / --output            Output filename
  --cooldown-temp INT      Bed release temp C              (default: 25)
  --push-speed INT         Center push feedrate mm/min     (default: 300)
  --push-x FLOAT           Push X centre mm               (default: auto)
  --lane-offset FLOAT      Left/right lane offset from X centre mm (default: 60)
  --test                   Test mode flag
  --test-bed-temp INT      [TEST] Bed preheat temp C       (default: auto from file)
  --test-nozzle-temp INT   [TEST] Nozzle preheat temp C    (default: 80)

SAFETY NOTES
------------
- Push coordinates are auto-detected from the print file header.
  Use --push-x to override if the part is off-centre.
- Doors must be removed for the push sweep to reach the front of the bed.
- Slice real jobs with a brim. The brim is what the pusher catches.
- Factorian's Z formula: max_z > 31mm → push at max_z-30mm, else Z=1mm.
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants -- P1S bed geometry
# ---------------------------------------------------------------------------
BED_X_CENTRE  = 128
BED_Y_BACK    = 251   # toolhead Y at start of each sweep
BED_Y_FRONT   = 25    # toolhead Y at end of each sweep (part falls off)


# ---------------------------------------------------------------------------
# Header / start-block parsing
# ---------------------------------------------------------------------------

def parse_header(lines: list) -> dict:
    """Extract key values from the BambuStudio GCODE header comments."""
    data = {
        "max_z_height": None,
        "nozzle_temp":  None,
        "print_x_min":  None,
        "print_x_max":  None,
    }
    patterns = {
        "max_z_height": r";\s*max_z_height\s*[:=]\s*([\d.]+)",
        "nozzle_temp":  r";\s*nozzle_temperature\s*[:=]\s*([\d]+)",
        "print_x_min":  r";\s*print_x_min\s*[:=]\s*([\d.]+)",
        "print_x_max":  r";\s*print_x_max\s*[:=]\s*([\d.]+)",
    }
    for line in lines[:600]:
        for key, pattern in patterns.items():
            if data[key] is None:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    data[key] = float(m.group(1))
    return data


def find_max_z_from_toolpath(lines: list):
    """Fallback: scan G1 Z moves and return the highest Z seen."""
    max_z = None
    z_pat = re.compile(r"G[01]\s[^;]*Z([\d.]+)", re.IGNORECASE)
    for line in lines:
        m = z_pat.search(line)
        if m:
            z = float(m.group(1))
            if max_z is None or z > max_z:
                max_z = z
    return max_z


def find_bed_temp_from_start(lines: list):
    """
    Scan the executable machine start block for the first M140 S{value}
    to get the actual bed temperature used by this file.
    Returns int or None.
    """
    in_exec = False
    pat = re.compile(r"M140\s+S(\d+)", re.IGNORECASE)
    for line in lines:
        if "; EXECUTABLE_BLOCK_START" in line:
            in_exec = True
        if in_exec and "; MACHINE_START_GCODE_END" in line:
            break
        if in_exec:
            m = pat.search(line)
            if m:
                return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# End-sequence generator (based on FactorianDesigns V2)
# ---------------------------------------------------------------------------

def build_end_sequence(
    max_z,
    cooldown_temp,
    push_x,
    push_speed=300,
    lane_offset=60,
    nozzle_target=0,
    flex_cycles=3,
    flex_z=204,
    flex_drop=20,
):
    """
    Generate the farm loop end GCODE block.

    Z push height formula (Factorian):
      max_z > 31mm  →  push at Z = max_z - 30
      max_z <= 31mm →  push at Z = 1

    Push lanes (Factorian):
      Center: push_x
      Right:  push_x + lane_offset
      Left:   push_x - lane_offset
    """
    # Factorian's Z formula
    if max_z > 31:
        push_z = round(max_z - 30, 2)
    else:
        push_z = 1.0

    # Clamp lanes to bed bounds
    cx = round(max(10.0, min(246.0, push_x)), 1)
    rx = round(max(10.0, min(246.0, push_x + lane_offset)), 1)
    lx = round(max(10.0, min(246.0, push_x - lane_offset)), 1)

    # Safe Z for initial raise (tiny amount above max_z, capped at 245)
    safe_z = round(min(max_z + 0.5, 245.0), 1)

    L = []
    a = L.append

    a("; ===== FARM LOOP END SEQUENCE =====")
    a("; Based on FactorianDesigns Print Automation Custom Codes V2")
    a("")

    # --- retract and move to safe/wipe position ---
    a("M400 ; wait for buffer to clear")
    a("G92 E0 ; zero the extruder")
    a("G1 E-0.8 F1800 ; retract")
    a("G1 Z{} F900 ; lower z a little".format(safe_z))
    a("G1 X65 Y245 F12000 ; move to safe pos")
    a("G1 Y265 F3000")
    a("")
    a("G1 X65 Y245 F12000")
    a("G1 Y265 F3000")
    a("M140 S0 ; turn off bed")
    a("M106 S0 ; turn off part fan")
    a("M106 P2 S0 ; turn off aux fan")
    a("M106 P3 S0 ; turn off chamber fan")
    a("")

    # --- AMS filament retract ---
    a("G1 X100 F12000 ; wipe")
    a("; pull back filament to AMS")
    a("M620 S255")
    a("G1 X20 Y50 F12000")
    a("G1 Y-3")
    a("T255")
    a("G1 X65 F12000")
    a("G1 Y265")
    a("G1 X100 F12000 ; wipe")
    a("M621 S255")
    a("M104 S{} ; turn off hotend".format(nozzle_target))
    a("")

    # --- timelapse end ---
    a("M622.1 S1 ; for prev firmware, default turned on")
    a("M1002 judge_flag timelapse_record_flag")
    a("M622 J1")
    a("    M400 ; wait all motion done")
    a("    M991 S0 P-1 ; end smooth timelapse at safe pos")
    a("    M400 S3 ; wait for last picture to be taken")
    a("M623 ; end of timelapse_record_flag")
    a("")
    a("M400 ; wait all motion done")
    a("M17 S ; save motor currents")
    a("")

    # --- cooldown ---
    # Cooldown Z: Factorian hardcodes Z=1 but that risks the print hitting the gantry
    # on taller parts. Use the same formula as push height so the bed only rises as
    # far as is safe: max_z > 31 -> max_z-30, else Z=1.
    cooldown_z = push_z  # already calculated above
    a("M400 ; wait all print moves done")
    a("M17 Z0.4 ; lower z motor current")
    a("G1 Z{} F600 ; raise bed for cooling (safe for print height {}mm)".format(cooldown_z, max_z))
    a("M400")
    a("M106 P2 S255 ; aux fan on for active cooling")
    a("M106 P3 S200 ; chamber fan on")
    a("")
    # Repeat M190 S40 times (Factorian's approach) -- each command has a ~90s firmware
    # timeout. Repeating extends total wait to ~60 min. Once bed reaches target,
    # remaining lines complete instantly.
    for i in range(40):
        a("M190 S{} ; wait for bed temp ({}/40)".format(cooldown_temp, i + 1))
    a("M140 S0 ; clear bed setpoint (M190 leaves it at cooldown_temp, not 0)")
    a("")
    a("M106 P2 S0 ; aux fan off")
    a("M106 P3 S0 ; chamber fan off")
    a("")

    # --- FarmLoop Stage 1 bed flex ---
    # The FarmLoop clip is at a fixed absolute Z position on the frame.
    # flex_z = Z where the clip engages the spring steel plate (P1S default: Z230,
    #          = 26mm up from bed floor at Z256, measured in 1mm jog clicks).
    # flex_drop = how many mm the bed drops PAST the clip each cycle (default: 10).
    # Toolhead parks at front-left (X10 Y15) over the clip while bed cycles.
    if flex_cycles > 0:
        flex_engage_z = flex_z          # clip engages here
        flex_bottom_z = round(flex_z + flex_drop, 2)  # full flex (bed lower)
        a("; --- FarmLoop Stage 1 bed flex ---")
        a("; Toolhead stays at X65 Y265 (cooldown park). Clip engages mechanically as bed drops.")
        a("G1 Z{} F600 ; approach clip engage position".format(flex_engage_z))
        a("M400")
        for i in range(flex_cycles):
            a("G1 Z{} F600 ; flex down {}/{} ({}mm past clip)".format(flex_bottom_z, i + 1, flex_cycles, flex_drop))
            a("G4 P500")
            a("G1 Z{} F600 ; flex up -- plate springs back".format(flex_engage_z))
            a("G4 P300")
        a("")

    # --- push off ---
    a(";=== Cool Down Done, Start Push Off ===")
    a("M400")
    if max_z > 31:
        a("G1 Z{} F600 ; push height = max_z({}) - 30mm".format(push_z, max_z))
    else:
        a("G1 Z{} F600 ; push height = Z1 (model <= 31mm)".format(push_z))
    a("M400 P100")
    a("")
    a("G1 X170 Y254 F600 ; move nozzle to side for safety")
    a("M400")
    a("")
    a("; center lane (X{})".format(cx))
    a("G1 X{} Y230 F1200 ; center push start position".format(cx))
    a("G1 X{} Y{} F{} ; push off center (slow)".format(cx, BED_Y_FRONT, push_speed))
    a("")
    a("; right lane (X{})".format(rx))
    a("G1 X{} Y200 F2000".format(cx))
    a("G1 X{} Y200 F2000".format(rx))
    a("G1 X{} Y{} F2000 ; push off right".format(rx, BED_Y_FRONT))
    a("")
    a("; left lane (X{})".format(lx))
    a("G1 X{} Y200 F2000".format(rx))
    a("G1 X{} Y200 F2000".format(lx))
    a("G1 X{} Y{} F2000 ; push off left".format(lx, BED_Y_FRONT))
    a("")

    # --- finalize ---
    a("M220 S100 ; reset feedrate magnitude")
    a("M201.2 K1.0 ; reset acc magnitude")
    a("M73.2 R1.0 ; reset left time magnitude")
    a("M1002 set_gcode_claim_speed_level : 0")
    a("")
    a("M17 X0.8 Y0.8 Z0.5 ; lower motor current to 45%")
    a("M400")
    a("M73 P100 R0")
    a("; ===== END FARM LOOP SEQUENCE =====")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Test GCODE builder
# ---------------------------------------------------------------------------

def build_test_gcode(lines, max_z, push_x, cooldown_temp, push_speed, lane_offset,
                     flex_cycles, flex_z, flex_drop, test_bed_temp, test_nozzle_temp):
    """
    Build a test GCODE file: keeps real machine start through M204 S10000,
    then homes, dwells 10s, runs end sequence.
    """
    # Find ; FEATURE: Custom that opens the machine start block
    feature_idx = None
    for i, line in enumerate(lines):
        if "; FEATURE: Custom" in line:
            feature_idx = i
            break
    if feature_idx is None:
        raise ValueError("Input file missing ; FEATURE: Custom")

    # Find M204 S10000 -- last safe init line before heatbed preheat
    safe_end_idx = None
    for i in range(feature_idx, min(feature_idx + 80, len(lines))):
        if "M204 S10000" in lines[i]:
            safe_end_idx = i
            break
    if safe_end_idx is None:
        raise ValueError("Could not find M204 S10000 in machine start block")

    kept = "".join(lines[:safe_end_idx + 1])

    end_seq = build_end_sequence(
        max_z=max_z,
        cooldown_temp=cooldown_temp,
        push_x=push_x,
        push_speed=push_speed,
        lane_offset=lane_offset,
        nozzle_target=test_nozzle_temp,
        flex_cycles=flex_cycles,
        flex_z=flex_z,
        flex_drop=flex_drop,
    )

    injected = (
        "; --- FARM LOOP TEST MODE ---\n"
        "; Place the already-printed object on the bed before starting.\n"
        "M104 S{noz} ; set nozzle (no wait)\n"
        "M140 S{bed} ; set bed (no wait)\n"
        "G28         ; home all axes\n"
        "G4 P10000   ; 10s dwell -- place object now if not already\n"
        "; MACHINE_START_GCODE_END\n"
        "\n"
        "; FEATURE: Custom\n"
        "; MACHINE_END_GCODE_START\n"
    ).format(bed=test_bed_temp, noz=test_nozzle_temp)

    injected += end_seq + "; EXECUTABLE_BLOCK_END\n"

    return kept + injected


# ---------------------------------------------------------------------------
# 3MF packaging
# ---------------------------------------------------------------------------

def write_3mf(output_path, gcode_bytes, source_3mf_path, plate_id=1):
    """
    Repack a .gcode.3mf, replacing only the gcode and its MD5.
    All other files are copied verbatim from the source with their
    original compression intact, preserving file order and structure.
    Required: Bambu firmware parser is strict about zip internals.
    plate_id: which plate's gcode to replace (1-based, matches Bambu plate numbering).
    """
    if isinstance(gcode_bytes, str):
        gcode_bytes = gcode_bytes.encode("utf-8")
    # Normalise to LF -- firmware rejects CRLF
    gcode_bytes = gcode_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    md5 = hashlib.md5(gcode_bytes).hexdigest().upper().encode("ascii")

    REPLACE = {
        "Metadata/plate_{}.gcode".format(plate_id):     (gcode_bytes,  zipfile.ZIP_DEFLATED),
        "Metadata/plate_{}.gcode.md5".format(plate_id): (md5,          zipfile.ZIP_DEFLATED),
    }

    with zipfile.ZipFile(source_3mf_path, "r") as src, \
         zipfile.ZipFile(output_path, "w") as dst:
        for info in src.infolist():
            if info.filename in REPLACE:
                data, compress = REPLACE[info.filename]
                dst.writestr(
                    zipfile.ZipInfo(info.filename),
                    data,
                    compress_type=compress,
                )
            else:
                dst.writestr(
                    info,
                    src.read(info.filename),
                    compress_type=info.compress_type,
                )


# ---------------------------------------------------------------------------
# Read input file
# ---------------------------------------------------------------------------

def read_input_3mf(path, plate_id=1):
    """
    Read a .gcode.3mf and return (gcode_lines, plate_json_dict).
    plate_json_dict contains bbox info for accurate push_x detection.
    plate_id: which plate's gcode to read (1-based). Falls back to first gcode found.
    """
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()

        preferred = "Metadata/plate_{}.gcode".format(plate_id)
        if preferred in names:
            gcode_name = preferred
        else:
            gcode_name = next(
                (n for n in names if n.endswith(".gcode") and not n.endswith(".md5")),
                None,
            )
        if gcode_name is None:
            raise ValueError("No .gcode file found inside the 3MF archive.")
        with zf.open(gcode_name) as f:
            lines = f.read().decode("utf-8", errors="replace").splitlines(keepends=True)

        plate_json = None
        plate_json_name = next(
            (n for n in names if n.endswith("plate_{}.json".format(plate_id))),
            next((n for n in names if n.endswith("plate_1.json")), None),
        )
        if plate_json_name:
            try:
                with zf.open(plate_json_name) as f:
                    plate_json = json.loads(f.read().decode("utf-8", errors="replace"))
            except Exception:
                pass

    return lines, plate_json


def push_x_from_plate_json(plate_json):
    """
    Extract the X centre of the first object bbox from plate_1.json.
    bbox format: [xmin, ymin, xmax, ymax]
    """
    if not plate_json:
        return None
    try:
        objs = plate_json.get("bbox_objects", [])
        if objs:
            bbox = objs[0]["bbox"]
            return round((bbox[0] + bbox[2]) / 2, 1)
        bbox = plate_json.get("bbox_all")
        if bbox:
            return round((bbox[0] + bbox[2]) / 2, 1)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Strip stock end GCODE
# ---------------------------------------------------------------------------

def strip_end_gcode(lines):
    """
    Find the stock end gcode boundary and cut there.
    Returns (kept_lines, cut_line_number or None).
    """
    for i in range(len(lines) - 1, -1, -1):
        if "; MACHINE_END_GCODE_START" in lines[i]:
            cut = i - 1 if i > 0 and "; FEATURE: Custom" in lines[i - 1] else i
            return lines[:cut], cut
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().lower().startswith("m400") and "wait for buffer" in lines[i].lower():
            return lines[:i], i
    return lines, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Bambu P1S farm loop GCODE post-processor (FactorianDesigns V2)"
    )
    parser.add_argument("input", help="Input .gcode.3mf file")
    parser.add_argument("-o", "--output",        default=None)
    parser.add_argument("--cooldown-temp",       type=int,   default=35,
                        help="Bed release temp C (default: 35)")
    parser.add_argument("--push-speed",          type=int,   default=300,
                        help="Center push feedrate mm/min (default: 300)")
    parser.add_argument("--push-x",              type=float, default=None,
                        help="Push X centre mm (default: auto)")
    parser.add_argument("--lane-offset",         type=float, default=60.0,
                        help="Left/right lane offset from X centre mm (default: 60)")
    parser.add_argument("--flex-cycles",         type=int,   default=3,
                        help="FarmLoop Stage 1 flex cycles before push (default: 3, 0 to disable)")
    parser.add_argument("--flex-z",              type=float, default=204.0,
                        help="Absolute Z where FarmLoop clip engages (default: 204 = 26x2mm jog clicks up from Z256 floor)")
    parser.add_argument("--flex-drop",           type=float, default=20.0,
                        help="mm bed drops past clip engagement each flex cycle (default: 20 = 10x2mm jog clicks)")
    parser.add_argument("--test",                action="store_true",
                        help="Test mode: home + warm + dwell + end sequence only")
    parser.add_argument("--test-bed-temp",       type=int,   default=None,
                        help="[TEST] Bed preheat temp C (default: auto from file)")
    parser.add_argument("--test-nozzle-temp",    type=int,   default=80,
                        help="[TEST] Nozzle preheat temp C (default: 80)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print("ERROR: not found: {}".format(input_path), file=sys.stderr)
        sys.exit(1)
    if not input_path.name.lower().endswith(".gcode.3mf"):
        print("ERROR: input must be a .gcode.3mf file", file=sys.stderr)
        sys.exit(1)

    print("Reading: {}".format(input_path.name))
    lines, plate_json = read_input_3mf(input_path)
    print("  {} lines".format(len(lines)))

    header = parse_header(lines)
    print("  Header: max_z={}, nozzle_temp={}".format(
        header.get("max_z_height"), header.get("nozzle_temp")))

    # -- max_z
    max_z = header.get("max_z_height")
    if max_z is None:
        print("  max_z_height not in header, scanning toolpath...")
        max_z = find_max_z_from_toolpath(lines)
    if max_z is None:
        print("ERROR: cannot determine max_z_height", file=sys.stderr)
        sys.exit(1)
    print("  max_z = {} mm".format(max_z))

    # -- push_x
    if args.push_x is not None:
        push_x = args.push_x
        print("  push_x = {} (manual)".format(push_x))
    else:
        x_min = header.get("print_x_min")
        x_max = header.get("print_x_max")
        if x_min is not None and x_max is not None:
            push_x = round((x_min + x_max) / 2, 1)
            print("  push_x = {} (from GCODE header)".format(push_x))
        else:
            json_x = push_x_from_plate_json(plate_json)
            if json_x is not None:
                push_x = json_x
                print("  push_x = {} (from plate_1.json bbox)".format(push_x))
            else:
                push_x = 120.0  # Factorian's default
                print("  push_x = {} (Factorian default)".format(push_x))

    # Z push height info
    if max_z > 31:
        print("  push_z = {} mm (max_z - 30, Factorian formula)".format(round(max_z - 30, 2)))
    else:
        print("  push_z = 1.0 mm (model <= 31mm)")

    # ------------------------------------------------------------------ TEST
    if args.test:
        if args.test_bed_temp is not None:
            test_bed_temp = args.test_bed_temp
            print("  test_bed_temp = {}C (manual)".format(test_bed_temp))
        else:
            test_bed_temp = find_bed_temp_from_start(lines)
            if test_bed_temp is not None:
                print("  test_bed_temp = {}C (from file M140)".format(test_bed_temp))
            else:
                test_bed_temp = 40
                print("  test_bed_temp = {}C (fallback)".format(test_bed_temp))

        print("TEST MODE")
        print("  max_z        : {} mm".format(max_z))
        print("  push_x       : {} mm  lanes ±{}mm".format(push_x, args.lane_offset))
        print("  cooldown     : {}C".format(args.cooldown_temp))
        print("  preheat bed  : {}C".format(test_bed_temp))
        print("  preheat noz  : {}C".format(args.test_nozzle_temp))

        gcode_str = build_test_gcode(
            lines=lines,
            max_z=max_z,
            push_x=push_x,
            cooldown_temp=args.cooldown_temp,
            push_speed=args.push_speed,
            lane_offset=args.lane_offset,
            flex_cycles=args.flex_cycles,
            flex_z=args.flex_z,
            flex_drop=args.flex_drop,
            test_bed_temp=test_bed_temp,
            test_nozzle_temp=args.test_nozzle_temp,
        )

        stem = input_path.name.split(".")[0]
        out = Path(args.output) if args.output else input_path.with_name(stem + "_test.gcode.3mf")
        write_3mf(out, gcode_str, input_path)

        print("\nWrote: {}".format(out))
        print("Place object on bed then start. Printer homes, dwells 10s, then runs end sequence.")
        return

    # --------------------------------------------------------------- NORMAL
    kept_lines, cut_at = strip_end_gcode(lines)
    if cut_at is None:
        print("  WARNING: end-gcode boundary not found -- appending to full file")
    else:
        print("  Stripped stock end gcode at line {}".format(cut_at))

    end_block = (
        "; FEATURE: Custom\n"
        "; MACHINE_END_GCODE_START\n"
        + build_end_sequence(
            max_z=max_z,
            cooldown_temp=args.cooldown_temp,
            push_x=push_x,
            push_speed=args.push_speed,
            lane_offset=args.lane_offset,
            flex_cycles=args.flex_cycles,
            flex_z=args.flex_z,
            flex_drop=args.flex_drop,
        )
        + "; EXECUTABLE_BLOCK_END\n"
    )

    final_gcode = "".join(kept_lines) + "\n" + end_block

    stem = input_path.name.split(".")[0]
    out = Path(args.output) if args.output else input_path.with_name(stem + "_farmed.gcode.3mf")
    write_3mf(out, final_gcode, input_path)

    print("\nWrote: {}".format(out))
    print("  max_z        : {} mm".format(max_z))
    print("  push_x       : {} mm  (lanes at X{} / X{} / X{})".format(
        push_x,
        round(max(10.0, min(246.0, push_x - args.lane_offset)), 1),
        round(max(10.0, min(246.0, push_x)), 1),
        round(max(10.0, min(246.0, push_x + args.lane_offset)), 1),
    ))
    print("  cooldown     : {}C".format(args.cooldown_temp))
    print("  push speed   : F{} (centre), F2000 (lane clears)".format(args.push_speed))
    print()
    print("Open in BambuStudio and click 'Print Plate'. Do NOT re-slice.")


def process_inplace(path: Path, plate_id: int = 1) -> None:
    """
    BambuBuddy scheduler entry point.

    Called with a single argument: path to a temp 3MF copy.
    Optional second argument: plate number (1-based, default 1).
    Modifies the file in-place using default settings, exits 0 on success.
    """
    lines, plate_json = read_input_3mf(path, plate_id=plate_id)

    header = parse_header(lines)

    max_z = header.get("max_z_height")
    if max_z is None:
        max_z = find_max_z_from_toolpath(lines)
    if max_z is None:
        print("ERROR: cannot determine max_z_height", file=sys.stderr)
        sys.exit(1)

    x_min = header.get("print_x_min")
    x_max = header.get("print_x_max")
    if x_min is not None and x_max is not None:
        push_x = round((x_min + x_max) / 2, 1)
    else:
        push_x = push_x_from_plate_json(plate_json) or 120.0

    kept_lines, _ = strip_end_gcode(lines)

    end_block = (
        "; FEATURE: Custom\n"
        "; MACHINE_END_GCODE_START\n"
        + build_end_sequence(max_z=max_z, cooldown_temp=35, push_x=push_x)
        + "; EXECUTABLE_BLOCK_END\n"
    )

    final_gcode = "".join(kept_lines) + "\n" + end_block

    # Write back to the same path (BambuBuddy expects in-place modification)
    import tempfile, shutil
    with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf", dir=path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write_3mf(tmp_path, final_gcode, path, plate_id=plate_id)
        shutil.move(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    # BambuBuddy calling convention: path [plate_id]
    # Single positional arg (no flags) triggers in-place mode.
    # Optional second arg is the plate number (1-based, default 1).
    if len(sys.argv) >= 2 and not sys.argv[1].startswith("-"):
        p = Path(sys.argv[1])
        if not p.exists():
            print("ERROR: not found: {}".format(p), file=sys.stderr)
            sys.exit(1)
        plate_id = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
        process_inplace(p, plate_id=plate_id)
    else:
        main()
