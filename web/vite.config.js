import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5180,
    // 前端只管界面，数据一律走 API 服务；/img 也代理过去，
    // 这样本地图和 CDN 回落对前端是透明的。
    proxy: {
      '/api': 'http://localhost:5183',
      '/img': 'http://localhost:5183',
    },
  },
})
