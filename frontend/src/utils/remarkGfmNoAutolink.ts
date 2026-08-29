/**
 * GFM markdown support without autolink literals (#2971).
 *
 * This is `remark-gfm` minus one of its five sub-extensions. We compose the
 * pieces by hand rather than importing `remark-gfm`, because the problem is an
 * *import-time* one and no runtime configuration can reach it.
 *
 * `remark-gfm` -> `mdast-util-gfm` -> `mdast-util-gfm-autolink-literal`, whose
 * module body contains this regex literal:
 *
 *     /(?<=^|\s|\p{P}|\p{S})([-.\w+]+)@([-\w]+(?:\.[-\w]+)+)/gu
 *
 * `(?<=` is a lookbehind assertion. Safari shipped lookbehind in **16.4**; it
 * does not exist in iOS 16.0-16.3. A regex literal is validated when its module
 * is *compiled*, not when the enclosing function runs, so on those browsers the
 * module fails to parse. `FolderReadmePanel` -> `FileManagerPage` -> `App` is a
 * plain static import chain, so the regex lands in the entry chunk and the whole
 * bundle fails to compile: every page renders as a blank white screen, not just
 * the File Manager. That is #2971, and it has been true since v1.2.5.
 *
 * Vite cannot help here. esbuild lowers modern *syntax* down to the build
 * target, but it does not rewrite regular expressions - measured: a lookbehind
 * builds silently under `safari15`, `safari16.0` and `es2020` alike. The only
 * fix is to keep the module out of the bundle, which is what this file does.
 *
 * What we keep: tables, strikethrough, task lists, footnotes - the GFM features
 * a folder README actually uses.
 *
 * What we lose: bare `https://example.com` and `foo@example.com` no longer turn
 * themselves into links. Explicit `[text](url)` and `<https://example.com>` are
 * core markdown and still work.
 *
 * Mirrors `remark-gfm@4`'s `lib/index.js` and `gfmFromMarkdown()` in
 * `mdast-util-gfm@3`. Unlike `remark-gfm` we push the four micromark extensions
 * individually instead of pre-combining them, which is equivalent - micromark
 * combines whatever array it is handed - and saves a dependency on
 * `micromark-util-combine-extensions`.
 *
 * Unlike `remark-gfm` this registers only the *parse* half. `remark-stringify`
 * is not a dependency of this project - nothing here serialises an mdast tree
 * back to markdown - and without it TypeScript cannot even see the
 * `toMarkdownExtensions` field, since that is a module augmentation
 * `remark-stringify` contributes. If you ever add `remark-stringify`, add the
 * matching `gfm*ToMarkdown()` extensions here too, or GFM tables and task lists
 * will serialise back out as plain paragraphs with no error.
 *
 * `frontend/scripts/check-browser-baseline.mjs` fails the build if a lookbehind
 * ever reaches the bundle again.
 */

import { gfmFootnoteFromMarkdown } from 'mdast-util-gfm-footnote';
import { gfmStrikethroughFromMarkdown } from 'mdast-util-gfm-strikethrough';
import { gfmTableFromMarkdown } from 'mdast-util-gfm-table';
import { gfmTaskListItemFromMarkdown } from 'mdast-util-gfm-task-list-item';
import { gfmFootnote } from 'micromark-extension-gfm-footnote';
import { gfmStrikethrough } from 'micromark-extension-gfm-strikethrough';
import { gfmTable } from 'micromark-extension-gfm-table';
import { gfmTaskListItem } from 'micromark-extension-gfm-task-list-item';
import type { Processor } from 'unified';

// Side-effect type import: `remark-parse` is what augments unified's `Data`
// with `micromarkExtensions` / `fromMarkdownExtensions`. `remark-gfm` does the
// same thing for the same reason.
import type {} from 'remark-parse';

/**
 * Drop-in for `remark-gfm`, minus autolink literals. Takes no options; the one
 * call site wants GFM defaults, and `remark-gfm`'s options only tune
 * strikethrough/table/footnote behaviour we do not override.
 */
export default function remarkGfmNoAutolink(this: Processor): undefined {
  const data = this.data();

  const micromarkExtensions = data.micromarkExtensions || (data.micromarkExtensions = []);
  const fromMarkdownExtensions = data.fromMarkdownExtensions || (data.fromMarkdownExtensions = []);

  micromarkExtensions.push(gfmFootnote(), gfmStrikethrough(), gfmTable(), gfmTaskListItem());

  fromMarkdownExtensions.push(
    gfmFootnoteFromMarkdown(),
    gfmStrikethroughFromMarkdown(),
    gfmTableFromMarkdown(),
    gfmTaskListItemFromMarkdown(),
  );
}
