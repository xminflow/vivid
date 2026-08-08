import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 官网发布在 antonycasa.weelume.com 根路径，后台管理（website/）挂在 /admin，
// 两者由服务器上的 Caddy 分流，见 server/deploy/Caddyfile。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    port: 5180,
    // 线上官网和接口同域，前端一律用相对路径 /api/*，不拼域名。
    // 开发时把它代理到后端：默认打本机 WSL 里跑的服务（端口与 server/Dockerfile 一致），
    // 要连线上就 VITE_PROXY_TARGET=https://antonycasa.weelume.com pnpm dev
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:3000',
        changeOrigin: true,
      },
    },
  },
})
