import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const runtime = globalThis as unknown as { process?: { env?: Record<string, string | undefined> } }
const apiTarget = runtime.process?.env?.VITE_API_TARGET || 'http://127.0.0.1:8848'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: "/v2/",
  build: {
    target: ["es2018", "safari12"],
    cssTarget: "safari12",
  },
  server: {
    port: 5173,
    proxy: {
      '/v2-api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/v2-api/, '/api'),
      },
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
