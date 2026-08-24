import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/f1': 'http://localhost:8082'
    }
  },
  build: {
    outDir: '../../src/main/resources/web/f1',
    emptyOutDir: true
  }
})
