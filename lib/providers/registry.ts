import { createAlibaba } from '@ai-sdk/alibaba'
import { createAnthropic } from '@ai-sdk/anthropic'
import { createDeepSeek } from '@ai-sdk/deepseek'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { createMoonshotAI } from '@ai-sdk/moonshotai'
import { createOpenAI } from '@ai-sdk/openai'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { createGateway, createProviderRegistry, type LanguageModel } from 'ai'
import type { ProviderV4 } from '@ai-sdk/provider'
import { redact } from '../log/redact.ts'
import { logger } from '../log/logger.ts'

export type ProviderKind =
  | 'openai-compatible'
  | 'openai'
  | 'deepseek'
  | 'moonshot'
  | 'alibaba'
  | 'anthropic'
  | 'google'
  | 'gateway'

export type ProviderRegion = 'cn' | 'global'

export interface ProviderDef {
  id: string
  label: string
  kind: ProviderKind
  /** 仅用于模型列表拉取（生成/调用走官方包各自的默认端点） */
  baseUrl?: string
  region: ProviderRegion
  needsProxy: boolean
}

export interface ListModelsResult {
  ok: boolean
  models?: string[]
  reason?: string
}

export const DEFAULT_MODEL_PROVIDER_ID = 'deepseek'

const BUILT_IN_PROVIDERS: ProviderDef[] = [
  {
    id: 'ollama',
    label: 'Ollama（本地）',
    kind: 'openai-compatible',
    baseUrl: 'http://127.0.0.1:11434/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    kind: 'deepseek',
    baseUrl: 'https://api.deepseek.com/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'siliconflow',
    label: '硅基流动 SiliconFlow',
    kind: 'openai-compatible',
    baseUrl: 'https://api.siliconflow.cn/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'zhipu',
    label: '智谱 GLM',
    kind: 'openai-compatible',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'moonshot',
    label: 'Moonshot Kimi',
    kind: 'moonshot',
    baseUrl: 'https://api.moonshot.cn/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'dashscope',
    label: '阿里云百炼 DashScope',
    kind: 'alibaba',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'openai',
    label: 'OpenAI',
    kind: 'openai',
    baseUrl: 'https://api.openai.com/v1',
    region: 'global',
    needsProxy: true,
  },
  {
    id: 'anthropic',
    label: 'Anthropic Claude',
    kind: 'anthropic',
    region: 'global',
    needsProxy: true,
  },
  {
    id: 'google',
    label: 'Google Gemini',
    kind: 'google',
    region: 'global',
    needsProxy: true,
  },
  {
    id: 'gateway',
    label: 'AI Gateway（一家通吃）',
    kind: 'gateway',
    region: 'global',
    needsProxy: false,
  },
]

/** 自定义厂家：环境变量注入（官方 openai-compatible 协议），让"接更多厂家"无需改代码。 */
export function parseCustomProviders(raw: string | undefined): ProviderDef[] {
  if (!raw || raw.trim().length === 0) return []
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const defs: ProviderDef[] = []
    for (const entry of parsed) {
      const item = (entry ?? {}) as Record<string, unknown>
      const id = typeof item.id === 'string' ? item.id.trim().toLowerCase() : ''
      const label = typeof item.label === 'string' ? item.label.trim() : ''
      const baseUrl = typeof item.baseUrl === 'string' ? item.baseUrl.trim() : ''
      if (!/^[a-z0-9-]{2,32}$/.test(id)) continue
      if (!label || !baseUrl.startsWith('http')) continue
      defs.push({
        id,
        label,
        kind: 'openai-compatible',
        baseUrl,
        region: item.region === 'global' ? 'global' : 'cn',
        needsProxy: false,
      })
    }
    return defs
  } catch {
    return []
  }
}

function loadCustomProviders(): ProviderDef[] {
  const custom = parseCustomProviders(process.env.MELEE_CUSTOM_PROVIDERS)
  return custom.filter((def) => {
    if (BUILT_IN_PROVIDERS.some((built) => built.id === def.id)) {
      logger.warn('自定义 provider 与内置 id 冲突，已忽略', { id: def.id })
      return false
    }
    return true
  })
}

const customProviders = loadCustomProviders()

function envBaseUrl(id: string): string | undefined {
  const raw = process.env[`MELEE_BASE_URL_${id.toUpperCase().replace(/-/g, '_')}`]
  const value = raw?.trim()
  return value && value.startsWith('http') ? value : undefined
}

