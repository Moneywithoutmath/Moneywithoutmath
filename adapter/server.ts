// Media generation adapter — reference executor for the media generation
// contract (../MEDIA_GENERATION_CONTRACT.md, v1.1.0).
//
// Receives the contract's tool input as JSON and responds with
// { status: "success", assets: [...] } or a structured contract error.
// Point MEDIA_GENERATION_API_URL at this service from any integration.
//
// Run: bun run server.ts
// Env:
//   PORT                    default 8787
//   MEDIA_ADAPTER_API_KEY   optional — require this Bearer token when set
//   MEDIA_PROVIDER          'replicate' | 'mock' (default: replicate when
//                           REPLICATE_API_TOKEN is set, otherwise mock)
//   GENERATION_TIMEOUT_MS   default 110000 (keep below callers' 120s timeout)
//   REPLICATE_*             see providers/replicate.ts
import type { ContractError, ContractRequest } from './contract'
import { contractError, validateRequest } from './contract'
import { generateWithMock } from './providers/mock'
import { generateWithReplicate } from './providers/replicate'

const PORT = Number(Bun.env.PORT ?? 8787)
const API_KEY = Bun.env.MEDIA_ADAPTER_API_KEY
const TIMEOUT_MS = Number(Bun.env.GENERATION_TIMEOUT_MS ?? 110_000)
const PROVIDER =
  Bun.env.MEDIA_PROVIDER ?? (Bun.env.REPLICATE_API_TOKEN ? 'replicate' : 'mock')

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
}

function errorStatus(error: ContractError): number {
  switch (error.code) {
    case 'invalid_request':
      return 400
    case 'content_policy':
      return 422
    case 'quota_exceeded':
      return 402
    case 'timeout':
      return 504
    default:
      return 502
  }
}

export async function handleRequest(req: Request): Promise<Response> {
  if (req.method !== 'POST') {
    return json(contractError('invalid_request', 'Use POST with the contract request JSON'), 405)
  }

  if (API_KEY) {
    const token = (req.headers.get('Authorization') ?? '').replace(/^Bearer\s+/i, '')
    if (token !== API_KEY) {
      return json(contractError('invalid_request', 'Invalid or missing API key'), 401)
    }
  }

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return json(contractError('invalid_request', 'Request body must be valid JSON'), 400)
  }

  const validationError = validateRequest(body)
  if (validationError) return json(validationError, errorStatus(validationError))

  const request = body as ContractRequest
  try {
    const result =
      PROVIDER === 'mock'
        ? await generateWithMock(request)
        : await generateWithReplicate(request, TIMEOUT_MS)

    if (result.status === 'error') return json(result, errorStatus(result))
    return json(result)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return json(contractError('provider_error', message), 502)
  }
}

if (import.meta.main) {
  Bun.serve({ port: PORT, fetch: handleRequest })
  console.log(`media-generation-adapter listening on :${PORT} (provider: ${PROVIDER})`)
}
