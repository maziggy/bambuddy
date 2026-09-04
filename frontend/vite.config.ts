import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Backend port for dev server proxy (default: 8000)
const backendPort = process.env.BACKEND_PORT || '8000'
const backendUrl = `http://localhost:${backendPort}`


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
  plugins: [react()],
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
