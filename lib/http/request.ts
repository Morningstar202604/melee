import type { Limiter } from '../battle/ratelimit.ts'
import { rateLimitMessage } from '../battle/ratelimit.ts'
import { secureResponse } from '../security.ts'

/**
 * 从请求头解析客户端 IP。
 *
 * MELEE_TRUSTED_PROXY_HOPS = 应用前置的可信代理层数（默认 1，即单层反代）。
 * XFF 语义：每个代理把自己看到的对端 IP **追加**（nginx $proxy_add_x_forwarded_for
 * 的默认行为），因此链尾 N 项是可信代理写入的，客户端可伪造的部分在前面。
 * 取 entries[length - hops] 才能拿到真实客户端 IP；直接取首段在 append 型反代
 * 后面可被客户端伪造绕过限流（已实测复现）。
 *
 * hops=0 表示直连暴露：任何头都不可信，退回 x-real-ip / 首段的行为仅作兜底，
 * 此时每 IP 限流不具防伪造能力（单机本地演示场景可接受）。
 */
function trustedProxyHops(): number {
  const raw = Number(process.env.MELEE_TRUSTED_PROXY_HOPS ?? '1')
  return Number.isFinite(raw) && raw >= 0 ? Math.trunc(raw) : 1
}

export function clientIp(request: Request): string {
  const forwardedFor = request.headers.get('x-forwarded-for')
  if (forwardedFor) {
    const entries = forwardedFor
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean)
    const hops = trustedProxyHops()
    const candidate = hops >= 1 ? entries[entries.length - hops] : entries[0]
    if (candidate) return candidate
  }
  const realIp = request.headers.get('x-real-ip')?.trim()
  return realIp || 'unknown'
}

/** 统一的限流拒绝响应（带安全头，措辞按档位）。 */
export function rateLimitedResponse(limiter: Limiter, unit = '次请求'): Response {
  return secureResponse(JSON.stringify({ error: rateLimitMessage(limiter.profile, unit) }), {
    status: 429,
  })
}

/**
 * 带体积上限的 JSON 读取：App Router 的 route handler 没有内建 body 上限，
 * 未受限的 request.json() 会把任意大小的体读进内存。
 *
 * 两条路径都封死：
 *   - 声明了 content-length 且超限 → 不读体直接 413（快路径）
 *   - 分块编码 / 无 content-length → 边读边计数，超限即取消并 413（慢路径）
 * 解析失败（含空体）走 400，语义与原先的 request.json() + catch 一致。
 */
export async function readJsonCapped<T = unknown>(
  request: Request,
  maxBytes: number,
): Promise<{ ok: true; body: T } | { ok: false; response: Response }> {
  const header = request.headers.get('content-length')
  if (header !== null) {
    const length = Number(header)
    if (Number.isFinite(length) && length > maxBytes) {
      return { ok: false, response: tooLargeResponse(maxBytes) }
    }
  }
  if (!request.body) {
    return { ok: false, response: invalidJsonResponse() }
  }

  const reader = request.body.getReader()
  const decoder = new TextDecoder()
  let text = ''
  let total = 0
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > maxBytes) {
        await reader.cancel().catch(() => {})
        return { ok: false, response: tooLargeResponse(maxBytes) }
      }
      text += decoder.decode(value, { stream: true })
    }
    text += decoder.decode()
  } catch {
    return { ok: false, response: invalidJsonResponse() }
  }

  try {
    return { ok: true, body: JSON.parse(text) as T }
  } catch {
    return { ok: false, response: invalidJsonResponse() }
  }
}

function tooLargeResponse(maxBytes: number): Response {
  return secureResponse(
    JSON.stringify({ error: `请求体超出上限（${Math.round(maxBytes / 1024)}KB）` }),
    { status: 413 },
  )
}

function invalidJsonResponse(): Response {
  return secureResponse(JSON.stringify({ error: '请求体不是合法 JSON' }), { status: 400 })
}
