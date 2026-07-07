// Replicate driver: creates a prediction and polls it to completion.
//
// Model selection per media type (env, official model slugs):
//   REPLICATE_IMAGE_MODEL  default black-forest-labs/flux-schnell
//   REPLICATE_VIDEO_MODEL  no default — video is opt-in (it is expensive)
//   REPLICATE_AUDIO_MODEL  no default — audio is opt-in
//
// Input mapping targets flux-schnell's schema for images; for other models
// set REPLICATE_<TYPE>_EXTRA_INPUT to a JSON object merged into the
// prediction input (e.g. '{"num_frames": 81}').
import type {
  ContractAsset,
  ContractError,
  ContractRequest,
  ContractSuccess
} from '../contract'
import { contractError } from '../contract'

const API_BASE = 'https://api.replicate.com/v1'
const POLL_INTERVAL_MS = 2_000

interface ReplicatePrediction {
  id: string
  status: 'starting' | 'processing' | 'succeeded' | 'failed' | 'canceled'
  output?: unknown
  error?: string
}

function modelFor(mediaType: ContractRequest['media_type']): string | undefined {
  switch (mediaType) {
    case 'image':
      return Bun.env.REPLICATE_IMAGE_MODEL ?? 'black-forest-labs/flux-schnell'
    case 'video':
      return Bun.env.REPLICATE_VIDEO_MODEL
    case 'audio':
      return Bun.env.REPLICATE_AUDIO_MODEL
  }
}

function extraInputFor(mediaType: ContractRequest['media_type']): Record<string, unknown> {
  const raw = Bun.env[`REPLICATE_${mediaType.toUpperCase()}_EXTRA_INPUT`]
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    console.error(`Ignoring invalid REPLICATE_${mediaType.toUpperCase()}_EXTRA_INPUT JSON`)
    return {}
  }
}

// Closest flux-supported aspect ratio for the requested dimensions.
function aspectRatioFor(width?: number, height?: number): string {
  if (!width || !height) return '1:1'
  const ratios: Array<[string, number]> = [
    ['1:1', 1],
    ['16:9', 16 / 9],
    ['9:16', 9 / 16],
    ['4:3', 4 / 3],
    ['3:4', 3 / 4],
    ['3:2', 3 / 2],
    ['2:3', 2 / 3]
  ]
  const target = width / height
  ratios.sort((a, b) => Math.abs(a[1] - target) - Math.abs(b[1] - target))
  return ratios[0][0]
}

function buildInput(request: ContractRequest): Record<string, unknown> {
  const input: Record<string, unknown> = {
    prompt: request.prompt,
    ...(request.seed !== undefined && { seed: request.seed })
  }
  if (request.media_type === 'image') {
    input.aspect_ratio = aspectRatioFor(request.width, request.height)
    input.output_format = request.format === 'jpeg' ? 'jpg' : request.format
    input.num_outputs = request.count ?? 1
  }
  if (request.negative_prompt) input.negative_prompt = request.negative_prompt
  return { ...input, ...extraInputFor(request.media_type) }
}

function outputUrls(output: unknown): string[] {
  if (typeof output === 'string') return [output]
  if (Array.isArray(output)) {
    return output.filter((entry): entry is string => typeof entry === 'string')
  }
  return []
}

export async function generateWithReplicate(
  request: ContractRequest,
  timeoutMs: number
): Promise<ContractSuccess | ContractError> {
  const token = Bun.env.REPLICATE_API_TOKEN
  if (!token) {
    return contractError('provider_error', 'REPLICATE_API_TOKEN is not configured')
  }
  const model = modelFor(request.media_type)
  if (!model) {
    return contractError(
      'provider_error',
      `No Replicate model configured for media_type "${request.media_type}" (set REPLICATE_${request.media_type.toUpperCase()}_MODEL)`
    )
  }

  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json'
  }

  const createResponse = await fetch(`${API_BASE}/models/${model}/predictions`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ input: buildInput(request) })
  })
  if (!createResponse.ok) {
    const detail = await createResponse.text().catch(() => '')
    return contractError(
      'provider_error',
      `Replicate rejected the prediction (HTTP ${createResponse.status}): ${detail.slice(0, 300)}`
    )
  }

  let prediction = (await createResponse.json()) as ReplicatePrediction
  const deadline = Date.now() + timeoutMs

  while (prediction.status === 'starting' || prediction.status === 'processing') {
    if (Date.now() > deadline) {
      return contractError(
        'timeout',
        `Generation exceeded ${Math.round(timeoutMs / 1000)}s; try quality "draft" or a shorter duration`
      )
    }
    await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS))
    const pollResponse = await fetch(`${API_BASE}/predictions/${prediction.id}`, {
      headers
    })
    if (!pollResponse.ok) {
      return contractError(
        'provider_error',
        `Replicate poll failed (HTTP ${pollResponse.status})`
      )
    }
    prediction = (await pollResponse.json()) as ReplicatePrediction
  }

  if (prediction.status !== 'succeeded') {
    const message = prediction.error ?? `prediction ${prediction.status}`
    const isPolicy = /nsfw|safety|sensitive/i.test(message)
    return contractError(
      isPolicy ? 'content_policy' : 'provider_error',
      `Replicate generation failed: ${message}`
    )
  }

  const urls = outputUrls(prediction.output)
  if (urls.length === 0) {
    return contractError('provider_error', 'Replicate returned no output files')
  }

  const assets: ContractAsset[] = urls.map(url => ({
    url,
    format: request.format,
    ...(request.media_type !== 'audio' && {
      width: request.width,
      height: request.height
    }),
    ...(request.media_type !== 'image' && {
      duration_seconds: request.duration_seconds
    }),
    ...(request.seed !== undefined && { seed: request.seed })
  }))

  return { status: 'success', assets }
}
