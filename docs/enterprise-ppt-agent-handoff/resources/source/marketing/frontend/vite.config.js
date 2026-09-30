import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { port: 18082, proxy: { '/api': process.env.MARKETING_API_PROXY || 'http://127.0.0.1:18081' } },
})
