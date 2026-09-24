import { defineConfig } from 'vite'

export default defineConfig({
  server: { port: 18082, proxy: { '/api': 'http://127.0.0.1:18081' } },
})
