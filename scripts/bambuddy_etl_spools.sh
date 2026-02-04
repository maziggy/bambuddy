#!/usr/bin/env bash
set -euo pipefail

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Falta comando: $1" >&2; exit 1; }; }
need_cmd sqlite3
need_cmd date
need_cmd mkdir
need_cmd mv
need_cmd head

# Directorio donde está este script y repo raíz (asumimos scripts/...)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# Usage:
#   ./scripts/bambuddy_etl_spools.sh [SRC_DB] [DST_DB] [OUTDIR]
SRC_DB="${1:-$REPO_DIR/bambuddy.db}"    # bambuddy.db (solo lectura)
DST_DB="${2:-$REPO_DIR/tracking.db}"    # tracking.db (escritura)
OUTDIR="${3:-$REPO_DIR/out}"

CSV_COLOR="$OUTDIR/filamento_por_color.csv"
CSV_SPOOL="$OUTDIR/filamento_por_bobina.csv"
SPOOLMAN_LOG="$OUTDIR/spoolman_push.log"

mkdir -p "$OUTDIR"
[[ -f "$SRC_DB" ]] || { echo "No existe SRC_DB: $SRC_DB" >&2; exit 1; }

ts="$(date +%Y%m%d-%H%M%S)"
[[ -f "$CSV_COLOR" ]] && mv -- "$CSV_COLOR" "${CSV_COLOR}.${ts}.bak"
[[ -f "$CSV_SPOOL" ]] && mv -- "$CSV_SPOOL" "${CSV_SPOOL}.${ts}.bak"

# Escapar comillas simples para ATTACH
SRC_DB_ESC="${SRC_DB//\'/\'\'}"

