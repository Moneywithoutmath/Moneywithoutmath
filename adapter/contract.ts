// Shared contract types and validation for the media generation adapter.
// Mirrors MEDIA_GENERATION_CONTRACT.md v1.1.0.

export type MediaType = 'image' | 'video' | 'audio'
export type MediaFormat = 'png' | 'jpeg' | 'webp' | 'mp4' | 'webm' | 'mp3' | 'wav'

export interface ContractRequest {
  media_type: MediaType
  prompt: string
  negative_prompt?: string
  format: MediaFormat
  width?: number
  height?: number
  duration_seconds?: number
  seed?: number
  quality?: 'draft' | 'standard' | 'high'
  count?: number
}

export interface ContractAsset {
  url: string
  format: string
  width?: number
  height?: number
  duration_seconds?: number
  seed?: number
}

export interface ContractSuccess {
  status: 'success'
  assets: ContractAsset[]
}

export type ContractErrorCode =
  | 'invalid_request'
  | 'content_policy'
  | 'quota_exceeded'
  | 'timeout'
  | 'provider_error'

export interface ContractError {
  status: 'error'
  code: ContractErrorCode
  message: string
  upgrade_url?: string
}

export const FORMATS_BY_MEDIA_TYPE: Record<MediaType, MediaFormat[]> = {
  image: ['png', 'jpeg', 'webp'],
  video: ['mp4', 'webm'],
  audio: ['mp3', 'wav']
}

export function contractError(
  code: ContractErrorCode,
  message: string
): ContractError {
  return { status: 'error', code, message }
}

// Cross-field validation the JSON Schema cannot express (see the contract's
// implementation guidance). Returns null when the request is valid.
export function validateRequest(body: unknown): ContractError | null {
  const request = body as Partial<ContractRequest> | null
  if (!request || typeof request !== 'object') {
    return contractError('invalid_request', 'Request body must be a JSON object')
  }
  if (
    request.media_type !== 'image' &&
    request.media_type !== 'video' &&
    request.media_type !== 'audio'
  ) {
    return contractError(
      'invalid_request',
      'media_type must be one of: image, video, audio'
    )
  }
  if (!request.prompt || typeof request.prompt !== 'string') {
    return contractError('invalid_request', 'prompt is required')
  }
  const allowed = FORMATS_BY_MEDIA_TYPE[request.media_type]
  if (!request.format || !allowed.includes(request.format)) {
    return contractError(
      'invalid_request',
      `format "${request.format}" is not valid for media_type "${request.media_type}"; use one of: ${allowed.join(', ')}`
    )
  }
  if (
    request.count !== undefined &&
    (!Number.isInteger(request.count) || request.count < 1 || request.count > 4)
  ) {
    return contractError('invalid_request', 'count must be an integer from 1 to 4')
  }
  return null
}
