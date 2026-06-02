// Verifies parity across locale files (en / de / fr / it / ja / pt-BR / zh-CN / zh-TW):
//   1. Leaf-key sets are identical
//   2. Each leaf's {{placeholder}} set is identical
//   3. Plural suffixes: every en key ending in _plural / _one / _other must
//      exist in every other locale, and other locales must not introduce an
//      _one key that en does not have.
//   4. NEW: leaves in a non-English locale must not be identical to en, unless
//      the value is a brand name / technical token / pure punctuation, OR the
//      key+locale pair is explicitly listed in IDENTICAL_TO_EN_ALLOWED below.
//      Catches the "copy English text into non-English locale to satisfy the
//      key-count parity gate" anti-pattern that accumulated 700+ shipped
//      strings of debt before the gate was tightened. Add an explicit entry
//      ONLY when the string is a real word/term in that target locale.
// Malformed input (missing `export default`, parse errors, non-string leaves,
// unsupported property kinds) fails loudly instead of silently passing the gate.
// Exits 1 with a diagnostic report on any failure, else exits 0.

import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, '..');
const localesDir = path.join(frontendDir, 'src/i18n/locales');
const tsPath = path.join(frontendDir, 'node_modules/typescript/lib/typescript.js');

const tsModule = await import(url.pathToFileURL(tsPath).href);
const ts = tsModule.default ?? tsModule;

function collectLeaves(node, prefix, leaves) {
  if (!ts.isObjectLiteralExpression(node)) return;
  for (const prop of node.properties) {
    if (!ts.isPropertyAssignment(prop)) {
      console.error(
        `Unsupported property kind ${ts.SyntaxKind[prop.kind]} at "${prefix}" ` +
        `(locale files must use plain \`key: value\` assignments — no spread, shorthand, methods, or accessors).`,
      );
      process.exit(1);
    }
    let name;
    if (ts.isIdentifier(prop.name)) name = prop.name.text;
    else if (ts.isStringLiteral(prop.name) || ts.isNoSubstitutionTemplateLiteral(prop.name)) name = prop.name.text;
    else if (ts.isComputedPropertyName(prop.name)) {
      console.error(`ComputedPropertyName not allowed in locale file at path "${prefix}"`);
      process.exit(1);
    } else {
      console.error(`Unsupported property-name kind ${ts.SyntaxKind[prop.name.kind]} at "${prefix}"`);
      process.exit(1);
    }
    const p = prefix ? `${prefix}.${name}` : name;
    if (ts.isObjectLiteralExpression(prop.initializer)) {
      collectLeaves(prop.initializer, p, leaves);
    } else {
      const value = extractStringValue(prop.initializer, p);
      leaves.set(p, value);
    }
  }
}

function extractStringValue(node, keyPath) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) {
    let out = node.head.text;
    for (const span of node.templateSpans) {
      out += '${' + span.expression.getText() + '}';
      out += span.literal.text;
    }
    return out;
  }
  console.error(
    `Non-string leaf at "${keyPath}" (kind=${ts.SyntaxKind[node.kind]}): ${node.getText()}\n` +
    `Locale files must only contain string or template literals as leaf values.`,
  );
  process.exit(1);
}

function loadLocale(filePath) {
  const src = fs.readFileSync(filePath, 'utf8');
  const sf = ts.createSourceFile(filePath, src, ts.ScriptTarget.Latest, true);
  if (sf.parseDiagnostics && sf.parseDiagnostics.length > 0) {
    console.error(`${filePath}: ${sf.parseDiagnostics.length} parse error(s):`);
    for (const d of sf.parseDiagnostics.slice(0, 10)) {
      const msg = typeof d.messageText === 'string' ? d.messageText : d.messageText.messageText;
      const { line, character } = sf.getLineAndCharacterOfPosition(d.start ?? 0);
      console.error(`  ${line + 1}:${character + 1} ${msg}`);
    }
    process.exit(1);
  }
  const leaves = new Map();
  let foundExport = false;
  ts.forEachChild(sf, (n) => {
    if (ts.isExportAssignment(n)) {
      foundExport = true;
      collectLeaves(n.expression, '', leaves);
    }
  });
  if (!foundExport) {
    console.error(`${filePath}: no \`export default\` found — locale files must use \`export default { ... }\`.`);
    process.exit(1);
  }
  if (leaves.size === 0) {
    console.error(`${filePath}: \`export default\` resolved to zero leaves — file is empty or not a nested object.`);
    process.exit(1);
  }
  return leaves;
}

