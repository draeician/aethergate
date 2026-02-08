import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/admin': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/v1': 'http://localhost:8000',
    },
  },
})
