import { describe, expect, it } from 'bun:test'

import { validateRequest } from './contract'
import { generateWithMock } from './providers/mock'
import { handleRequest } from './server'

const validImageRequest = {
  media_type: 'image',
  prompt: 'a lighthouse at sunset',
  format: 'png',
  width: 1024,
  height: 1024,
  count: 2
}

describe('validateRequest', () => {
  it('accepts a valid request', () => {
    expect(validateRequest(validImageRequest)).toBeNull()
  })

  it('rejects a format that does not match the media_type', () => {
    const error = validateRequest({ ...validImageRequest, format: 'mp4' })
    expect(error?.code).toBe('invalid_request')
    expect(error?.message).toContain('mp4')
  })

  it('rejects a missing prompt', () => {
    expect(validateRequest({ media_type: 'image', format: 'png' })?.code).toBe(
      'invalid_request'
    )
  })

  it('rejects an out-of-range count', () => {
    expect(validateRequest({ ...validImageRequest, count: 9 })?.code).toBe(
      'invalid_request'
    )
  })
})

describe('mock provider', () => {
  it('returns a contract-shaped success with the requested count', async () => {
    const result = await generateWithMock(validImageRequest as never)
    expect(result.status).toBe('success')
    expect(result.assets).toHaveLength(2)
    expect(result.assets[0]).toMatchObject({ format: 'png', width: 1024, height: 1024 })
  })
})

describe('handleRequest', () => {
  const post = (body: unknown) =>
    handleRequest(
      new Request('http://localhost/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
    )

  it('serves a valid request end-to-end with the mock provider', async () => {
    const response = await post(validImageRequest)
    expect(response.status).toBe(200)
    const payload = await response.json()
    expect(payload.status).toBe('success')
    expect(payload.assets).toHaveLength(2)
  })

  it('returns a structured contract error for invalid input', async () => {
    const response = await post({ ...validImageRequest, media_type: 'audio' })
    expect(response.status).toBe(400)
    const payload = await response.json()
    expect(payload).toMatchObject({ status: 'error', code: 'invalid_request' })
  })

  it('rejects non-POST methods', async () => {
    const response = await handleRequest(new Request('http://localhost/'))
    expect(response.status).toBe(405)
  })
})
