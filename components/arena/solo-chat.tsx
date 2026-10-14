'use client'

import { useMemo, useState } from 'react'
import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport } from 'ai'
import { Brain, MessageSquare, Send } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { ProviderDef } from '@/lib/providers/registry'
import { providerAccent } from './theme'

export interface SoloChatProps {
  providers: ProviderDef[]
}

const INPUT_CLASS =
  'min-w-0 flex-1 rounded-lg border border-hairline bg-void px-3.5 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-dust focus:border-blaze/60'

export function SoloChat({ providers }: SoloChatProps) {
  const [providerId, setProviderId] = useState(providers[0]?.id ?? '')
  const [modelId, setModelId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [input, setInput] = useState('')

  const provider = providers.find((item) => item.id === providerId)
  const accent = providerAccent(providerId)
  const configured = Boolean(providerId && modelId.trim() && apiKey.trim())

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: '/api/chat',
        body: () => ({ providerId, modelId: modelId.trim(), apiKey }),
      }),
    [apiKey, modelId, providerId],
  )

  const { messages, sendMessage, status, error } = useChat({ transport })

  const busy = status === 'streaming' || status === 'submitted'

  const submit = () => {
    const text = input.trim()
    if (!text || !configured || busy) return
    setInput('')
    void sendMessage({ text })
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-hairline bg-panel p-4">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md" style={{ backgroundColor: `${accent}1f`, color: accent }}>
          <MessageSquare className="h-4 w-4" />
        </span>
        <label className="flex min-w-40 flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-dust">提供方</span>
          <select
            value={providerId}
            onChange={(event) => setProviderId(event.target.value)}
            className={cn(INPUT_CLASS, 'sm:w-44')}
          >
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
                {item.needsProxy ? '（需代理）' : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-dust">模型 id</span>
          <input
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            placeholder="如 deepseek-chat / glm-4.6 / kimi-k2-0905"
            className={cn(INPUT_CLASS, 'font-mono text-[13px]')}
          />
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-dust">API Key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="只走当次请求内存，不落库"
            autoComplete="off"
            className={cn(INPUT_CLASS, 'font-mono text-[13px]')}
          />
        </label>
      </div>

      <div className="flex min-h-80 flex-col gap-3 rounded-xl border border-hairline bg-panel p-4">
        {messages.length === 0 ? (
          <p className="m-auto text-[13px] text-dust">
            填好「提供方 + 模型 id + Key」就可以开聊——这是官方 <code className="font-mono">useChat</code> 的单挑台。
          </p>
        ) : null}
        {messages.map((message) => (
          <article
            key={message.id}
            className={cn(
              'flex flex-col gap-1.5 rounded-xl border px-3.5 py-2.5',
              message.role === 'user' ? 'self-end border-blaze/30 bg-blaze/5' : 'self-start border-hairline bg-void',
            )}
          >
            <span className="font-mono text-[10px] uppercase tracking-wider text-dust">
              {message.role === 'user' ? '你' : (provider?.label ?? 'AI')}
            </span>
            {message.parts.map((part, index) => {
              if (part.type === 'reasoning') {
                return (
                  <details key={index} className="rounded-lg border border-hairline/60 bg-panel/60">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-dust">
                      <Brain className="h-3 w-3" />
                      思考过程
                    </summary>
                    <p className="max-h-44 overflow-y-auto whitespace-pre-wrap break-words px-2.5 pb-2 text-[12px] leading-relaxed text-dust">
                      {part.text}
                    </p>
                  </details>
                )
              }
              if (part.type === 'text') {
                return (
                  <p key={index} className="whitespace-pre-wrap break-words text-[14px] leading-[1.7] text-ink/90">
                    {part.text}
                  </p>
                )
              }
              return null
            })}
          </article>
        ))}
        {busy ? (
          <p className="flex items-center gap-2 text-[12px] text-dust">
            <span aria-hidden className="h-1.5 w-1.5 animate-live rounded-full bg-ok" />
            输出中…
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-[12px] text-bad">
            {error.message}
          </p>
        ) : null}
      </div>

      <div className="flex items-end gap-3">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          rows={2}
          placeholder={configured ? '问点什么…（Enter 发送，Shift+Enter 换行）' : '先填好上面的配置'}
          className={cn(INPUT_CLASS, 'resize-none')}
        />
        <button
          type="button"
          onClick={submit}
          disabled={!configured || busy || input.trim().length === 0}
          className="inline-flex items-center gap-2 rounded-lg bg-blaze px-5 py-2.5 text-sm font-bold text-void transition-colors hover:bg-blaze/90 disabled:cursor-not-allowed disabled:bg-hairline disabled:text-dust"
        >
          <Send className="h-4 w-4" />
          发送
        </button>
      </div>

      <p className="font-mono text-[11px] text-dust">
        官方件：useChat + DefaultChatTransport + toUIMessageStream / createUIMessageStreamResponse（含 reasoning 部件）· Key 只随请求内存
      </p>
    </section>
  )
}
