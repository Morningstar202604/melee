/**
 * 安全头（零依赖）。
 *
 * 覆盖 API route 的 response 级头。
 *
 * 选型：不引入 helmet——Next.js 16 反序列 helmet 全家桶没有收益，且我们只需要
 * 一小组静态头。
 *
 * 范围说明：这些头只覆盖 API route（由 secureResponse 注入）。页面路由未注入
 * 页面级 CSP，原因是 Next.js 的 client bundle 与 dev overlay 依赖 inline 脚本，
 * 页面级 script-src 'self' 会直接破坏 hydration 与开发期报错浮层；
 * X-Frame-Options / HSTS 也无法用 meta 表达。若将来要把页面也纳入覆盖，走
 * middleware/proxy 在 response 上注入，并需为 Next 内联脚本放开相应来源。
 */

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

export function withSecurityHeaders(init?: ResponseInit): ResponseInit {
  const headers = new Headers(init?.headers)
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    if (!headers.has(name)) headers.set(name, value)
  }
  return { ...init, headers }
}

export function secureResponse(body: BodyInit | null, init?: ResponseInit): Response {
  return new Response(body, withSecurityHeaders(init))
}

/**
 * 给已构建好的 Response（如 SDK 的流式响应、SSE 长连接）补安全头。
 * 通过重建 Response 保留原 body 流、状态与既有头；不会覆盖同名头。
 */
export function hardenResponse(response: Response): Response {
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: withSecurityHeaders({ headers: response.headers }).headers,
  })
}
