// Apply a batch of translations to locale files in-place.
//
// Usage:  node scripts/apply-translations.mjs <translation-file.json>
//
// The translation file is a JSON object shaped like:
//   {
//     "de": { "nav.system": "System", "common.optional": "Optional" },
//     "fr": { "nav.archives": "Archives" },
//     ...
//   }
//
// For each (locale, dottedKey, newValue) entry, the script uses the
// TypeScript parser to locate the leaf at that exact dotted path, then
// rewrites the string literal in place — preserving all other content
// (comments, ordering, formatting, surrounding code) untouched.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import url from 'node:url';

const scriptDir = path.dirname(url.fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, '..');
const localesDir = path.join(frontendDir, 'src/i18n/locales');
const tsPath = path.join(frontendDir, 'node_modules/typescript/lib/typescript.js');

const tsModule = await import(url.pathToFileURL(tsPath).href);
const ts = tsModule.default ?? tsModule;

// Walk the locale's AST, building a map of dottedPath -> string-literal node.
function collectLeafNodes(node, prefix, out) {
  if (!ts.isObjectLiteralExpression(node)) return;
  for (const prop of node.properties) {
    if (!ts.isPropertyAssignment(prop)) continue;
    let name;
    if (ts.isIdentifier(prop.name)) name = prop.name.text;
    else if (ts.isStringLiteral(prop.name)) name = prop.name.text;
    else continue;
    const p = prefix ? `${prefix}.${name}` : name;
    if (ts.isObjectLiteralExpression(prop.initializer)) {
      collectLeafNodes(prop.initializer, p, out);
    } else if (
      ts.isStringLiteral(prop.initializer) ||
      ts.isNoSubstitutionTemplateLiteral(prop.initializer)
    ) {
      out.set(p, prop.initializer);
    }
  }
}

function loadLocaleNodes(filePath) {
  const src = fs.readFileSync(filePath, 'utf8');
  const sf = ts.createSourceFile(filePath, src, ts.ScriptTarget.Latest, true);
  const leaves = new Map();
  ts.forEachChild(sf, (n) => {
    if (ts.isExportAssignment(n)) collectLeafNodes(n.expression, '', leaves);
  });
  return { src, leaves };
}

function literalReplacement(node, newValue) {
  // Re-emit the literal preserving its quote style. Locale files use either
  // single-quoted strings or backtick template-literal-with-no-substitutions.
  const original = node.getText();
  const quote = original.startsWith('`') ? '`' : original[0];  // ' or `
  if (quote === '`') {
    return '`' + newValue.replace(/\\/g, '\\\\').replace(/`/g, '\\`').replace(/\$\{/g, '\\${') + '`';
  }
  const esc = newValue.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  return `'${esc}'`;
}

function applyToLocale(code, map) {
  const file = path.join(localesDir, `${code}.ts`);
  if (!fs.existsSync(file)) {
    throw new Error(`Locale file not found: ${file}`);
  }
  let { src, leaves } = loadLocaleNodes(file);

  // Apply edits in reverse-position order so earlier edits don't shift later positions.
  const edits = [];
  const errors = [];
  let applied = 0;
  let skipped = 0;
  for (const [dottedKey, newValue] of Object.entries(map)) {
    const node = leaves.get(dottedKey);
    if (!node) {
      errors.push(`${code}: key "${dottedKey}" not found in locale file`);
      continue;
    }
    if (node.text === newValue) {
      skipped++;
      continue;
    }
    edits.push({
      start: node.getStart(),
      end: node.getEnd(),
      replacement: literalReplacement(node, newValue),
    });
    applied++;
  }
  edits.sort((a, b) => b.start - a.start);
  for (const e of edits) {
    src = src.slice(0, e.start) + e.replacement + src.slice(e.end);
  }

  if (errors.length) {
    console.error(`\n[${code}] errors:`);
    for (const e of errors) console.error(`  ${e}`);
  }

  if (applied > 0) {
    fs.writeFileSync(file, src, 'utf8');
  }
  console.log(`[${code}] applied=${applied} skipped(same)=${skipped} errors=${errors.length}`);
  return { applied, skipped, errors };
}

async function main() {
  const arg = process.argv[2];
  if (!arg) {
    console.error('Usage: node apply-translations.mjs <translation-file.json>');
    process.exit(2);
  }
  const data = JSON.parse(fs.readFileSync(arg, 'utf8'));
  let totalErrors = 0;
  for (const [code, map] of Object.entries(data)) {
    const { errors } = applyToLocale(code, map);
    totalErrors += errors.length;
  }
  if (totalErrors > 0) {
    console.error(`\n${totalErrors} key(s) failed to apply.`);
    process.exit(1);
  }
}

main();
