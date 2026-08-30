import { readFile } from 'node:fs/promises'
import { serve } from '@hono/node-server'
import { createMockServer } from '@scalar/mock-server'
import { parse } from 'yaml'

const host = '127.0.0.1'
const port = Number(process.env.PORT ?? 4010)
const documentPath = new URL('../docs/api/openapi.yaml', import.meta.url)
const document = parse(await readFile(documentPath, 'utf8'))

const scalarApp = await createMockServer({
  document,
  validateRequest: true,
})

const app = {
  async fetch(request, env, executionContext) {
    const response = await scalarApp.fetch(request, env, executionContext)
    if (response.status === 422) {
      return new Response(
        JSON.stringify({
          code: 'VALIDATION_ERROR',
          message: '요청 값을 확인해 주세요.',
          traceId: 'openapi-mock-validation',
          fieldErrors: [],
        }),
        {
          status: 400,
          headers: { 'content-type': 'application/json; charset=utf-8' },
        },
      )
    }

    if (response.ok && !response.headers.get('X-Catalog-Version')?.trim()) {
      const headers = new Headers(response.headers)
      headers.set('X-Catalog-Version', 'catalog-20260829-01')
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
      })
    }

    return response
  },
}

serve(
  {
    fetch: app.fetch,
    hostname: host,
    port,
  },
  () => {
    console.log(`FEELM OpenAPI mock listening on http://${host}:${port}`)
  },
)
