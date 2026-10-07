import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev-only proxy target. The runtime client is same-origin (relative paths);
// this only forwards API calls from the Vite dev server to the local v2 API.
// Override with VITE_API_PROXY_TARGET when the API binds a non-default port.
const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/admin': proxyTarget,
      '/health': proxyTarget,
      '/v1': proxyTarget,
    },
  },
})
