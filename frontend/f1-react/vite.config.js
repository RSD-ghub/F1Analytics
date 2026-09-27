import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The browser only ever talks to core-api. The three internal services are not
// published to the host at all, so there is nothing else to proxy — which is
// what makes core-api the single place authentication is enforced.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // shadcn's components are copied into the repo and import each other by
    // "@/components/ui/…", so the alias is part of its contract rather than a
    // convenience.
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