const placeholderRe = /\{\{[^{}]+\}\}/g;

// Heuristic: values that are ALWAYS allowed to match en, regardless of locale.
// Brand names, technical tokens, pure punctuation, very short strings, version
// numbers, hex codes, and ALL-CAPS acronyms. Cognates that happen to be the
// same word in a specific locale go in IDENTICAL_TO_EN_ALLOWED instead.
function isAlwaysAllowedIdentical(value) {
  if (!value) return true;
  if (/^[\s\W_]+$/.test(value)) return true;            // pure punctuation/whitespace
  if (value.length <= 2) return true;                   // single character or 2-char abbrev
  if (/^[A-Z][A-Z0-9_]+$/.test(value)) return true;     // ALL_CAPS_TOKEN
  if (/^v?\d+(\.\d+)+/.test(value)) return true;        // version-like
  if (/^#[0-9a-fA-F]{3,8}$/.test(value)) return true;   // hex color
  if (/^\{\{[^}]+\}\}$/.test(value)) return true;       // pure placeholder
  if (/^[0-9a-fA-F]{6}$/.test(value)) return true;      // bare hex color
  if (/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i.test(value)) return true;  // email
  if (/^https?:\/\//.test(value)) return true;          // URL
  if (/^ON,\s+true,\s+1$/.test(value)) return true;     // literal example "ON, true, 1"
  // Brand / technical names that ship verbatim everywhere.
  if (/^(Bambuddy|BamBuddy|SpoolBuddy|Bambu Lab|Bambu Studio|Bambu Studio 2\.6\+|Bambu Studio sidecar URL|OrcaSlicer|OrcaSlicer sidecar URL|MakerWorld|Spoolman|\(Spoolman\)|Spoolman URL|Tailscale|GitHub|GitLab|Gitea|Forgejo|Discord|MQTT|FTP|HTTPS?|JSON|YAML|RTSP|TLS|SSL|CSRF|OIDC|SSO|SSO \/ OIDC|LDAP|TOTP|2FA|MFA|API|AMS|CRC|SHA256|SHA-256|kWh|MB|GB|KB|RGBA?|HSL|RGB|UTC|ISO|UI|HTTP|HTTP Method|H2D|H2D Pro|X1C|X1E|P1S|P1P|A1|A1 Mini|H2C|N3F|N3S|PETG|PLA|ABS|PA|TPU|PEI|PA-CF|PVA|HIPS|ASA|PC|PETG-HF|G\.code|G-code|gcode|cm³|°C|°F|GCODE|SOURCE|ntfy|Pushover|Telegram|Webhook|Webhook URL|Home Assistant|Home Assistant URL|CallMeBot\/WhatsApp|Bambuddy URL|Cool Plate|Cool Plate SuperTack|Engineering Plate|High Temp Plate|Smooth PEI Plate|Textured PEI Plate|Ext-L|Ext-R|ISO \(YYYY-MM-DD\))$/.test(value)) return true;
  return false;
}

// Per-(locale, value) allow-list for strings that are a real word/term in
// that target locale and so legitimately match en.ts. Curated — add an entry
// here ONLY after verifying that the word is correct (not just a shortcut to
// silence the check).
//
// Convention: same shape as the locales themselves — { de: Set, fr: Set, ... }.
// Values are matched exactly. To allow a value across many locales, list it in
// each one (verbosity is the point: every locale's allow-list is an explicit
// translator decision).
// German loanwords / cognates from English are extensive. Most short technical
// UI labels are identical in DE. List below curates the legitimate ones.
const DE_COGNATES = [
  'Name', 'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Modus',
  'Stop', 'Reset', 'Test', 'Code', 'Token', 'Server', 'Port', 'Bug', 'Job',
  'Pause', 'Power', 'System', 'Problem', 'Designer', 'Extruder', 'Firmware',
  'Material', 'Original', 'Position', 'Webhook', 'Workflow', 'Slicer',
  'Region', 'Normal', 'Orange', 'Branch', 'Budget', 'Commit', 'Global',
  'Version', 'Slot', 'Live', 'Rate', 'Host', 'Trend', 'Min', 'Admin', 'Cloud',
  'Filament', 'Filaments', 'Software', 'Hardware', 'Avatar', 'Pin', 'Modal',
  'Active', 'Plate', 'Layer', 'Total', 'Plus', 'Pro', 'Mini', 'Studio',
  'Temperatur', 'Process', 'Service', 'Cache', 'Color', 'Login', 'Logout',
  'Action', 'Description', 'Sender', 'Setup', 'Bundle', 'Cluster', 'Tier',
  'Standard (100%)', 'Sport (124%)', 'Ludicrous (166%)',
  'Smart Plugs', 'Smart Switches', 'Smart Plug', 'High Flow',
  'Optional', 'optional', 'Filter', 'Filters', 'optional)',
  'Material:', 'Default:', 'Name *', '(System)', '(Inv)',
  'Spoolman URL', 'Bundle', 'Slicer Bundles', 'Imported',
  'STARTTLS (Port 587)', 'SSL/TLS (Port 465)', 'Sport', 'Standard',
  'EC984C,#6CD4BC,A66EB9,D87694',
  'Hex', 'Warm', 'Neutral', 'Navigation', 'Screenshot', 'Architecture',
  'Backend & Auth', 'Stream Overlay', 'Bambuddy Backend URL',
  'Material (optional)', 'Custom Headers (JSON)', '({{count}}/8)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'China', 'Proxy', 'Start',
  'Diagnose',  // DE: same spelling/meaning as EN — camera diagnostic button label
];

// French cognates — many UI labels overlap with English exactly.
const FR_COGNATES = [
  'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Filament',
  'Filaments', 'Software', 'Hardware', 'Stop', 'Reset', 'Test', 'Code',
  'Token', 'Server', 'Port', 'Plate', 'Layer', 'Active', 'Total', 'Avatar',
  'Job', 'Modal', 'Pin', 'Pro', 'Mini', 'Studio', 'Excellent', 'Description',
  'Action', 'Actions', 'Date', 'Type', 'Cache', 'Service', 'Configuration',
  'Archives', 'Maintenance', 'Notifications', 'Notification', 'Position',
  'Pause', 'Solution', 'Source', 'Version', 'Format', 'Documentation',
  'Mode', 'Format', 'Default', 'Auto', 'Image', 'Audio', 'Video', 'Hex',
  'Camera', 'Avatar', 'Information', 'Initialization', 'Inactive', 'Active',
  'Print', 'Console', 'Cluster', 'Tier', 'Status URL',
  'Smart Plugs', 'Smart Switches', 'Smart Plug', 'High Flow',
  'Material:', 'Default:', 'Name *', '(System)', '(Inv)',
  'Process', 'Service', 'Service', 'Connect', 'Network', 'Local',
  'Sport (124%)', 'Ludicrous (166%)', 'Standard (100%)',
  'STARTTLS (Port 587)', 'SSL/TLS (Port 465)',
  'Bundle', 'Slicer Bundles', 'Imported',
  'Page', 'Note', 'Tare', 'Est.', 'Cloud', 'Style', 'Notes', 'Stock',
  'Accent', 'Orange', 'Global', 'Stable', 'Archive', 'visible', 'minutes',
  'Message', 'Slicer', 'Rotation', 'Original', 'Direction', 'Architecture',
  'notifications', 'Maintenance OK', 'total', 'Provider', 'Token name',
  '{{count}} filament', '{{count}} filaments', '{{count}} permissions',
  '{{count}} downloads', '{{count}} item', '{{count}} selected',
  '({{count}} item)', 'Provisioning...', 'Pressure Advance',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  '({{count}}/8)', 'Custom Headers (JSON)', 'Permissions',
  'Expand dispatch details', 'Collapse dispatch details',
  'Cancelling upload...', 'Backup in progress...', 'Searching directory...',
  'EC984C,#6CD4BC,A66EB9,D87694',
  'Proxy', 'Navigation', 'Budget', 'Commit', 'Designer',
  'ntfy, Pushover, Discord, etc.',
];

// Italian cognates.
const IT_COGNATES = [
  'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Filament',
  'Filaments', 'Software', 'Hardware', 'Stop', 'Reset', 'Test', 'Code',
  'Token', 'Server', 'Port', 'Plate', 'Layer', 'Modal', 'Pin', 'Pro', 'Mini',
  'Studio', 'Cache', 'Service', 'Avatar', 'Slicer', 'Action', 'Actions',
  'Format', 'Modal', 'Login', 'Logout', 'Color', 'Plus', 'Job', 'Live',
  'Position', 'Original', 'Material', 'Cluster', 'Tier', 'Auto', 'Hex',
  'Bundle', 'Slicer Bundles', 'Imported', 'Smart Plugs', 'Smart Switches',
  'Smart Plug', 'High Flow', 'Sport (124%)', 'Ludicrous (166%)',
  'Standard (100%)', 'STARTTLS (Port 587)', 'SSL/TLS (Port 465)',
  'Slot', 'Host', 'File', 'Cloud', 'Admin', 'Silk', '(Inv)', 'Slice',
  'Backup', 'Legacy', 'Branch', 'Auto On', 'Display', 'Password',
  'Auto Off', 'Dashboard', 'Timestamp', 'Pressure Advance', 'Provisioning...',
  '(25%, 50%, 75%)', 'Provider', 'Provider: {{type}}', 'Base: {{name}}',
  'Slicing…', 'Designer', 'Firmware', 'Timelapse', 'Commit', 'Budget',
  '({{count}}/8)', 'Custom Headers (JSON)', 'ETA {{minutes}} min',
  '{{name}} - Timelapse', 'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'Hex: #{{hex}}',
  'EC984C,#6CD4BC,A66EB9,D87694',
  'Proxy', 'Designer',
];

// Japanese: very few cognates because of script difference. Almost
// everything needs translation. Only true loanwords / proper nouns stay.
const JA_COGNATES = [
  'OK', 'Bambu', 'Code',
  'EU (DD/MM/YYYY)', 'US (MM/DD/YYYY)', 'ON, true, 1',
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
];

// Portuguese (BR) cognates.
const PT_BR_COGNATES = [
  'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Filament',
  'Software', 'Hardware', 'Stop', 'Reset', 'Test', 'Code', 'Token', 'Server',
  'Port', 'Plate', 'Layer', 'Modal', 'Pin', 'Pro', 'Mini', 'Studio', 'Cache',
  'Service', 'Avatar', 'Total', 'Active', 'Login', 'Logout', 'Color', 'Hex',
  'Slot', 'Live', 'Rate', 'Host', 'Trend', 'Original', 'Auto', 'Bundle',
  'Imported', 'Action', 'Actions', 'Slicer Bundles', 'Sport (124%)',
  'Ludicrous (166%)', 'Standard (100%)', 'STARTTLS (Port 587)',
  'SSL/TLS (Port 465)', 'Smart Plugs', 'Smart Switches', 'High Flow',
  'Position', 'Mode', 'Setup', 'Modal',
  'Local', 'Metal', 'China', 'Admin', 'Silk', 'Backup', '(Inv)', 'Branch',
  'Normal', 'Material', 'Material:', 'Multicolor', 'Designer', 'Firmware',
  'Timelapse', 'Est.', 'total', 'Commit', 'Global',
  'Base: {{name}}', 'ETA {{minutes}} min', '{{count}} item',
  '{{count}} downloads', '({{count}} item)', '(25%, 50%, 75%)',
  '({{count}}/8)', 'Custom Headers (JSON)', '{{name}} - Timelapse',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'Cancelling upload...', 'EC984C,#6CD4BC,A66EB9,D87694',
  'Expand dispatch details', 'Collapse dispatch details',
  'e.g., Home Assistant, OctoPrint', 'ntfy, Pushover, Discord, etc.',
  'Proxy', 'total: {{minutes}} min',
];

// Chinese (Simplified): very few cognates beyond brand names.
const ZH_CN_COGNATES = [
  'OK', 'Bambu',
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
];

const ZH_TW_COGNATES = [
  'OK', 'Bambu',
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
];

// Spanish cognates — words/phrases that are genuinely identical in Spanish.
const ES_COGNATES = [
  'Error', 'Firmware', 'General', 'Control', 'Total', 'total', 'Material',
  'Material:', 'Color', 'Hex', 'Local', 'Global', 'China', 'Editable',
  'Normal', 'Metal', 'Multicolor', 'Proxy', 'Host', 'Factor', 'Original',
  'Sport (124%)', 'Ludicrous (166%)', 'MakerWorld: {{designer}}',
  '{{printer}}: {{error}}', 'Base: {{name}}',
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}', 'total: {{minutes}} min',
  '({{count}}/8)', 'Hex: #{{hex}}', '(25%, 50%, 75%)',
  'EC984C,#6CD4BC,A66EB9,D87694', 'Est.',
  'ntfy, Pushover, Discord, etc.',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
];

