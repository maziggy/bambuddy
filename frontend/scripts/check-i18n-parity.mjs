// Verifies parity across locale files (en / zh-CN / zh-TW):
//   1. Leaf-key sets are identical
//   2. Each leaf's {{placeholder}} set is identical
//   3. Plural suffixes: every en key ending in _plural / _one / _other must
//      exist in every other locale, and other locales must not introduce an
//      _one key that en does not have.
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

  return { failed: reports.length > 0, reports };
}

// Strict locales fail CI when they drift from en. Everything else discovered
// in the locales directory is reported informationally — promote a locale to
// STRICT once its drift is caught up. en is implicitly the reference.
const STRICT = ['de', 'zh-CN', 'zh-TW'];

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
  const missingStrict = STRICT.filter((c) => !discovered.includes(c));
  if (missingStrict.length) {
    console.error(`STRICT locales declared but not found on disk: ${missingStrict.join(', ')}`);
    process.exit(1);
  }
  const codes = ['en', ...discovered.filter((c) => c !== 'en')];
  const locales = Object.fromEntries(
    codes.map((c) => [c, loadLocale(path.join(localesDir, `${c}.ts`))]),
  );

  const MAX_REPORT = 20;
  const strictSet = new Set(STRICT);
  const printReports = (reports, header) => {
    if (!reports.length) return;
    console.error(`\n${header}`);
    for (const { label, items } of reports) {
      console.error(`\n[${label}] (${items.length})`);
      items.slice(0, MAX_REPORT).forEach((i) => console.error(`  ${i}`));
      if (items.length > MAX_REPORT) console.error(`  ... and ${items.length - MAX_REPORT} more`);
    }
  };

  // Label prefix is "${code}:" — route reports to strict vs informational.
  const { reports } = compareLocales(locales);
  const codeOf = (label) => label.split(':', 1)[0];
  const strictReports = reports.filter((r) => strictSet.has(codeOf(r.label)));
  const infoReports = reports.filter((r) => !strictSet.has(codeOf(r.label)));

  printReports(strictReports, '=== STRICT locales (failures below fail CI) ===');
  // Informational locales: show per-category drift counts only, not the
  // full key lists — the leaf-count table below already gives the overall
  // picture. Flip VERBOSE_INFO=1 to dump the full missing-key/placeholder
  // reports when actually working on translations.
  if (infoReports.length) {
    if (process.env.VERBOSE_INFO === '1') {
      printReports(infoReports, '=== INFORMATIONAL locales (drift shown, does not fail CI) ===');
    } else {
      console.error('\n=== INFORMATIONAL locales (drift summary; VERBOSE_INFO=1 for detail) ===');
      for (const { label, items } of infoReports) {
        console.error(`  ${label}: ${items.length}`);
      }
    }
  }

  console.log('\nLocale leaf counts:');
  for (const [code, map] of Object.entries(locales)) {
    const tier = code === 'en' ? 'ref' : strictSet.has(code) ? 'strict' : 'info';
    console.log(`  ${code.padEnd(6)} ${String(map.size).padEnd(6)} [${tier}]`);
  }

  if (strictReports.length > 0) {
    console.error(`\n❌ i18n parity check failed (strict: ${STRICT.join(', ')}).`);
    process.exit(1);
  }
  console.log(`\n✓ Strict locales in parity (en / ${STRICT.join(' / ')}).`);
}
