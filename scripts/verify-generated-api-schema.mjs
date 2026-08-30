import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const generator = path.join(root, 'frontend/node_modules/openapi-typescript/bin/cli.js')
const canonical = path.join(root, 'frontend/src/api/schema.d.ts')
const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'feelm-openapi-schema-'))
const generated = path.join(temporaryRoot, 'schema.d.ts')

try {
  assert.ok(fs.existsSync(generator), 'frontend dependencies are not installed; run npm ci --prefix frontend')
  const result = spawnSync(process.execPath, [generator, 'docs/api/openapi.yaml', '-o', generated], {
    cwd: root,
    encoding: 'utf8',
    stdio: 'pipe',
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout || 'openapi-typescript failed\n')
    process.exit(result.status ?? 1)
  }
  const expectedBytes = fs.readFileSync(canonical)
  const generatedBytes = fs.readFileSync(generated)
  assert.deepEqual(generatedBytes, expectedBytes, 'frontend/src/api/schema.d.ts is stale; run npm run generate:api --prefix frontend')
  console.log(`Generated API schema validation: PASS (${expectedBytes.length} bytes, byte-identical)`)
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true })
}
