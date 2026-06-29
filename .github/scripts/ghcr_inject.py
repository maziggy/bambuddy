#!/usr/bin/env python3
"""Inject GHCR container-download stats into the jgehrcke/github-repo-stats report.

GHCR exposes a container's total + 30-day daily-pull series only in the
package page HTML. There is no REST or GraphQL API for it. This script
scrapes that page once per workflow run, merges the rolling 30-day window
into a sidecar CSV on gh-pages, and re-injects a Vega-Lite chart at the
top of ``latest-report/report.html``.

Why the merge: each run only sees the last 30 days, but the CSV grows
forever — days that fall off GitHub's 30-day window stay in the CSV
because they were captured while in-window. Overlapping dates are
overwritten on each run, so GitHub's late-arriving revisions to recent
days self-correct.

Hard-fails if either scrape pattern stops matching. Silent fallbacks
would let the chart freeze at last-known-good and nobody would notice.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GHCR_URL = "https://github.com/{owner}/{pkg}/pkgs/container/{pkg}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) bambuddy-stats"

TOTAL_RE = re.compile(
    r'Total downloads</span>\s*<h3 title="(\d+)">([^<]+)</h3>',
    re.DOTALL,
)
RECT_MERGE_FIRST_RE = re.compile(r'data-merge-count="(\d+)"[^>]*data-date="(\d{4}-\d{2}-\d{2})"')
RECT_DATE_FIRST_RE = re.compile(r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*data-merge-count="(\d+)"')


def fetch_ghcr(owner: str, pkg: str) -> str:
    req = urllib.request.Request(
        GHCR_URL.format(owner=owner, pkg=pkg),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_total(html: str) -> tuple[int, str]:
    m = TOTAL_RE.search(html)
    if not m:
        raise RuntimeError(
            "GHCR scrape: 'Total downloads' marker not found. GitHub markup likely changed — update TOTAL_RE."
        )
    return int(m.group(1)), m.group(2).strip()


def parse_daily(html: str) -> dict[str, int]:
    daily: dict[str, int] = {}
    for m in RECT_MERGE_FIRST_RE.finditer(html):
        daily[m.group(2)] = int(m.group(1))
    for m in RECT_DATE_FIRST_RE.finditer(html):
        daily.setdefault(m.group(1), int(m.group(2)))
    if not daily:
        raise RuntimeError(
            "GHCR scrape: 30-day sparkline rects not found. GitHub markup likely changed — update RECT_*_RE."
        )
    return daily


def merge_csv(csv_path: Path, fresh: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    if csv_path.exists():
        with csv_path.open() as fp:
            for row in csv.DictReader(fp):
                merged[row["date"]] = int(row["daily_count"])
    merged.update(fresh)
    return dict(sorted(merged.items()))


def write_csv(csv_path: Path, series: dict[str, int]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["date", "daily_count"])
        for date, count in series.items():
            w.writerow([date, count])


# Cloned verbatim from jgehrcke's "Total clones" chart so the new chart
# inherits the report's theme (fonts, palette, axis colors).
VEGA_CONFIG = {
    "arc": {"fill": "#1b1e23"},
    "area": {"fill": "#1b1e23"},
    "axisBottom": {
        "domainColor": "#a9b4c4",
        "gridColor": "#a9b4c4",
        "labelColor": "#1b1e23",
        "labelFont": "relative-mono-11-pitch-pro, Menlo, monospace",
        "tickColor": "#a9b4c4",
        "titleColor": "#1b1e23",
        "titleFont": "relative-mono-11-pitch-pro, Menlo, monospace",
    },
    "axisLeft": {
        "domainColor": "#a9b4c4",
        "gridColor": "#a9b4c4",
        "labelColor": "#1b1e23",
        "labelFont": "relative-mono-11-pitch-pro, Menlo, monospace",
        "tickColor": "#a9b4c4",
        "titleColor": "#1b1e23",
        "titleFont": "relative-mono-11-pitch-pro, Menlo, monospace",
    },
    "axisX": {"grid": False},
    "axisY": {"grid": False, "labelBound": True},
    "background": "#FFFFFF",
    "group": {"fill": "#FFFFFF"},
    "header": {
        "fontWeight": 400,
        "labelFont": "relative-mono-11-pitch-pro, Menlo, monospace",
        "titleFont": "relative-mono-11-pitch-pro, Menlo, monospace",
    },
    "legend": {
        "labelFont": "relative-mono-11-pitch-pro, Menlo, monospace",
        "symbolSize": 200,
        "symbolType": "circle",
        "titleFont": "relative-mono-11-pitch-pro, Menlo, monospace",
    },
    "line": {"color": "#1b1e23", "stroke": "#1b1e23"},
    "path": {"stroke": "#1b1e23"},
    "point": {
        "color": "#1b1e23",
        "cursor": "pointer",
        "filled": True,
        "size": 20,
    },
    "range": {
        "category": ["#85a2f7", "#ea9755", "#7eb36a", "#f07071", "#bc85d9", "#e587b6", "#a9b4c4", "#d4c05e", "#64b9c4"],
    },
    "style": {
        "bar": {"fill": "#1b1e23"},
        "text": {
            "font": "relative-mono-11-pitch-pro, Menlo, monospace",
            "fontWeight": 400,
        },
    },
    "symbol": {"shape": "circle"},
    "title": {
        "anchor": "start",
        "font": "relative-mono-11-pitch-pro, Menlo, monospace",
        "fontWeight": 400,
    },
    "trail": {"color": "#1b1e23", "stroke": "#1b1e23"},
    "view": {"stroke": None},
}


def build_vega_spec(series: dict[str, int]) -> dict:
    rows = [{"time": f"{date}T00:00:00+00:00", "daily_count": count} for date, count in series.items()]
    counts = [r["daily_count"] for r in rows] or [1]
    y_max = max(counts)
    dates = sorted(series.keys())
    x_domain = [dates[0], dates[-1]] if dates else None
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v4.17.0.json",
        "config": VEGA_CONFIG,
        "data": {"name": "data-ghcr-pulls"},
        "datasets": {"data-ghcr-pulls": rows},
        "encoding": {
            "tooltip": [
                {"field": "daily_count", "format": ".0f", "title": "pulls", "type": "quantitative"},
                {"field": "time", "format": "%B %e, %Y", "title": "date", "type": "temporal"},
            ],
            "x": {
                "axis": {"labelAngle": 25},
                "field": "time",
                "scale": {"domain": x_domain} if x_domain else {},
                "timeUnit": "yearmonthdate",
                "title": "date",
                "type": "temporal",
            },
            "y": {
                "axis": {"values": [1, 10, 50, 100, 500, 1000, 5000, 10000, 50000]},
                "field": "daily_count",
                "scale": {
                    "domain": [0, y_max * 1.1 if y_max > 0 else 1],
                    "type": "symlog",
                    "zero": True,
                },
                "title": "container pulls per day",
                "type": "quantitative",
            },
        },
        "height": 200,
        "mark": {"point": True, "type": "line"},
        "padding": 10,
        "width": "container",
    }


TOC_START = "<!-- ghcr:toc-start -->"
TOC_END = "<!-- ghcr:toc-end -->"
SECTION_START = "<!-- ghcr:section-start -->"
SECTION_END = "<!-- ghcr:section-end -->"
SCRIPT_START = "<!-- ghcr:script-start -->"
SCRIPT_END = "<!-- ghcr:script-end -->"


def _strip_existing(html: str, start: str, end: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    return pattern.sub("", html)


def patch_report(
    report_path: Path,
    spec: dict,
    cumulative: int,
    cumulative_display: str,
    fetched_at: str,
    owner: str,
    pkg: str,
) -> None:
    html = report_path.read_text(encoding="utf-8")

    # Idempotency: if a prior run left markers (shouldn't happen because
    # jgehrcke regenerates the file, but guard against partial re-runs),
    # strip them before re-injecting.
    for s, e in (
        (TOC_START, TOC_END),
        (SECTION_START, SECTION_END),
        (SCRIPT_START, SCRIPT_END),
    ):
        html = _strip_existing(html, s, e)

    toc_block = f'{TOC_START}\n<li><a href="#ghcr-pulls">Container pulls (ghcr.io)</a></li>\n{TOC_END}\n'
    section_block = (
        f"{SECTION_START}\n"
        f'<h2 id="ghcr-pulls">Container pulls (ghcr.io)</h2>\n'
        f"<p>Daily pulls of <code>ghcr.io/{owner}/{pkg}</code>. "
        f"Cumulative: <strong>{cumulative:,}</strong> "
        f"({cumulative_display}). Source refreshed {fetched_at}.</p>\n"
        f'<h4 id="ghcr-pulls-daily">Pulls per day</h4>\n'
        f'<div id="chart_ghcr_pulls_daily" class="full-width-chart">\n\n</div>\n'
        f'<div class="pagebreak-for-print">\n\n</div>\n'
        f"{SECTION_END}\n"
    )
    script_block = (
        f"{SCRIPT_START}\n"
        f'<script type="text/javascript">\n'
        f"vegaEmbed('#chart_ghcr_pulls_daily', "
        f"{json.dumps(spec, separators=(',', ':'))}, "
        f'{{"actions": false, "renderer": "svg"}}).catch(console.error);\n'
        f"</script>\n"
        f"{SCRIPT_END}\n"
    )

    toc_anchor = "<p>Table of contents:</p>\n<ul>\n"
    if toc_anchor not in html:
        raise RuntimeError("report.html: TOC anchor not found; layout drift?")
    html = html.replace(toc_anchor, toc_anchor + toc_block, 1)

    section_anchor = "</nav>\n"
    if section_anchor not in html:
        raise RuntimeError("report.html: section anchor (</nav>) not found; layout drift?")
    html = html.replace(section_anchor, section_anchor + section_block, 1)

    script_anchor = "</article>\n"
    if script_anchor not in html:
        raise RuntimeError("report.html: script anchor (</article>) not found; layout drift?")
    html = html.replace(script_anchor, script_block + script_anchor, 1)

    report_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--pkg", required=True)
    parser.add_argument(
        "--ghcr-cache",
        type=Path,
        default=None,
        help="Read GHCR HTML from a local file instead of fetching. For local dry-runs only.",
    )
    args = parser.parse_args()

    if not args.report.exists():
        print(f"::error::report not found: {args.report}", file=sys.stderr)
        return 1

    if args.ghcr_cache:
        html = args.ghcr_cache.read_text(encoding="utf-8")
        print(f"Loaded cached GHCR HTML: {args.ghcr_cache} ({len(html):,} bytes)")
    else:
        html = fetch_ghcr(args.owner, args.pkg)
        print(f"Fetched GHCR page: {len(html):,} bytes")

    cumulative, cumulative_display = parse_total(html)
    fresh_daily = parse_daily(html)
    print(f"Cumulative pulls: {cumulative:,} ({cumulative_display})")
    print(f"Fresh days from sparkline: {len(fresh_daily)}")

    merged = merge_csv(args.csv, fresh_daily)
    write_csv(args.csv, merged)
    print(f"Merged CSV rows: {len(merged)} -> {args.csv}")

    spec = build_vega_spec(merged)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    patch_report(
        args.report,
        spec,
        cumulative,
        cumulative_display,
        fetched_at,
        args.owner,
        args.pkg,
    )
    print(f"Patched: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
