import { createAnthropic } from '@ai-sdk/anthropic'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { createOpenAI } from '@ai-sdk/openai'
import type { LanguageModel } from 'ai'
import { redact } from '../log/redact.ts'

export type ProviderKind = 'openai-compatible' | 'anthropic' | 'google'
export type ProviderRegion = 'cn' | 'global'

export interface ProviderDef {
  id: string
  label: string
  kind: ProviderKind
  baseUrl?: string
  defaultModelId?: string
  region: ProviderRegion
  needsProxy: boolean
}

export interface ListModelsResult {
  ok: boolean
  models?: string[]
  reason?: string
}

export const PROVIDERS: ProviderDef[] = [
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
    kind: 'openai-compatible',
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
    kind: 'openai-compatible',
    baseUrl: 'https://api.moonshot.cn/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'dashscope',
    label: '阿里云百炼 DashScope',
    kind: 'openai-compatible',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    region: 'cn',
    needsProxy: false,
  },
  {
    id: 'openai',
    label: 'OpenAI',
    kind: 'openai-compatible',
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
]

export function getProvider(id: string): ProviderDef | undefined {
  return PROVIDERS.find((p) => p.id === id)
}

export function createModel(p: ProviderDef, modelId: string, apiKey: string): LanguageModel {
  if (p.kind === 'openai-compatible') {
    return createOpenAI({ baseURL: p.baseUrl, apiKey }).chat(modelId)
  }
  if (p.kind === 'anthropic') {
    return createAnthropic({ apiKey })(modelId)
  }
  return createGoogleGenerativeAI({ apiKey })(modelId)
}

export async function listModels(p: ProviderDef, apiKey: string): Promise<ListModelsResult> {
  if (p.kind !== 'openai-compatible' || !p.baseUrl) {
    return { ok: false, reason: '该 provider 需手填模型 id' }
  }
  const baseUrl = p.baseUrl.replace(/\/+$/, '')
  try {
    const response = await fetch(`${baseUrl}/models`, {
      headers: { Authorization: `Bearer ${apiKey}` },
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
    const reason = error instanceof Error ? error.message : String(error)
    return { ok: false, reason: redact(`${p.label} 拉取模型列表失败：${reason}`, [apiKey]) }
  }
}