// Turkish cognates — technical UI labels that Turkish speakers use verbatim
// from English (loanwords + acronyms + format strings). Curated, not a shortcut.
const TR_COGNATES = [
  'Filament', 'Firmware', 'Disk', 'Hex', 'Test', 'Port', 'Model', 'Metal',
  'Min', 'Normal', 'Platform', 'Net', 'Trend', 'Commit', 'Global', 'Proxy',
  'N/A', 'email',
  'STARTTLS (Port 587)', 'SSL/TLS (Port 465)',
  '({{count}}/8)', 'Hex: #{{hex}}', 'MakerWorld: {{designer}}',
  '{{count}} filament', '{{printer}}: {{error}}', '{{weight}}g',
  'Filament {{index}} ({{type}})',
  'EC984C,#6CD4BC,A66EB9,D87694',
];

const IDENTICAL_TO_EN_ALLOWED = {
  de: new Set(DE_COGNATES),
  fr: new Set(FR_COGNATES),
  it: new Set(IT_COGNATES),
  ja: new Set(JA_COGNATES),
  es: new Set(ES_COGNATES),
  'pt-BR': new Set(PT_BR_COGNATES),
  'zh-CN': new Set(ZH_CN_COGNATES),
  'zh-TW': new Set(ZH_TW_COGNATES),
  tr: new Set(TR_COGNATES),
};

