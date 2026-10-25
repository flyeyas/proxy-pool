import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式下把后端 API 请求转发给 Flask 服务
const apiProxyTarget = process.env.API_TARGET || 'http://127.0.0.1:5010'
const apiPaths = ['/get', '/pop', '/all', '/count', '/delete', '/refresh', '/fetchers', '/logs']

export default defineConfig({
  plugins: [vue()],
  base: '/webui/',
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((p) => [p, { target: apiProxyTarget }]))
  },
  build: {
    outDir: 'dist'
  }
})
