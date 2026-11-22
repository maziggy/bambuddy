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
//   5. Every literal dotted key passed to t() in frontend/src (tests
//      excluded) exists in en.ts (#3631).
//   6. No hardcoded English UI text in .tsx files: JSX text, shown props and
//      showToast() messages go through t() (#3638).
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
  if (/^\{\{[^}]+\}\}([\s/\-–·,]+\{\{[^}]+\}\})+$/.test(value)) return true;  // placeholders joined by punctuation only ({{a}} / {{b}})
  if (/^[0-9a-fA-F]{6}$/.test(value)) return true;      // bare hex color
  if (/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i.test(value)) return true;  // email
  if (/^https?:\/\//.test(value)) return true;          // URL
  if (/^ON,\s+true,\s+1$/.test(value)) return true;     // literal example "ON, true, 1"
  // Brand / technical names that ship verbatim everywhere.
  if (/^(Bambuddy|BamBuddy|Obico|OctoEverywhere|SpoolBuddy|Bambu Lab|Bambu Studio|Bambu Studio 2\.6\+|Bambu Studio sidecar URL|OrcaSlicer|OrcaSlicer sidecar URL|MakerWorld|Manyfold|Spoolman|\(Spoolman\)|Spoolman URL|Tailscale|Python|Ethernet|GitHub|GitLab|Gitea|Forgejo|Discord|MQTT|FTP|HTTPS?|JSON|YAML|RTSP|TLS|SSL|CSRF|OIDC|SSO|SSO \/ OIDC|LDAP|TOTP|2FA|MFA|API|AMS|CRC|SHA256|SHA-256|kWh|MB|GB|KB|RGBA?|HSL|RGB|UTC|ISO|UI|HTTP|HTTP Method|H2D|H2D Pro|X1C|X1E|P1S|P1P|A1|A1 Mini|H2C|N3F|N3S|PETG|PLA|ABS|PA|TPU|PEI|PA-CF|PVA|HIPS|ASA|PC|PETG-HF|G\.code|G-code|gcode|cm³|°C|°F|GCODE|SOURCE|ntfy|Notify!|Pushover|Bark|Gotify|Telegram|Webhook|Webhook URL|Home Assistant|Home Assistant URL|CallMeBot\/WhatsApp|Bambuddy URL|Cool Plate|Cool Plate SuperTack|Engineering Plate|High Temp Plate|Smooth PEI Plate|Smooth PEI Plate \/ High Temp Plate|Textured PEI Plate|Ext-L|Ext-R|ISO \(YYYY-MM-DD\))$/.test(value)) return true;
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
  'Tag: {{tag}}', 'Original:', 'URL *', 'Name (A-Z)', 'Name (Z-A)',  // #3638: same in this language
  'Hostname', 'Backend', 'K-Profile', 'Kernel',  // #3631: same word in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'Apps',  // notifications badge for messages from connected apps — same word
  '{{ams}} · Slot {{slot}}',  // #2587 runout slot label — "Slot" is the DE term too
  'Auto',  // calibrationMode_auto — German UI uses the loanword (matches BambuStudio DE)
  'Name', 'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Modus',
  'Stop', 'Reset', 'Test', 'Code', 'Token', 'Server', 'Port', 'Bug', 'Job',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name

  'Pause', 'Power', 'System', 'Problem', 'Designer', 'Extruder', 'Firmware',
  'Material', 'Original', 'Position', 'Webhook', 'Workflow', 'Slicer',
  'Pipeline', 'Pipelines', 'Filament {{n}}',  // #1425 — Slicer Pipelines (DE)
  'parallel',  // #1425 PR C polish — "parallel" is the same word in German
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
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
  'Score',  // #1546 AI detection modal — established DE loanword (Duden)
  'Version 2',  // stream overlay artwork picker (#3177) — same word in German
];

