import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    port: 5190,
    // 后台数据全部来自 server/（FastAPI）。端口 3000 与 server/README、Dockerfile 的 PORT 一致。
    // 只代理 /api/admin：那是后台专用的一组接口，小程序自己的接口后台不该碰。
    proxy: {
      '/api/admin': 'http://127.0.0.1:3000',
    },
  },
})
