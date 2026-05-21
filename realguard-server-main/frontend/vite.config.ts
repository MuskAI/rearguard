import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.VITE_DEV_BACKEND || "http://127.0.0.1:5000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/sms": backend,
      "/image_upload": backend,
      "/video_upload": backend,
      "/retrieve": backend,
      "/static": backend
    }
  }
});
