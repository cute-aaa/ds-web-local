import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 后端以 StaticFiles 把构建产物挂载在 /console 下（见 backend/main.py），
  // 故产物内的资源引用必须带 /console/ 前缀，否则 /assets/*.js 会 404 → 页面白屏
  base: '/console/',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8088',
      '/health': 'http://localhost:8088',
    },
  },
})
