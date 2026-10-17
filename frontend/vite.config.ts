import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// API_KEY is read by the Node process only. Never give it a VITE_ prefix.
export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiKey = process.env.API_KEY || env.API_KEY
  if (command === 'serve' && (!apiKey || apiKey === 'replace-with-your-generated-key')) {
    throw new Error('Set API_KEY in .env.local to the key from the FastAPI .env file')
  }
  const backendUrl = new URL(process.env.BACKEND_URL || env.BACKEND_URL || 'http://127.0.0.1:8000')
  if (!['http:', 'https:'].includes(backendUrl.protocol) || backendUrl.username || backendUrl.password) {
    throw new Error('BACKEND_URL must be an HTTP(S) URL without embedded credentials')
  }
  const proxy = {
    '/api': {
      target: backendUrl.origin,
      changeOrigin: true,
      headers: apiKey ? { 'X-API-Key': apiKey } : {},
      proxyTimeout: 330_000,
      timeout: 330_000,
    },
    '/health': { target: backendUrl.origin, changeOrigin: true },
  }
  return {
    plugins: [react()],
    server: { host: '127.0.0.1', port: 3000, strictPort: true, cors: false, proxy },
    preview: { host: '127.0.0.1', port: 3000, strictPort: true, cors: false, proxy },
  }
})