// Pure comparison logic, exported so tests can verify each failure mode
// without going through file IO or the TypeScript parser.
// Input:  locales = { code: Map<leafKey, leafString> }  (must contain 'en')
// Output: { failed, reports: Array<{ label, items }> }
export function compareLocales(locales) {
  if (!locales.en) throw new Error("compareLocales requires a locales.en entry");
  const reports = [];
  const add = (label, items) => {
    if (items.length) reports.push({ label, items });
  };

  const enKeys = new Set(locales.en.keys());

  // Check 1: key set equality
  for (const [code, map] of Object.entries(locales)) {
    if (code === 'en') continue;
    const keys = new Set(map.keys());
    const missing = [...enKeys].filter((k) => !keys.has(k)).sort();
    const extra = [...keys].filter((k) => !enKeys.has(k)).sort();
    add(`${code}: missing keys vs en`, missing);
    add(`${code}: extra keys vs en`, extra);
  }

  // Check 2: placeholder set equality per leaf
  for (const [code, map] of Object.entries(locales)) {
    if (code === 'en') continue;
    const mismatches = [];
    for (const [key, enValue] of locales.en) {
      const otherValue = map.get(key);
      if (otherValue === undefined) continue;
      const enPlaceholders = new Set((enValue.match(placeholderRe) ?? []));
      const otherPlaceholders = new Set((otherValue.match(placeholderRe) ?? []));
      const missingPh = [...enPlaceholders].filter((p) => !otherPlaceholders.has(p));
      const extraPh = [...otherPlaceholders].filter((p) => !enPlaceholders.has(p));
      if (missingPh.length || extraPh.length) {
        mismatches.push(`${key}: en=${[...enPlaceholders].join(',') || '∅'} vs ${code}=${[...otherPlaceholders].join(',') || '∅'}`);
      }
    }
    add(`${code}: placeholder mismatch vs en`, mismatches);
  }

  // Check 3: plural suffix presence + reverse _one guard
  for (const [code, map] of Object.entries(locales)) {
    if (code === 'en') continue;
    const pluralIssues = [];
    for (const key of enKeys) {
      if (key.endsWith('_plural') && !map.has(key)) pluralIssues.push(`missing _plural key: ${key}`);
      if (key.endsWith('_one') && !map.has(key)) pluralIssues.push(`missing _one key: ${key}`);
      if (key.endsWith('_other') && !map.has(key)) pluralIssues.push(`missing _other key: ${key}`);
    }
    for (const key of map.keys()) {
      if (key.endsWith('_one') && !enKeys.has(key)) {
        pluralIssues.push(`unexpected _one not present in en: ${key}`);
      }
    }
    add(`${code}: plural key mismatch`, pluralIssues);
  }

  // Check 4: identical-to-en leaks. A non-English leaf whose value exactly
  // matches en.ts must either pass the always-allowed heuristic OR be listed
  // in IDENTICAL_TO_EN_ALLOWED[code]. Otherwise it's almost certainly an
  // untranslated English string that slipped through past parity gates.
  for (const [code, map] of Object.entries(locales)) {
    if (code === 'en') continue;
    const allowed = IDENTICAL_TO_EN_ALLOWED[code] ?? new Set();
    const leaks = [];
    for (const [key, enValue] of locales.en) {
      const localeValue = map.get(key);
      if (localeValue === undefined) continue;
      if (localeValue !== enValue) continue;
      if (isAlwaysAllowedIdentical(enValue)) continue;
      if (allowed.has(enValue)) continue;
      const preview = enValue.length > 60 ? `${enValue.slice(0, 57)}...` : enValue;
      leaks.push(`${key}: "${preview}"`);
    }
    add(`${code}: leaves identical to en (untranslated?)`, leaks);
  }

  return { failed: reports.length > 0, reports };
}