// French cognates — many UI labels overlap with English exactly.
const FR_COGNATES = [
  '{{count}} archive', '{{count}} archives', '(inactive)', 'URL *', '{{count}} tag', '{{count}} tags', 'Photo {{number}}', 'Timelapse', 'Images ({{count}})',  // #3638: same in this language
  'Application', 'Backend',  // #3631: same word in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'Apps',  // notifications badge for messages from connected apps — same word
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name
  'Status', 'Tag', 'Tags', 'Online', 'Offline', 'Standard', 'Filament',
  'Filaments', 'Software', 'Hardware', 'Stop', 'Reset', 'Test', 'Code',
  'Token', 'Server', 'Port', 'Plate', 'Layer', 'Active', 'Total', 'Avatar',
  'Job', 'Modal', 'Pin', 'Pro', 'Mini', 'Studio', 'Excellent', 'Description',
  'Pipeline', 'Pipelines', 'Filament {{n}}',  // #1425 — Slicer Pipelines (FR)
  'Copies', '{{n}} copies', 'max {{n}}',  // #1425 PR C — French uses these forms verbatim
  'round robin',  // borrowed English term used as-is in French tech contexts
  'Action', 'Actions', 'Date', 'Type', 'Cache', 'Service', 'Configuration',
  'Score',  // #1546 AI detection modal — "le score" is standard French
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
  '{{name}} ({{count}} copies)',  // FR plural of "copie" is also "copies"
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  '({{count}}/8)', 'Custom Headers (JSON)', 'Permissions',
  'Expand dispatch details', 'Collapse dispatch details',
  'Cancelling upload...', 'Backup in progress...', 'Searching directory...',
  'EC984C,#6CD4BC,A66EB9,D87694',
  'Proxy', 'Navigation', 'Budget', 'Commit', 'Designer',
  'Compact',  // cam-wall status overlay mode — same word in French
  'ntfy, Pushover, Discord, etc.',
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
  'Simple', 'Expert',  // slicer settings visibility tiers — identical words in French
  'Support',  // same word in French
  'Photos', '{{count}} photo', '{{count}} photos',  // file details photo strip (#3077) — same word in French
  'Version 2',  // stream overlay artwork picker (#3177) — same word in French
  'Page {{page}}',  // Manyfold model list pager (#1471) — "page" is the same word in French
  'Alternatives',  // #3462 multi-file print mode label — same word in French
];

// Italian cognates.
const IT_COGNATES = [
  'WiFi: {{signal}} dBm - {{strength}}', 'Tag: {{tag}}', '{{speed}}x (output: {{duration}})', 'Output:', 'URL *', '{{count}} tag',  // #3638: same in this language
  'Database', 'Backend', 'Kernel',  // #3631: same word in this language
  'Account',  // #3602 account menu — the Italian word for a user account
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  '{{ams}} · Slot {{slot}}',  // #2587 runout slot label — "Slot" is the IT term too
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name
  'Email',  // common loanword in Italian, used verbatim in UI labels
  'Pipeline', 'slicing',  // #1425 — Slicer Pipelines (cognate in IT)
  'max {{n}}',  // #1425 PR C — same form in Italian (max + number)
  'round robin',  // borrowed English term used as-is in Italian tech contexts
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
  'Off',  // cam-wall status overlay mode — common loanword in Italian UI
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
  'Skirt / brim',  // Italian slicer UIs keep the English terms
];

// Japanese: very few cognates because of script difference. Almost
// everything needs translation. Only true loanwords / proper nouns stay.
const JA_COGNATES = [
  'WiFi: {{signal}} dBm - {{strength}}', 'URL *',  // #3638: same in this language
  'OK', 'Bambu', 'Code',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'EU (DD/MM/YYYY)', 'US (MM/DD/YYYY)', 'ON, true, 1',
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
];

// Portuguese (BR) cognates.
const PT_BR_COGNATES = [
  'Original:', 'URL *', '{{count}} tag', '{{count}} tags',  // #3638: same in this language
  'total: {{cost}}', 'Backend', 'Kernel',  // #3631: same word in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'Apps',  // notifications badge for messages from connected apps — same word
  '{{ams}} · Slot {{slot}}',  // #2587 runout slot label — "Slot" is the PT-BR term too
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name
  'Pipeline', 'Pipelines',  // #1425 — Slicer Pipelines (PT-BR)
  'round robin',  // borrowed English term used as-is in Portuguese tech contexts
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
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
];

