// Dump every (locale, dottedKey, enValue) tuple that the parity check would
// flag as "identical to en, not in allow-list, not auto-allowed". Output is
// JSON, structured so apply-translations.mjs can consume the same shape after
// translations are filled in.
//
// Usage:  node scripts/dump-untranslated.mjs > /tmp/untranslated.json
//
// Logic is imported from check-i18n-parity.mjs (compareLocales) so this stays
// in lockstep with the gate.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import url from 'node:url';

const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));
const localesDir = path.resolve(scriptDir, '../src/i18n/locales');
const tsPath = path.resolve(scriptDir, '../node_modules/typescript/lib/typescript.js');
const tsModule = await import(url.pathToFileURL(tsPath).href);
const ts = tsModule.default ?? tsModule;

const { compareLocales } = await import(url.pathToFileURL(path.join(scriptDir, 'check-i18n-parity.mjs')).href);

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

const codes = ['en', 'de', 'fr', 'it', 'ja', 'pt-BR', 'zh-CN', 'zh-TW'];
const locales = Object.fromEntries(codes.map((c) => [c, loadLocale(path.join(localesDir, `${c}.ts`))]));
const { reports } = compareLocales(locales);

// Reports look like: { label: 'de: leaves identical to en (untranslated?)', items: ['<key>: "<value>"', ...] }
// We need to reverse-engineer the (locale, key, enValue) tuples — easier to
// just walk en and check each locale ourselves with the live ALLOWED, which
// is what compareLocales does anyway. So mirror that here.
const en = locales.en;
const out = {};
for (const code of codes) {
  if (code === 'en') continue;
  const map = locales[code];
  const flagged = {};
  for (const r of reports) {
    if (r.label !== `${code}: leaves identical to en (untranslated?)`) continue;
    for (const item of r.items) {
      // Item format:  "<dottedKey>: "<value>""
      const m = item.match(/^(\S+):\s+"(.*)"$/);
      if (!m) continue;
      const key = m[1];
      const enValue = en.get(key);
      if (enValue !== undefined && map.get(key) === enValue) {
        flagged[key] = enValue;
      }
    }
  }
  out[code] = flagged;
}
process.stdout.write(JSON.stringify(out, null, 2));