// en is the reference locale; every other locale discovered in the locales
// directory is checked identically and a drift in any of them fails CI.
// Skip file IO / process.exit when imported as a library (e.g. from tests).
const isMainModule = import.meta.url === url.pathToFileURL(process.argv[1] ?? '').href;
if (isMainModule) {
  const discovered = fs
    .readdirSync(localesDir)
    .filter((f) => f.endsWith('.ts'))
    .map((f) => f.slice(0, -3))
    .sort();
  if (!discovered.includes('en')) {
    console.error(`No en.ts found in ${localesDir} — cannot run parity check without a reference locale.`);
    process.exit(1);
  }
  const codes = ['en', ...discovered.filter((c) => c !== 'en')];
  const locales = Object.fromEntries(
    codes.map((c) => [c, loadLocale(path.join(localesDir, `${c}.ts`))]),
  );

  const MAX_REPORT = 20;
  const { reports } = compareLocales(locales);

  if (reports.length) {
    console.error(`\n=== Locale parity failures (en is the reference) ===`);
    for (const { label, items } of reports) {
      console.error(`\n[${label}] (${items.length})`);
      items.slice(0, MAX_REPORT).forEach((i) => console.error(`  ${i}`));
      if (items.length > MAX_REPORT) console.error(`  ... and ${items.length - MAX_REPORT} more`);
    }
  }

  console.log('\nLocale leaf counts:');
  for (const [code, map] of Object.entries(locales)) {
    const tier = code === 'en' ? 'ref' : 'locale';
    console.log(`  ${code.padEnd(6)} ${String(map.size).padEnd(6)} [${tier}]`);
  }

  if (reports.length > 0) {
    console.error(`\n❌ i18n parity check failed.`);
    process.exit(1);
  }
  const others = codes.filter((c) => c !== 'en');
  console.log(`\n✓ All locales in parity with en (${others.join(' / ')}).`);
}
