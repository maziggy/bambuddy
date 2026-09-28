"""Regenerate backend/app/data/hms_catalog.json from a Bambu Studio checkout.

Usage:
    python scripts/generate_hms_catalog.py /path/to/BambuStudio

Bambu Studio bundles its HMS texts in resources/hms/hms_en_<model>.json, one
file per 3-character serial prefix. Each file has two lists:

  device_hms    faults from the report's `hms[]` array, keyed by the 16-hex code
                the printer shows (attr then code: module, module no., part,
                part no., alert level, error)
  device_error  `print_error` faults, keyed by the 8-hex value

Most texts are the same on every model, but not all: 0300_8001 means "paused by
the user" on some models and "paused by a pause command in the file" on others.
So the output holds one merged table plus, per model, only the entries that
differ from it. A code listed with empty text stays in as "" -- Bambu keeping a
code but publishing nothing for it is what Bambuddy uses to leave it out of the
fault count (#2728), so it must not be dropped or filled in.

backend/app/data/hms_catalog_extra.json holds `print_error` texts Bambu Studio does not
ship (they came from the ha-bambulab table Bambuddy used before). They are
merged in only where Studio has no entry, so Studio always wins.

Run it by hand when Bambu Studio updates its HMS files, and review the diff:
added codes, changed texts and codes that lost their text are all visible there.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "backend" / "app" / "data" / "hms_catalog.json"
EXTRA = REPO_ROOT / "backend" / "app" / "data" / "hms_catalog_extra.json"

SECTIONS = {"hms": ("device_hms", 16), "error": ("device_error", 8)}


def _studio_commit(studio: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(studio), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_models(hms_dir: Path) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, int]]:
    """Return {section: {model: {code: text}}} and each model file's `ver`."""
    per_model: dict[str, dict[str, dict[str, str]]] = {section: {} for section in SECTIONS}
    versions: dict[str, int] = {}
    files = sorted(hms_dir.glob("hms_en_*.json"))
    if not files:
        sys.exit(f"no hms_en_*.json files in {hms_dir}")
    for path in files:
        model = path.stem.removeprefix("hms_en_")
        doc = json.loads(path.read_text(encoding="utf-8"))
        versions[model] = doc.get("ver", 0)
        for section, (source_key, length) in SECTIONS.items():
            entries = doc["data"][source_key]["en"]
            table: dict[str, str] = {}
            for entry in entries:
                code = entry["ecode"].strip().upper()
                if len(code) != length or any(c not in "0123456789ABCDEF" for c in code):
                    sys.exit(f"{path.name}: unexpected {source_key} code {entry['ecode']!r}")
                table[code] = entry.get("intro", "").strip()
            per_model[section][model] = table
    return per_model, versions


def _merge(tables: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, dict[str, str]], int]:
    """Pick one text per code, and keep per model only what differs from it.

    The merged text is the one most models use, ignoring models that list the
    code with no text; a tie goes to the model that sorts first, so the output
    is stable between runs. Returns (merged, overrides, codes with a conflict).
    """
    models = sorted(tables)
    codes = sorted({code for table in tables.values() for code in table})
    merged: dict[str, str] = {}
    overrides: dict[str, dict[str, str]] = {}
    conflicts = 0
    for code in codes:
        texts = [tables[m][code] for m in models if code in tables[m]]
        counts = Counter(t for t in texts if t)
        if counts:
            best = max(counts.values())
            merged[code] = next(t for t in texts if counts.get(t) == best)
        else:
            merged[code] = ""
        if len(counts) > 1:
            conflicts += 1
        for model in models:
            text = tables[model].get(code)
            if text is not None and text != merged[code]:
                overrides.setdefault(model, {})[code] = text
    return merged, overrides, conflicts


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    studio = Path(sys.argv[1]).resolve()
    hms_dir = studio / "resources" / "hms"
    per_model, versions = _load_models(hms_dir)

    catalog: dict = {
        "_source": {
            "generator": "scripts/generate_hms_catalog.py",
            "bambu_studio_commit": _studio_commit(studio),
            "model_file_versions": versions,
            "extra": str(EXTRA.relative_to(REPO_ROOT)),
        }
    }
    for section in SECTIONS:
        merged, overrides, conflicts = _merge(per_model[section])
        print(
            f"{section}: {len(merged)} codes, {sum(1 for t in merged.values() if not t)} without text, "
            f"{conflicts} with different texts per model, "
            f"{sum(len(o) for o in overrides.values())} model overrides"
        )
        catalog[section] = merged
        catalog[f"{section}_by_model"] = {model: overrides[model] for model in sorted(overrides)}

    extra = json.loads(EXTRA.read_text(encoding="utf-8"))
    added = 0
    for code, text in extra["error"].items():
        if code not in catalog["error"]:
            catalog["error"][code] = text
            added += 1
    catalog["error"] = dict(sorted(catalog["error"].items()))
    print(f"extra: {added} of {len(extra['error'])} codes added (the rest are now in Bambu Studio)")

    OUTPUT.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
