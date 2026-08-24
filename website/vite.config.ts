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
    //
    // 要打别的后端就 VITE_PROXY_TARGET=http://127.0.0.1:3001 pnpm dev——
    // 本地起第二个服务（改完代码验证，而 dev_server.py 不支持 --reload）时用得上。
    // 变量名与 antony-web/vite.config.ts 一致，两个前端不要各起一套叫法
    proxy: {
      '/api/admin': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:3000',
        changeOrigin: true,
      },
    },
  },
})
