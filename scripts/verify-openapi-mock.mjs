import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

const port = 4011
const baseUrl = `http://127.0.0.1:${port}`
const serverPath = fileURLToPath(new URL('./mock-openapi.mjs', import.meta.url))
const child = spawn(process.execPath, [serverPath], {
  env: { ...process.env, PORT: String(port) },
  stdio: ['ignore', 'pipe', 'pipe'],
})

async function waitUntilReady() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/api/v1/catalog/genres`)
      if (response.ok) return
    } catch {
      // Server is still starting.
    }
    await delay(100)
  }
  throw new Error('OpenAPI mock did not start within 5 seconds')
}

async function expectStatus(path, expected) {
  const response = await fetch(`${baseUrl}${path}`)
  if (response.status !== expected) {
    throw new Error(`${path}: expected ${expected}, received ${response.status}`)
  }
  return response
}

try {
  await waitUntilReady()
  await expectStatus('/api/v1/movies?limit=999', 400)
  await expectStatus('/api/v1/movies/not-a-uuid', 400)
  await expectStatus(
    '/api/v1/movies/6b226903-0ca4-4f5a-9bf0-50d6cedd224c/similar?limit=999',
    400,
  )
  const facets = await expectStatus('/api/v1/catalog/genres', 200)
  if (!facets.headers.get('x-catalog-version')?.trim()) {
    throw new Error('Catalog success response has no X-Catalog-Version value')
  }
  console.log('OpenAPI mock verification passed: invalid requests use 400 and version header is set.')
} finally {
  child.kill('SIGTERM')
}
