/**
 * 安全头中间件（零依赖）。
 *
 * 覆盖 Next.js App Router 与 API route 共通的几类 HTTP 安全头。
 *
 * 🔗 工程逻辑：
 *   - 这些头对所有 route 生效、与业务逻辑解耦，所以抽出单一模块，由 layout
 *     在 root <head> 处注入 meta 级头，由 API route 由函数注入 response 级头。
 *   - 不引入 helmet：Next.js 16 不反序列 helmet 的全家桶，且我们只需要一小组
 *    静态头，不必付出 ~120KB 的代价。
 */

/** CSP：仅允许同源 + 内联 style + data: 协议的图片 / 字体。 */
const CONTENT_SECURITY_POLICY =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"

const STRICT_TRANSPORT_SECURITY = 'max-age=63072000; includeSubDomains; preload'
const X_CONTENT_TYPE_OPTIONS = 'nosniff'
const X_FRAME_OPTIONS = 'DENY'
const REFERRER_POLICY = 'strict-origin-when-cross-origin'
const PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), interest-cohort=()'

export const SECURITY_HEADERS: Record<string, string> = {
  'Content-Security-Policy': CONTENT_SECURITY_POLICY,
  'Strict-Transport-Security': STRICT_TRANSPORT_SECURITY,
  'X-Content-Type-Options': X_CONTENT_TYPE_OPTIONS,
  'X-Frame-Options': X_FRAME_OPTIONS,
  'Referrer-Policy': REFERRER_POLICY,
  'Permissions-Policy': PERMISSIONS_POLICY,
}

/**
 * 把安全头合并到已有的 ResponseInit 里，返回新的 ResponseInit。
 * 不破坏调用方原有的 status / headers。
 */
export function withSecurityHeaders(init?: ResponseInit): ResponseInit {
  const headers = new Headers(init?.headers)
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    if (!headers.has(name)) headers.set(name, value)
  }
  return { ...init, headers }
}

/**
 * 把安全头包裹到一个已有的 Response 上（方便在 route handler 里一行链式调用）。
 */
export function secureResponse(body: BodyInit | null, init?: ResponseInit): Response {
  return new Response(body, withSecurityHeaders(init))
}
