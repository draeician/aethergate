import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev-only proxy target. The runtime client is same-origin (relative paths);
// this only forwards API calls from the Vite dev server to the local v2 API.
// Override with VITE_API_PROXY_TARGET when the API binds a non-default port.
const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      // react-router 7.13 imports these CommonJS-only packages from its ESM build;
      // point them at their concrete entries so Rollup resolves them deterministically.
      cookie: fileURLToPath(new URL('./node_modules/cookie/dist/index.js', import.meta.url)),
      'set-cookie-parser': fileURLToPath(
        new URL('./node_modules/set-cookie-parser/lib/set-cookie.js', import.meta.url),
      ),
    },
  },
  server: {
    proxy: {
      '/admin': proxyTarget,
      '/health': proxyTarget,
      '/v1': proxyTarget,
    },
  },
})