// Chinese (Simplified): very few cognates beyond brand names.
const ZH_CN_COGNATES = [
  'URL *',  // #3638: same in this language
  'OK', 'Bambu',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
];

const ZH_TW_COGNATES = [
  'URL *',  // #3638: same in this language
  'OK', 'Bambu',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  '({{count}}/8)', 'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
];

// Korean: script difference means almost nothing is identical.
// Allow loanwords/acronyms, format strings, and proper nouns that stay verbatim.
const KO_COGNATES = [
  'WiFi: {{signal}} dBm - {{strength}}', 'URL *',  // #3638: same in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'OK', 'Bambu', 'N/A',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  '({{count}}/8)', '(25%, 50%, 75%)',
  'Custom Headers (JSON)',
  'Box label (62 × 29 mm)',
  'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
  'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '{{weight}}g',                                      // unit suffix format string
  'MakerWorld: {{designer}}',                         // brand + placeholder
  'email',                                            // OIDC claim name placeholder
  '{{printer}}: {{error}}',                           // pure placeholders
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}', // pure placeholders
  'Obico ML API URL',                                 // product name (Obico)
  '{{filament}} @ {{temp}}°C',                        // drying badge format
];

// Spanish cognates — words/phrases that are genuinely identical in Spanish.
const ES_COGNATES = [
  'WiFi: {{signal}} dBm - {{strength}}', '{{count}} error', 'Original:', 'URL *', 'ERROR: {{error}}',  // #3638: same in this language
  'Personal', 'total: {{cost}}', 'Backend', 'Kernel',  // #3631: same word in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'Apps',  // notifications badge for messages from connected apps — same word
  '{{ams}} · Slot {{slot}}',  // #2587 runout slot label — "Slot" is the ES term too
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name
  'Pipeline', 'Pipelines',  // #1425 — Slicer Pipelines (ES)
  'round robin',  // borrowed English term used as-is in Spanish tech contexts
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
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
  'Simple',  // slicer settings visibility tier — identical word in Spanish
];

// Swedish cognates — technical UI labels that Swedish speakers use verbatim
// from English (loanwords + acronyms + format strings). Curated, not a shortcut.
const SV_COGNATES = [
  'Original:', 'URL *', 'Full', 'Cache',  // #3638: same in this language
  'Max', 'Backend',  // #3631: same word in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'MakerWorld: {{designer}}',
  'email',
  '{{printer}}: {{error}}',
  'Bambu Cloud',
  'Orca Cloud',
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '({{count}}/8)',
  '(25%, 50%, 75%)', 'System', 'Filament', 'Status', 'Total budget', 'Budget', 'Offline', 'Problem', 'Diameter', 'Standard',
  'Live', 'Firmware', '{{filament}} @ {{temp}}°C', 'Standard (100%)', 'Sport (124%)', '{{weight}}g', 'Pipeline', 'Pipelines', 'Position', 'Batch', 'Timelapse', 'Online',
  'Disk', 'STARTTLS (Port 587)', 'SSL/TLS (Port 465)', 'Start', 'Auto', 'Admin', 'Data', 'Material', 'Hex', 'Accent', 'Neutral', 'Orange', 'Rotation', 'Test',
  'Port', '(System)', 'Autologin', 'Process', 'Filament {{n}}', 'Region', 'Global', 'Normal', 'Version', 'max {{n}}', 'Expert', 'Filament {{index}} ({{type}})', 'Material:',
  '(Inv)', 'Original', 'Commit', 'Extruder', 'Gradient', 'Proxy', 'Metadata', '{{count}} filament', 'Temp', 'Min', 'ntfy, Pushover, Discord, etc.', 'Hex: #{{hex}}', 'Designer',
  'Prefix', 'Trend', 'max(global {{global}}d, SKU {{sku}}d)', 'Slicer',
  'Version 2',  // stream overlay artwork picker (#3177) — same word in Swedish
];

