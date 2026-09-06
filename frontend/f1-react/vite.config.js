import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser only ever talks to core-api. The three internal services are not
// published to the host at all, so there is nothing else to proxy — which is
// what makes core-api the single place authentication is enforced.
export default defineConfig({
  plugins: [react()],
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