export const PROVIDERS: ProviderDef[] = [...BUILT_IN_PROVIDERS, ...customProviders].map((def) => {
  const override = envBaseUrl(def.id)
  return override ? { ...def, baseUrl: override } : def
})

export function getProvider(id: string): ProviderDef | undefined {
  return PROVIDERS.find((p) => p.id === id)
}

/** 官方 provider 工厂：全部走 @ai-sdk/* 官方包（openai-compatible 用于任意兼容端点）。 */
function providerFactory(def: ProviderDef, apiKey: string): ProviderV4 {
  switch (def.kind) {
    case 'openai-compatible':
      return createOpenAICompatible({
        name: def.id,
        baseURL: def.baseUrl ?? '',
        apiKey,
        includeUsage: true,
      })
    case 'openai':
      return createOpenAI({ apiKey, baseURL: def.baseUrl })
    case 'deepseek':
      return createDeepSeek({ apiKey, baseURL: def.baseUrl })
    case 'moonshot':
      return createMoonshotAI({ apiKey, baseURL: def.baseUrl })
    case 'alibaba':
      return createAlibaba({ apiKey, baseURL: def.baseUrl })
    case 'anthropic':
      return createAnthropic({ apiKey })
    case 'google':
      return createGoogleGenerativeAI({ apiKey })
    case 'gateway':
      return createGateway({ apiKey })
  }
}

export function createModel(def: ProviderDef, modelId: string, apiKey: string): LanguageModel {
  const provider = providerFactory(def, apiKey) as unknown as (id: string) => LanguageModel
  return provider(modelId)
}

/**
 * `provider:model` 引用解析——采用官方 createProviderRegistry 的寻址约定
 * （默认分隔符 `:`）；Provider 实例按请求内的 Key 即时构建。
 */
export function resolveModelRef(ref: string, apiKey: string): LanguageModel {
  const trimmed = ref.trim()
  if (!trimmed) throw new Error('模型引用为空')
  const registry = createProviderRegistry(
    Object.fromEntries(PROVIDERS.map((def) => [def.id, providerFactory(def, apiKey)])),
    { separator: ':' },
  )
  const separatorIndex = trimmed.indexOf(':')
  if (separatorIndex > 0) {
    const prefix = trimmed.slice(0, separatorIndex)
    if (!PROVIDERS.some((def) => def.id === prefix)) {
      throw new Error(`未知 provider 前缀「${prefix}」（引用：${trimmed}）`)
    }
  }
  const addressed = separatorIndex > 0 ? trimmed : `${DEFAULT_MODEL_PROVIDER_ID}:${trimmed}`
  return registry.languageModel(addressed as `${string}:${string}`)
}

export async function listModels(
  p: ProviderDef,
  apiKey: string,
  options: { signal?: AbortSignal } = {},
): Promise<ListModelsResult> {
  if (p.kind === 'gateway') {
    try {
      const metadata = await createGateway({ apiKey }).getAvailableModels()
      const models = metadata.models
        .map((model) => model.id)
        .filter((id): id is string => typeof id === 'string' && id.length > 0)
        .sort((a, b) => a.localeCompare(b))
      return { ok: true, models }
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      return { ok: false, reason: redact(`${p.label} 拉取模型目录失败：${reason}`, [apiKey]) }
    }
  }

  if (!p.baseUrl) {
    return { ok: false, reason: '该 provider 需手填模型 id' }
  }
  const baseUrl = p.baseUrl.replace(/\/+$/, '')
  try {
    const response = await fetch(`${baseUrl}/models`, {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: options.signal,
    })
    if (!response.ok) {
      return { ok: false, reason: redact(`HTTP ${response.status}`, [apiKey]) }
    }
    const payload = (await response.json()) as { data?: Array<{ id?: string }> }
    const models = (payload.data ?? [])
      .map((entry) => entry?.id)
      .filter((id): id is string => typeof id === 'string' && id.length > 0)
      .sort((a, b) => a.localeCompare(b))
    return { ok: true, models }
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return { ok: false, reason: '拉取模型列表超时' }
    }
    const reason = error instanceof Error ? error.message : String(error)
    return { ok: false, reason: redact(`${p.label} 拉取模型列表失败：${reason}`, [apiKey]) }
  }
}