// Turkish cognates — technical UI labels that Turkish speakers use verbatim
// from English (loanwords + acronyms + format strings). Curated, not a shortcut.
const TR_COGNATES = [
  'URL *', '~{{weight}}g filament',  // #3638: same in this language
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  '{{ams}} · Slot {{slot}}',  // #2587 runout slot label — "Slot" is the TR term too
  'Filament', 'Firmware', 'Disk', 'Hex', 'Test', 'Port', 'Model', 'Metal',
  'Bambu Cloud', 'Orca Cloud',  // brand names — same in every locale
  'AMS Filament Backup',  // Bambu Lab product/firmware feature name
  'Pipeline', 'Filament {{n}}',  // #1425 — Slicer Pipelines (TR)
  'Min', 'Normal', 'Platform', 'Net', 'Trend', 'Commit', 'Global', 'Proxy',
  'N/A', 'email',
  'STARTTLS (Port 587)', 'SSL/TLS (Port 465)',
  '({{count}}/8)', 'Hex: #{{hex}}', 'MakerWorld: {{designer}}',
  '{{count}} filament', '{{printer}}: {{error}}', '{{weight}}g',
  'Filament {{index}} ({{type}})',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '{{filament}} @ {{temp}}°C',  // drying badge: filament code + universal °C
];

const RU_COGNATES = [
  'URL *',  // #3638: same in this language
  'MakerWorld: {{designer}}',
  'email',
  '{{printer}}: {{error}}',
  'Bambu Cloud',
  'Orca Cloud',
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '({{count}}/8)',
  '(25%, 50%, 75%)',
];

// Ukrainian keeps only Latin brand names and placeholder/punctuation-only
// formats verbatim; every prose string in uk.ts is translated.
const UK_COGNATES = [
  'URL *',  // #3638: same in this language
  'MakerWorld: {{designer}}',                         // brand + placeholder
  '{{printer}}: {{error}}',                           // pure placeholders
  'Bambu Cloud', 'Orca Cloud',                        // brand names — same in every locale
  'Sport (124%)', 'Ludicrous (166%)',                 // official Bambu speed-mode names
  'email',                                            // OIDC claim name placeholder
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '({{count}}/8)',
  '(25%, 50%, 75%)',
];

