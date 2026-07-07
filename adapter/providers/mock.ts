// Zero-cost provider for development and demos: returns placeholder assets
// without calling any paid API. Selected with MEDIA_PROVIDER=mock (also the
// default when no REPLICATE_API_TOKEN is configured).
import type { ContractRequest, ContractSuccess } from '../contract'

const SAMPLE_VIDEO =
  'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'
const SAMPLE_AUDIO =
  'https://interactive-examples.mdn.mozilla.net/media/cc0-audio/t-rex-roar.mp3'

export async function generateWithMock(
  request: ContractRequest
): Promise<ContractSuccess> {
  const count = request.count ?? 1
  const width = request.width ?? 1024
  const height = request.height ?? 1024
  const seed = request.seed ?? Math.floor(Math.random() * 1_000_000)

  const assets = Array.from({ length: count }, (_, index) => {
    if (request.media_type === 'video') {
      return {
        url: SAMPLE_VIDEO,
        format: request.format,
        width,
        height,
        duration_seconds: request.duration_seconds ?? 5,
        seed: seed + index
      }
    }
    if (request.media_type === 'audio') {
      return {
        url: SAMPLE_AUDIO,
        format: request.format,
        duration_seconds: request.duration_seconds ?? 2,
        seed: seed + index
      }
    }
    const text = encodeURIComponent(request.prompt.slice(0, 30))
    return {
      url: `https://placehold.co/${width}x${height}/png?text=${text}`,
      format: request.format,
      width,
      height,
      seed: seed + index
    }
  })

  return { status: 'success', assets }
}
