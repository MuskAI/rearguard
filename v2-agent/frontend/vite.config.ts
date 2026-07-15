import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const runtime = globalThis as unknown as { process?: { env?: Record<string, string | undefined> } }
const evidenceApiTarget = runtime.process?.env?.VITE_API_TARGET || 'http://127.0.0.1:8848'
const accountApiTarget = runtime.process?.env?.VITE_ACCOUNT_API_TARGET || 'http://127.0.0.1:5000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    target: ["es2018", "safari12"],
    cssTarget: "safari12",
  },
  server: {
    port: 5173,
    proxy: {
      '/v2-api': {
        target: evidenceApiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/v2-api/, '/api'),
      },
      '/api': {
        target: accountApiTarget,
        changeOrigin: true,
      },
      '/sms': {
        target: accountApiTarget,
        changeOrigin: true,
      },
      '/image_upload': {
        target: accountApiTarget,
        changeOrigin: true,
      },
      '/video_upload': {
        target: accountApiTarget,
        changeOrigin: true,
      },
      '/legal': {
        target: accountApiTarget,
        changeOrigin: true,
      },
    },
  },
})