// Dutch cognates. Dutch shares most technical UI vocabulary with English
// verbatim -- printer, filament, status, nozzle, timelapse, dashboard --
// and the slicer feature names (support, ironing, prime tower, gap fill)
// are used untranslated by Dutch slicer users. Each entry below was
// checked individually against the Dutch translation in #2891.
const NL_COGNATES = [
  '{{day}} {{hour}}: {{count}} print', '{{day}} {{hour}}: {{count}} prints', 'AMS {{amsId}} T{{trayId}} · Printer #{{printerId}}', 'Tag: {{tag}}', 'Printer {{id}}', 'URL *', '{{count}} tag', '{{count}} tags', 'Cache', '~{{weight}}g filament', '{{date}}: {{count}} print', '{{date}}: {{count}} prints',  // #3638: same in this language
  'Database', 'Backend', 'In AMS', 'Kernel',  // #3631: same word in this language
  'Account',  // #3602 account menu — the Dutch word for a user account
  '{{page}} · {{width}} × {{height}} mm · {{capacity}} labels ({{cols}} × {{rows}})',  // #3296 custom sheet summary — "labels" is the Dutch word too
  'A4 (210 × 297 mm)',  // #3296 page size name in the custom label sheet form — ISO name and metric size, written the same
  'Project:',  // archive page label
  'Apps',  // notifications badge for messages from connected apps — same word
  '1 printer', '{{n}} printers',
  '1 week', '(25%, 50%, 75%)', 'Accent', 'AMS Filament Backup',
  '{{ams}} Slot {{slot}}', 'Auto', 'Auto Home', 'Bambu Cloud',
  'Batch', 'Batches', 'Branch', 'Budget',
  'Camera', 'China', 'Client Secret', 'Cloud',
  'Commit', 'Compact', '({{count}}/8)', '{{count}} downloads',
  '{{count}} filament', '({{count}} item)', '{{count}} item', '({{count}} items)',
  '{{count}} items', '{{count}} object', 'Dashboard', 'Diagnose',
  'Diameter', 'EC984C,#6CD4BC,A66EB9,D87694', 'Effect', 'email',
  'ETA {{minutes}} min', 'Expert', 'Extruder', 'Extruders',
  'Factor', 'Filament', 'Filament {{index}} ({{type}})', 'Filament {{n}}',
  '{{filament}} @ {{temp}}°C', 'Filters', 'Firmware', 'Flow',
  'Galaxy', 'Gap fill', 'Hex', 'Hex: #{{hex}}',
  'Host', 'In browser', 'Interval', 'Interval (min)',
  'Interval ({{type}})', 'Ironing', 'Jade White, Fire Red...', 'Label',
  'Label...', 'LAN Developer Mode', 'Live', 'Long-Lived Access Token',
  'Ludicrous (166%)', 'MakerWorld: {{designer}}', 'Metadata', 'Min',
  'Model', '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}', '{{name}} - Timelapse', 'Nozzle',
  'Nozzle 2', 'Nozzle {{n}}', '{{n}} run', '{{n}} runs', 'Offline',
  'Online', 'Open', 'Orca Cloud', 'parallel',
  'Personal Access Token', 'Pipeline', 'Pipelines', 'Platform',
  'Pressure Advance', 'Prime tower', 'Printer', 'Printer:',
  '{{printer}}: {{error}}', '{{printer}} is offline', 'Printer is offline', 'Printer offline',
  'Printers', 'Project', 'Project: {{name}}', 'Protocol',
  'Providers', 'Provider: {{type}}', 'Proxy', 'Recent',
  'Records', 'round robin', 'Scopes', 'Score',
  'sets', 'Skirt / brim', 'Slicer', 'SpoolBuddy is offline',
  'Sport (124%)', 'Start', 'Status', 'Subtype',
  'Support', 'Tag', 'Tags', 'Temp',
  'Timelapse', 'Topic', 'Trend', 'Type',
  'Type:', 'Updates', 'Uptime', 'Urgent',
  'Warm', '{{weight}}g', 'Workflow',
];

// Polish cognates — brand names, acronyms, format strings and the technical
// words Polish UIs use verbatim. Checked against pl.ts entry by entry.
const PL_COGNATES = [
  '{{count}} tag',  // #3638: same in this language
  'Backend',  // #3631: same word in this language
  'MakerWorld: {{designer}}',
  '{{printer}}: {{error}}',
  'Bambu Cloud', 'Orca Cloud',
  'Sport (124%)', 'Ludicrous (166%)',
  'email',
  '{{name}} — {{stage}} ({{percent}}%) — {{elapsed}}',
  'EC984C,#6CD4BC,A66EB9,D87694',
  '({{count}}/8)',
  '(25%, 50%, 75%)',

  // Loanwords, brands, Bambu's own plate/finish names, acronyms, placeholders:
  'Filament', 'MakerWorld', 'System', 'Status', 'Model', 'Offline', 'Problem', 'Firmware',
  '{{filament}} @ {{temp}}°C', 'L', '{{ams}} · Slot {{slot}}', 'GCODE', '{{weight}}g',
  '{{name}} - Timelapse', 'SJF', 'G-code', 'Timelapse', 'SpoolBuddy', 'LDAP', 'SSO / OIDC', 'Online', 'NFC',
  'Spoolman', 'Auto', 'Slicer', 'Hex', 'ISO (YYYY-MM-DD)', 'Bambu Studio', 'OrcaSlicer',
  'http://192.168.1.61:1984/api/frame.jpeg?src=printer', 'Home Assistant', 'Port', 'OK', 'Client ID',
  'Client Secret', 'Filament {{n}}', '{{shown}} / {{total}}', '000000', 'XXXXXXXX', 'user@example.com',
  'Branding', 'Pressure Advance', 'Region', 'README', 'https://printables.com/model/...', 'URL',
  'https://makerworld.com/...', 'Filament {{index}} ({{type}})', 'Cool Plate', 'Cool Plate SuperTack',
  'Engineering Plate', 'Smooth PEI Plate / High Temp Plate', 'Textured PEI Plate', '(Spoolman)', 'Slot',
  'Jade White, Fire Red...', 'Galaxy', 'Gradient', 'AMS', '?', 'K', 'https://github.com/username/repo-name',
  'https://gitea.example.com/username/repo-name', 'https://forgejo.example.com/username/repo-name',
  'https://gitlab.com/username/repo-name', 'GitHub', 'GitLab', 'Gitea', 'Forgejo', 'Commit', '0.020',
  'Proxy', 'SHA-256', '{{count}} filament', 'ON, true, 1', 'Live Activities (iOS)', 'Notify!',
  'CallMeBot/WhatsApp', 'ntfy', 'Pushover', 'Telegram', 'Discord', 'Webhook', 'Bark', 'Gotify', 'Start',
  'Test', 'Hex: #{{hex}}', 'BamBuddy', '-', 'Tag', 'Host', 'Manyfold', 'http://192.168.1.10:3214',
  'Skirt / brim', 'Folder', 'SKU', 'Trend', 'ROP', 'SS', '{{count}} alert', 'CSV',
  'A4 (210 × 297 mm)', 'Letter',  // paper formats, as Polish names them
];

