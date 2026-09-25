import { createRequire } from 'node:module'
import { readdirSync, readFileSync } from 'node:fs'
import { defineConfig, minify, transformWithOxc } from 'vite'
import type { Plugin, ResolvedConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Backend port for dev server proxy (default: 8000)
const backendPort = process.env.BACKEND_PORT || '8000'
const backendUrl = `http://localhost:${backendPort}`

// pdf.js keeps these out of its bundle and fetches them at runtime (#2976):
// CJK text needs the CMaps, non-embedded fonts the standard font files, and
// JPEG2000/JBIG2 images and ICC colour the wasm decoders. Published under the
// bundle's own assets directory so the existing /assets mount serves them —
// PdfPreviewModal builds the matching URLs from its PDFJS_ASSET_BASE.
const PDFJS_RUNTIME_DIRS = ['cmaps', 'iccs', 'standard_fonts', 'wasm']
const PDFJS_RUNTIME_PREFIX = 'assets/pdfjs'

/**
 * Lower emitted script *assets* to `build.target`.
 *
 * `build.target` only governs modules the bundler compiles. A file pulled in
 * with `?url` is copied through verbatim, which is how pdf.js's worker kept
 * its `static {}` blocks (Safari 16.4+) while the build reported a clean
 * baseline (#2976). pdf.js's own `legacy/` build ships them too, so the
 * lowering has to happen here.
 */
function lowerEmittedScriptAssets(): Plugin {
  let config: ResolvedConfig

  return {
    name: 'bambuddy:lower-emitted-script-assets',
    apply: 'build',
    configResolved(resolved) {
      config = resolved
    },
    async generateBundle(_options, bundle) {
      const target = config.build.target
      if (!target) return
      for (const [fileName, output] of Object.entries(bundle)) {
        // pdf.js publishes its wasm fallbacks ready for our baseline; they are
        // asm.js-shaped emscripten output that nothing here should rewrite.
        if (output.type !== 'asset' || fileName.startsWith(`${PDFJS_RUNTIME_PREFIX}/`)) continue
        if (!/\.[cm]?js$/.test(fileName)) continue
        const source =
          typeof output.source === 'string' ? output.source : Buffer.from(output.source).toString('utf8')
        const lowered = await transformWithOxc(source, fileName, { target })
        output.source = config.build.minify
          ? (await minify(fileName, lowered.code, { module: !fileName.endsWith('.cjs') })).code
          : lowered.code
      }
    },
  }
}

/** Publish pdf.js's runtime data directories next to the bundle. */
function pdfjsRuntimeAssets(): Plugin {
  const require = createRequire(__filename)
  const packageDir = path.dirname(require.resolve('pdfjs-dist/package.json'))
  // Published path -> file on disk. Built once, and used as an allowlist by
  // the dev-server handler so no request can escape the package directory.
  const files = new Map<string, string>()
  for (const dir of PDFJS_RUNTIME_DIRS) {
    for (const entry of readdirSync(path.join(packageDir, dir), { withFileTypes: true })) {
      if (!entry.isFile()) continue
      files.set(`${PDFJS_RUNTIME_PREFIX}/${dir}/${entry.name}`, path.join(packageDir, dir, entry.name))
    }
  }

  return {
    name: 'bambuddy:pdfjs-runtime-assets',
    generateBundle() {
      for (const [fileName, source] of files) {
        this.emitFile({ type: 'asset', fileName, source: readFileSync(source) })
      }
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const source = files.get((req.url ?? '').split('?')[0].replace(/^\//, ''))
        if (!source) return next()
        res.setHeader('Content-Type', source.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream')
        res.end(readFileSync(source))
      })
    },
  }
}

export default defineConfig({
  // Default base ('/') emits absolute asset URLs (/assets/...). Required so
  // deep SPA routes (camera popup at /camera/<id>, /projects/<id>, kiosk
  // /spoolbuddy/ams, refresh on any nested route) resolve their <script>
  // and <link> tags to /assets/... instead of /<route-prefix>/assets/...,
  // which the SPA fallback would otherwise return as text/html and the
  // browser would refuse to execute (#1221). The earlier `base: ''` partial
  // fix for subpath reverse proxies (#1195, wontfix) is reverted — that
  // audience uses NPM + Cloudflare Tunnel at a real domain per the
  // documented workaround, which doesn't depend on this setting.
  plugins: [react(), pdfjsRuntimeAssets(), lowerEmittedScriptAssets()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 3000,
    // Support floor is Safari 16.0 / iOS 16.0 (see
    // scripts/check-browser-baseline.mjs, #2971). Without an explicit target
    // the bundler keeps newer syntax verbatim — pdf.js ships class static
    // initialisation blocks (Safari 16.4+), which would parse-fail the whole
    // chunk on iOS 16.0-16.3 (#2976). This lowers such syntax at build time;
    // regex features are NOT lowered, which is why the baseline check script
    // still exists alongside this setting.
    target: 'safari16',
  },
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api/v1/ws': {
        target: backendUrl,
        ws: true,
        changeOrigin: true,
      },
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
