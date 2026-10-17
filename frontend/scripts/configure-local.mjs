import { readFileSync, writeFileSync } from 'node:fs'
import { parseEnv } from 'node:util'
import { resolve } from 'node:path'

// Copies a local secret without printing it or exposing it through VITE_ variables.
const source = resolve(process.argv[2] || '../.env')
const backend = new URL(process.argv[3] || 'http://127.0.0.1:8000')
if (!['http:', 'https:'].includes(backend.protocol) || backend.username || backend.password) {
  throw new Error('Backend address must be an HTTP(S) URL without credentials')
}
const config = parseEnv(readFileSync(source, 'utf8'))
const key = config.API_KEY
if (!key?.trim() || key === 'replace-with-your-generated-key' || /[\r\n]/.test(key)) {
  throw new Error('Set a generated API_KEY in the backend .env first')
}
const size = Number(config.MAX_UPLOAD_MB || 100)
if (!Number.isSafeInteger(size) || size < 1) throw new Error('Invalid MAX_UPLOAD_MB')
writeFileSync('.env.local', `BACKEND_URL=${backend.origin}\nAPI_KEY=${JSON.stringify(key)}\nVITE_MAX_UPLOAD_MB=${size}\n`, { flag: 'wx', mode: 0o600 })
console.log('Created .env.local. API key is hidden. This file is ignored by Git.')
