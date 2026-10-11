import { APICallError, streamText, type LanguageModel, type ModelMessage } from 'ai'
import { redact } from '../log/redact.ts'
import { logger } from '../log/logger.ts'

export type ContestantStatus =
  | 'ok'
  | 'timeout'
  | 'rate_limited'
  | 'auth_failed'
  | 'provider_error'
  | 'empty'

export type StreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; usage: { prompt: number; completion: number } | null; latencyMs: number }
  | { type: 'error'; status: ContestantStatus; message: string }

export interface StreamOptions {
  signal: AbortSignal
  apiKey?: string
  providerLabel?: string
  maxOutputTokens?: number
  maxRetries?: number
  system?: string
}

const DETAIL_LIMIT = 300

function isAbortError(error: unknown): boolean {
  if (error instanceof Error && error.name === 'AbortError') return true
  return typeof DOMException === 'function' && error instanceof DOMException && error.name === 'AbortError'
}

function unwrapError(error: unknown): unknown {
  let current = error
  for (let depth = 0; depth < 4; depth += 1) {
    if (!(current instanceof Error)) return current
    const candidate = current as Error & { lastError?: unknown }
    if (!(candidate.lastError instanceof Error) || candidate.lastError === current) return current
    current = candidate.lastError
  }
  return current
}

function clip(text: string): string {
  return text.length > DETAIL_LIMIT ? `${text.slice(0, DETAIL_LIMIT)}…` : text
}

function statusFromError(error: unknown): ContestantStatus {
  if (isAbortError(error)) return 'timeout'
  const statusCode = APICallError.isInstance(error) ? error.statusCode : undefined
  if (statusCode === 401 || statusCode === 403) return 'auth_failed'
  if (statusCode === 429) return 'rate_limited'
  return 'provider_error'
}

function describeError(
  error: unknown,
  label: string,
  apiKey?: string,
): { status: ContestantStatus; message: string } {
  const root = unwrapError(error)
  const status = statusFromError(root)
  const detail = root instanceof Error ? clip(root.message) : clip(String(root))
  const statusCode = APICallError.isInstance(root) ? root.statusCode : undefined
  const httpPart = typeof statusCode === 'number' ? `（HTTP ${statusCode}）` : ''
  let reason: string
  if (status === 'timeout') {
    reason = `${label} 超时未完成`
  } else if (status === 'auth_failed') {
    reason = `${label} 鉴权失败${httpPart}，请检查 Key 是否有效`
  } else if (status === 'rate_limited') {
    reason = `${label} 被限流${httpPart}，请稍后重试或检查额度`
  } else {
    reason = `${label} 调用失败${httpPart}：${detail}`
  }
  return { status, message: redact(reason, apiKey ? [apiKey] : []) }
}

function modelLabel(model: LanguageModel): string {
  if (typeof model === 'string') return model
  return `${model.provider} ${model.modelId}`.trim()
}

function isRetryable(error: unknown): boolean {
  if (APICallError.isInstance(error)) {
    const code = error.statusCode
    if (code === 401 || code === 403 || code === 400) return false
    if (code === 429 || (typeof code === 'number' && code >= 500)) return true
  }
  if (error instanceof Error) {
    const msg = error.message.toLowerCase()
    return (
      msg.includes('econnreset') ||
      msg.includes('econnrefused') ||
      msg.includes('etimedout') ||
      msg.includes('socket hang up') ||
      msg.includes('network') ||
      msg.includes('fetch failed')
    )
  }
  return false
}

function backoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 8000) + Math.floor(Math.random() * 200)
}

export async function* streamModel(
  model: LanguageModel,
  messages: ModelMessage[],
  opts: StreamOptions,
): AsyncGenerator<StreamEvent> {
  const label = opts.providerLabel ?? modelLabel(model)
  const startedAt = Date.now()
  let emitted = 0
  const maxAttempts = 1 + (opts.maxRetries ?? 2)

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (opts.signal?.aborted) return
    if (attempt > 0) {
      const delay = backoffMs(attempt - 1)
      logger.warn(`${label} 触发重试`, { attempt, delayMs: delay })
      yield { type: 'delta', text: '' }
      await new Promise((resolve) => setTimeout(resolve, delay))
      if (opts.signal?.aborted) return
    }

    try {
      const result = streamText({
        model,
        messages,
        system: opts.system,
        abortSignal: opts.signal,
        maxOutputTokens: opts.maxOutputTokens,
        maxRetries: 0,
      })

      for await (const part of result.stream) {
        if (part.type === 'text-delta') {
          if (!part.text) continue
          emitted += part.text.length
          yield { type: 'delta', text: part.text }
          continue
        }
        if (part.type === 'error') {
          void Promise.resolve(result.usage).catch(() => {})
          yield { type: 'error', ...describeError(part.error, label, opts.apiKey) }
          return
        }
        if (part.type === 'abort') {
          void Promise.resolve(result.usage).catch(() => {})
          yield { type: 'error', status: 'timeout', message: redact(`${label} 超时未完成`) }
          return
        }
        if (part.type === 'finish') {
          continue
        }
      }

      const usage = await result.usage
      const latencyMs = Date.now() - startedAt

      if (emitted === 0) {
        yield { type: 'error', status: 'empty', message: redact(`${label} 未返回任何内容`) }
        return
      }

      if (attempt > 0) {
        logger.info(`${label} 重试后成功`, { attempt, emitted })
      }
      const hasUsage = usage.inputTokens !== undefined || usage.outputTokens !== undefined
      yield {
        type: 'done',
        usage: hasUsage
          ? { prompt: usage.inputTokens ?? 0, completion: usage.outputTokens ?? 0 }
          : null,
        latencyMs,
      }
      return
    } catch (error) {
      if (attempt < maxAttempts - 1 && isRetryable(error)) {
        continue
      }
      yield { type: 'error', ...describeError(error, label, opts.apiKey) }
      return
    }
  }
}
