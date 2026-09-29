import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前端 Vue3 工程。开发期 npm run dev（5173）代理 /api 到后端 8000；
// 生产 npm run build 产物 web/dist 由后端 FastAPI 静态托管。
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})