const IDENTICAL_TO_EN_ALLOWED = {
  de: new Set(DE_COGNATES),
  fr: new Set(FR_COGNATES),
  it: new Set(IT_COGNATES),
  ja: new Set(JA_COGNATES),
  ko: new Set(KO_COGNATES),
  es: new Set(ES_COGNATES),
  'pt-BR': new Set(PT_BR_COGNATES),
  'zh-CN': new Set(ZH_CN_COGNATES),
  'zh-TW': new Set(ZH_TW_COGNATES),
  sv: new Set(SV_COGNATES),
  tr: new Set(TR_COGNATES),
  ru: new Set(RU_COGNATES),
  uk: new Set(UK_COGNATES),
  nl: new Set(NL_COGNATES),
  pl: new Set(PL_COGNATES),
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

// Check 5 (#3631): every literal key the code passes to t() must exist in
// en.ts. The checks above only compare the locale files with each other, so a
// key that no locale has slipped through: with an inline fallback,
// t('a.b', 'Text'), every language showed the English text; without one, the
// UI showed the raw key. Only literal dotted keys are checked; keys built at
// runtime (template literals, variables) cannot be resolved statically.
const codeKeyRe = /\bt\(\s*(['"])([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)\1/g;

// Input:  sources = Array<{ file, text }>, enKeys = Set<leafKey>
// Output: sorted "file: key" strings for keys en.ts does not have.
export function findMissingCodeKeys(sources, enKeys) {
  const prefixes = new Set();
  for (const key of enKeys) {
    const parts = key.split('.');
    for (let i = 1; i < parts.length; i++) prefixes.add(parts.slice(0, i).join('.'));
  }
  const exists = (key) =>
    enKeys.has(key) || // also the base of an old-style key / key_plural pair
    enKeys.has(`${key}_other`) || // key_one / key_other always come as a pair
    prefixes.has(key); // t(key, { returnObjects: true }) reads a subtree
  const missing = new Set();
  for (const { file, text } of sources) {
    for (const m of text.matchAll(codeKeyRe)) {
      if (!exists(m[2])) missing.add(`${file}: ${m[2]}`);
    }
  }
  return [...missing].sort();
}

// Check 6 (#3638): no hardcoded English in the UI. Flags, in .tsx files, JSX
// text and the literal value of props whose text is shown (title, placeholder,
// aria-label, alt, label), plus literal messages passed to showToast(). Text
// that is the same in every language is not flagged: brand and technical names
// (isAlwaysAllowedIdentical), strings without a word in them, and values that
// look technical (paths, URLs, hosts, identifiers, example values). Anything
// else that must stay verbatim goes into HARDCODED_ALLOWED by exact value.
const SHOWN_PROPS = new Set(['title', 'placeholder', 'aria-label', 'alt', 'label']);
const HARDCODED_ALLOWED = new Set([
  // product, brand and model names
  'AMS 2 Pro', 'AMS-HT', 'BIQU Panda Breath', 'Google', 'Panda Breath', 'Polymaker', 'Swagger UI',
  'Tasmota', 'X1 Carbon',
  // units and formats
  'dBm', 'dBm)', 'MP3, WAV, M4A, AAC, OGG', '3MF',
  // example values in placeholders (a Bambu colour name, filament subtypes, a token, OIDC scopes)
  'Jade White', 'Basic, Matte, Silk...', 'eyJ...', 'eyJ0eXAiOiJKV1QiLC...', 'openid email profile',
  // ForecastPanel's lead-time formula
  'max(g:', ', sku:',
]);

function isTechnicalText(value) {
  // HTML entities and the punctuation around a unit ("GB)", "kWh ·") carry no words
  const v = value.replace(/&[a-z]+;/g, ' ').replace(/^[\s\W]+|[\s\W]+$/g, '');
  if (!/[A-Za-z]{2,}/.test(v)) return true; // numbers, symbols, single letters
  if (isAlwaysAllowedIdentical(v)) return true;
  if (/[/_@={}#\\]|https?:|\w\.\w|\d\.\d|^[a-z0-9.-]+$|^-{1,2}[a-z]/.test(v)) return true;
  return false;
}

// Input:  sources = Array<{ file, text }> (.tsx files are scanned)
// Output: sorted "file:line: kind "value"" strings
export function findHardcodedStrings(sources, allowed = HARDCODED_ALLOWED) {
  const found = [];
  for (const { file, text } of sources) {
    if (!file.endsWith('.tsx')) continue;
    const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const report = (node, kind, raw) => {
      const value = raw.replace(/\s+/g, ' ').trim();
      if (!value || isTechnicalText(value) || allowed.has(value)) return;
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
      found.push(`${file}:${line}: ${kind} "${value}"`);
    };
    const literal = (node) =>
      node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : null;
    const visit = (node) => {
      if (ts.isJsxText(node)) {
        report(node, 'text', node.text);
      } else if (ts.isJsxAttribute(node) && SHOWN_PROPS.has(node.name.getText(sf)) && node.initializer) {
        const init = node.initializer;
        const value = literal(init) ?? (ts.isJsxExpression(init) ? literal(init.expression) : null);
        if (value !== null) report(node, node.name.getText(sf), value);
      } else if (ts.isCallExpression(node) && node.expression.getText(sf) === 'showToast') {
        const arg = node.arguments[0];
        if (arg && ts.isTemplateExpression(arg)) {
          // `Failed: ${e}` -> "Failed: …" (the interpolations would read as technical)
          report(node, 'showToast', arg.head.text + arg.templateSpans.map((s) => `…${s.literal.text}`).join(''));
        }
        else if (literal(arg) !== null) report(node, 'showToast', literal(arg));
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return found.sort();
}

function collectSources(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== '__tests__' && entry.name !== 'i18n') collectSources(p, out);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push({ file: path.relative(frontendDir, p), text: fs.readFileSync(p, 'utf8') });
    }
  }
  return out;
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
  const sources = collectSources(path.join(frontendDir, 'src'));
  const missingCodeKeys = findMissingCodeKeys(sources, new Set(locales.en.keys()));
  if (missingCodeKeys.length) {
    reports.push({ label: 'keys used in code but missing from en.ts', items: missingCodeKeys });
  }
  const hardcoded = findHardcodedStrings(sources);
  if (hardcoded.length) {
    reports.push({ label: 'hardcoded UI text, use t() (or HARDCODED_ALLOWED if it is the same in every language)', items: hardcoded });
  }

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
