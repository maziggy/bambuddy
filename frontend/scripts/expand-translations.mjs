// Expand a "source-string → per-locale translations" map into the
// per-(locale, dottedKey) shape that apply-translations.mjs consumes.
//
// Usage:  node scripts/expand-translations.mjs <source-table.json> > out.json
//
// Source-table shape (one entry per unique English source string):
//   {
//     "Status": { "de": "Status", "fr": "Statut", "it": "Stato", "ja": "ステータス", "pt-BR": "Status", "zh-CN": "状态", "zh-TW": "狀態" },
//     ...
//   }
//
// Reads the current untranslated set from the live locale files (same logic
// as dump-untranslated.mjs) and outputs:
//   { "de": { "<dottedKey>": "<translation>", ... }, "fr": {...}, ... }

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import url from 'node:url';

const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));
const localesDir = path.resolve(scriptDir, '../src/i18n/locales');
const tsPath = path.resolve(scriptDir, '../node_modules/typescript/lib/typescript.js');
const tsModule = await import(url.pathToFileURL(tsPath).href);
const ts = tsModule.default ?? tsModule;

function collectLeaves(node, prefix, leaves) {
  if (!ts.isObjectLiteralExpression(node)) return;
  for (const prop of node.properties) {
    if (!ts.isPropertyAssignment(prop)) continue;
    let name;
    if (ts.isIdentifier(prop.name)) name = prop.name.text;
    else if (ts.isStringLiteral(prop.name)) name = prop.name.text;
    else continue;
    const p = prefix ? `${prefix}.${name}` : name;
    if (ts.isObjectLiteralExpression(prop.initializer)) {
      collectLeaves(prop.initializer, p, leaves);
    } else if (ts.isStringLiteral(prop.initializer) || ts.isNoSubstitutionTemplateLiteral(prop.initializer)) {
      leaves.set(p, prop.initializer.text);
    }
  }
}
function loadLocale(filePath) {
  const sf = ts.createSourceFile(filePath, fs.readFileSync(filePath, 'utf8'), ts.ScriptTarget.Latest, true);
  const leaves = new Map();
  ts.forEachChild(sf, (n) => { if (ts.isExportAssignment(n)) collectLeaves(n.expression, '', leaves); });
  return leaves;
}

const arg = process.argv[2];
if (!arg) {
  console.error('Usage: node expand-translations.mjs <source-table.json>');
  process.exit(2);
}
const table = JSON.parse(fs.readFileSync(arg, 'utf8'));
const en = loadLocale(path.join(localesDir, 'en.ts'));

const codes = ['de', 'fr', 'it', 'ja', 'pt-BR', 'zh-CN', 'zh-TW'];
const out = Object.fromEntries(codes.map((c) => [c, {}]));
const missingSources = new Set();

for (const code of codes) {
  const map = loadLocale(path.join(localesDir, `${code}.ts`));
  for (const [key, enValue] of en) {
    const localeValue = map.get(key);
    if (localeValue === undefined) continue;
    if (localeValue !== enValue) continue;  // already translated
    const entry = table[enValue];
    if (!entry) { missingSources.add(enValue); continue; }
    const translated = entry[code];
    if (translated === undefined) continue;
    // Always emit, even when translated === enValue, so the apply script can
    // either no-op or replace as needed. (Same value is a no-op via .text check.)
    out[code][key] = translated;
  }
}

if (missingSources.size > 0) {
  process.stderr.write(`\n[warn] ${missingSources.size} source strings not in table:\n`);
  for (const s of [...missingSources].sort()) {
    const preview = s.length > 80 ? s.slice(0, 77) + '...' : s;
    process.stderr.write(`  ${JSON.stringify(preview)}\n`);
  }
}

process.stdout.write(JSON.stringify(out, null, 2));