# 0) Esquema (tracking.db) + mapas de color + spoolman_map
sqlite3 "$DST_DB" <<'SQL'
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS ingest_state (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
INSERT OR IGNORE INTO ingest_state(k,v) VALUES ('last_archive_id','0');

CREATE TABLE IF NOT EXISTS print_facts (
  archive_id INTEGER PRIMARY KEY,
  completed_at TEXT,
  filament_used_grams REAL,
  filament_type TEXT,
  filament_color TEXT,
  has_map INTEGER NOT NULL DEFAULT 0,
  map_keys INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS print_trays (
  archive_id INTEGER NOT NULL,
  ams_id INTEGER NOT NULL,
  tray_index INTEGER NOT NULL,
  tag_uid TEXT,
  tray_uuid TEXT,
  tray_color TEXT,
  tray_weight REAL,
  state INTEGER,
  PRIMARY KEY (archive_id, ams_id, tray_index)
);

CREATE TABLE IF NOT EXISTS allocations (
  archive_id INTEGER PRIMARY KEY,
  tag_uid TEXT NOT NULL,
  used_g REAL NOT NULL,
  method TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS color_to_tag_uid (
  color_rgba TEXT PRIMARY KEY,
  tag_uid TEXT NOT NULL
);

-- Mapa de nombres legibles (amplíalo cuando quieras)
CREATE TABLE IF NOT EXISTS color_map (
  color_hex  TEXT PRIMARY KEY,   -- #RRGGBB o #RRGGBBAA
  color_name TEXT NOT NULL
);

INSERT OR REPLACE INTO color_map(color_hex, color_name) VALUES
  ('#000000',   'NegroMate'),
  ('#00000000', 'Transparente'),
  ('#3F8E43',   'Verde'),
  ('#8E9089',   'Gris'),
  ('#MULTI',    'MultipleColor');

-- Spoolman mapping (tag_uid -> spool_id)
CREATE TABLE IF NOT EXISTS spoolman_map (
  tag_uid  TEXT PRIMARY KEY,
  spool_id INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alloc_tag_uid ON allocations(tag_uid);
CREATE INDEX IF NOT EXISTS idx_print_facts_completed ON print_facts(completed_at);
SQL

# 1) Ingest incremental desde SRC_DB -> DST_DB
sqlite3 "$DST_DB" <<SQL
ATTACH DATABASE '$SRC_DB_ESC' AS src;

DROP TABLE IF EXISTS temp.newprints;
CREATE TEMP TABLE newprints AS
SELECT
  pa.id AS archive_id,
  pa.completed_at,
  pa.filament_used_grams,
  pa.filament_type,
  pa.filament_color,
  json_extract(pa.extra_data,'$."_print_data"."raw_data"."ams_extruder_map"') AS map_json,
  pa.extra_data AS extra_data
FROM src.print_archives pa
WHERE pa.status='completed'
  AND pa.id > (SELECT CAST(v AS INTEGER) FROM ingest_state WHERE k='last_archive_id');

INSERT OR IGNORE INTO print_facts(archive_id, completed_at, filament_used_grams, filament_type, filament_color, has_map, map_keys)
SELECT
  archive_id,
  completed_at,
  filament_used_grams,
  filament_type,
  filament_color,
  CASE WHEN map_json IS NOT NULL THEN 1 ELSE 0 END AS has_map,
  CASE WHEN map_json IS NOT NULL THEN (SELECT COUNT(*) FROM json_each(map_json)) ELSE 0 END AS map_keys
FROM newprints;

INSERT OR IGNORE INTO print_trays(archive_id, ams_id, tray_index, tag_uid, tray_uuid, tray_color, tray_weight, state)
SELECT
  np.archive_id,
  0 AS ams_id,
  jt.key AS tray_index,
  json_extract(jt.value,'$.tag_uid') AS tag_uid,
  json_extract(jt.value,'$.tray_uuid') AS tray_uuid,
  json_extract(jt.value,'$.tray_color') AS tray_color,
  json_extract(jt.value,'$.tray_weight') AS tray_weight,
  json_extract(jt.value,'$.state') AS state
FROM newprints np
JOIN json_each(np.extra_data, '$."_print_data"."raw_data".ams[0].tray') jt;

WITH m AS (
  SELECT
    np.archive_id,
    je.value AS tray_index
  FROM newprints np
  JOIN json_each(np.map_json) je
  WHERE np.map_json IS NOT NULL
    AND (SELECT COUNT(*) FROM json_each(np.map_json)) = 1
),
resolved AS (
  SELECT
    np.archive_id,
    np.filament_used_grams AS used_g,
    json_extract(np.extra_data, '$."_print_data"."raw_data".ams[0].tray[' || m.tray_index || '].tag_uid') AS tag_uid
  FROM newprints np
  JOIN m ON m.archive_id = np.archive_id
)
INSERT OR IGNORE INTO allocations(archive_id, tag_uid, used_g, method, confidence)
SELECT
  archive_id,
  tag_uid,
  used_g,
  'map',
  'high'
FROM resolved
WHERE tag_uid IS NOT NULL
  AND tag_uid <> '0000000000000000';

UPDATE ingest_state
SET v = CAST((SELECT COALESCE(MAX(id), CAST(v AS INTEGER)) FROM src.print_archives WHERE status='completed') AS TEXT)
WHERE k='last_archive_id';

DROP TABLE IF EXISTS temp.newprints;
DETACH DATABASE src;
SQL

# 2) Autopoblar color_to_tag_uid desde el último print con trays
sqlite3 "$DST_DB" <<'SQL'
INSERT OR IGNORE INTO color_to_tag_uid(color_rgba, tag_uid)
WITH last_with_trays AS (
  SELECT archive_id
  FROM print_trays
  ORDER BY archive_id DESC
  LIMIT 1
),
pairs AS (
  SELECT
    upper(substr(tray_color,1,8)) AS color_rgba,
    tag_uid
  FROM print_trays
  WHERE archive_id = (SELECT archive_id FROM last_with_trays)
    AND tray_color IS NOT NULL
    AND length(tray_color) >= 8
    AND tag_uid IS NOT NULL
)
SELECT color_rgba, tag_uid
FROM pairs;
SQL

# 3) Backfill por color (prints sin map, mono-color)
sqlite3 "$DST_DB" <<'SQL'
WITH candidates AS (
  SELECT
    pf.archive_id,
    pf.filament_used_grams AS used_g,
    pf.filament_color AS fc
  FROM print_facts pf
  LEFT JOIN allocations a ON a.archive_id = pf.archive_id
  WHERE pf.has_map=0
    AND a.archive_id IS NULL
    AND pf.filament_color IS NOT NULL
    AND pf.filament_color NOT LIKE '%,%'
    AND (pf.filament_type IS NULL OR pf.filament_type NOT LIKE '%,%')
),
norm AS (
  SELECT
    archive_id,
    used_g,
    CASE
      WHEN length(upper(ltrim(fc,'#'))) = 6 THEN upper(ltrim(fc,'#')) || 'FF'
      WHEN length(upper(ltrim(fc,'#'))) = 8 THEN upper(ltrim(fc,'#'))
      ELSE NULL
    END AS color_rgba
  FROM candidates
),
resolved AS (
  SELECT
    n.archive_id,
    n.used_g,
    c.tag_uid
  FROM norm n
  JOIN color_to_tag_uid c ON c.color_rgba = n.color_rgba
  WHERE n.color_rgba IS NOT NULL
)
INSERT OR IGNORE INTO allocations(archive_id, tag_uid, used_g, method, confidence)
SELECT archive_id, tag_uid, used_g, 'backfill_color', 'medium'
FROM resolved;
SQL

# 4) CSV por color (total)
sqlite3 -header -csv "$DST_DB" "
WITH by_color AS (
  SELECT
    CASE
      WHEN filament_color LIKE '%,%' THEN '#MULTI'
      WHEN length(ltrim(filament_color,'#')) >= 8 THEN '#' || upper(substr(ltrim(filament_color,'#'),1,8))
      ELSE '#' || upper(substr(ltrim(filament_color,'#'),1,6))
    END AS color_hex,
    round(total(filament_used_grams), 2) AS grams
  FROM print_facts
  GROUP BY color_hex
)
SELECT color_hex, grams
FROM by_color
ORDER BY grams DESC;
" > "$CSV_COLOR"

# 5) CSV por bobina (tag_uid) + spool_id + nombre color + tipo dominante
sqlite3 -header -csv "$DST_DB" "
WITH by_spool AS (
  SELECT
    a.tag_uid,
    max(t.tray_color) AS last_tray_color,
    round(max(coalesce(t.tray_weight,1000)), 0) AS nominal_g,
    round(total(a.used_g), 2) AS used_g,
    round(max(coalesce(t.tray_weight,1000)) - total(a.used_g), 2) AS approx_remaining_g
  FROM allocations a
  LEFT JOIN print_trays t
    ON t.archive_id = a.archive_id
   AND t.tag_uid = a.tag_uid
  GROUP BY a.tag_uid
),
named AS (
  SELECT
    bs.tag_uid,
    bs.last_tray_color,
    ('#' || upper(substr(bs.last_tray_color,1,8))) AS color_hex8,
    ('#' || upper(substr(bs.last_tray_color,1,6))) AS color_hex6,
    bs.nominal_g,
    bs.used_g,
    bs.approx_remaining_g
  FROM by_spool bs
),
type_totals AS (
  SELECT
    a.tag_uid,
    pf.filament_type,
    sum(a.used_g) AS g_by_type
  FROM allocations a
  JOIN print_facts pf ON pf.archive_id = a.archive_id
  WHERE pf.filament_type IS NOT NULL AND pf.filament_type <> ''
    AND pf.filament_type NOT LIKE '%,%'
  GROUP BY a.tag_uid, pf.filament_type
),
type_ranked AS (
  SELECT
    tag_uid,
    filament_type,
    g_by_type,
    row_number() OVER (
      PARTITION BY tag_uid
      ORDER BY g_by_type DESC, filament_type
    ) AS rn
  FROM type_totals
),
best_type AS (
  SELECT tag_uid, filament_type
  FROM type_ranked
  WHERE rn = 1
)
SELECT
  n.tag_uid,
  sm.spool_id,
  coalesce(cm8.color_hex, cm6.color_hex, '(desconocido)') AS color_hex,
  coalesce(cm8.color_name, cm6.color_name, '(desconocido)') AS color_name,
  coalesce(bt.filament_type, '(desconocido)') AS filament_type,
  n.nominal_g,
  n.used_g,
  n.approx_remaining_g
FROM named n
LEFT JOIN color_map cm8 ON cm8.color_hex = n.color_hex8
LEFT JOIN color_map cm6 ON cm6.color_hex = n.color_hex6
LEFT JOIN best_type bt  ON bt.tag_uid = n.tag_uid
LEFT JOIN spoolman_map sm ON sm.tag_uid = n.tag_uid
ORDER BY n.used_g DESC;
" > "$CSV_SPOOL"

# 6) Aviso si faltan mappings de spoolman_map (soft)
missing="$(sqlite3 -noheader "$DST_DB" "
SELECT count(*)
FROM (SELECT DISTINCT tag_uid FROM allocations) a
LEFT JOIN spoolman_map sm ON sm.tag_uid = a.tag_uid
WHERE sm.spool_id IS NULL;
")"

if [[ "${missing:-0}" != "0" ]]; then
  echo "WARN: faltan $missing mappings en spoolman_map (tag_uid -> spool_id)."
  echo "Estos tag_uid no se sincronizarán a Spoolman:"
  sqlite3 -noheader "$DST_DB" "
  SELECT a.tag_uid
  FROM (SELECT DISTINCT tag_uid FROM allocations) a
  LEFT JOIN spoolman_map sm ON sm.tag_uid = a.tag_uid
  WHERE sm.spool_id IS NULL
  ORDER BY a.tag_uid;
  "
fi

# 7) Sync a Spoolman (overwrite remaining_weight) + fail-soft + log
# Default embebido; puedes sobreescribirlo desde fuera si quieres:
SPOOLMAN_URL="${SPOOLMAN_URL:-http://localhost:7912}"

if [[ -n "$SPOOLMAN_URL" ]]; then
  need_cmd curl
  echo "" >> "$SPOOLMAN_LOG"
  echo "[$(date -Is)] Spoolman sync (overwrite remaining_weight) -> $SPOOLMAN_URL" | tee -a "$SPOOLMAN_LOG" >/dev/null

  # Solo sincroniza spools que estén mapeados (JOIN spoolman_map)
  sqlite3 -csv -noheader "$DST_DB" "
  SELECT
    sm.spool_id,
    round(max(coalesce(t.tray_weight,1000)) - total(a.used_g), 2) AS remaining_g
  FROM allocations a
  JOIN spoolman_map sm ON sm.tag_uid = a.tag_uid
  LEFT JOIN print_trays t
    ON t.archive_id = a.archive_id
   AND t.tag_uid = a.tag_uid
  GROUP BY sm.spool_id
  ORDER BY sm.spool_id;
  " | while IFS=, read -r spool_id remaining_g; do
        if curl -sSf -X PATCH "$SPOOLMAN_URL/api/v1/spool/$spool_id" \
             -H "Content-Type: application/json" \
             -d "{\"remaining_weight\": $remaining_g}" >/dev/null; then
          echo "OK spool_id=$spool_id remaining_g=$remaining_g" | tee -a "$SPOOLMAN_LOG" >/dev/null
        else
          echo "WARN spool_id=$spool_id (no se pudo actualizar)" | tee -a "$SPOOLMAN_LOG" >/dev/null
        fi
      done
fi

echo "OK"
echo "REPO_DIR: $REPO_DIR"
echo "SRC_DB: $SRC_DB"
echo "DST_DB: $DST_DB"
echo "OUTDIR: $OUTDIR"
echo "Generado: $CSV_COLOR"
echo "Generado: $CSV_SPOOL"
echo "Log: $SPOOLMAN_LOG"
echo
echo "Preview (por bobina):"
head -n 20 "$CSV_SPOOL"
