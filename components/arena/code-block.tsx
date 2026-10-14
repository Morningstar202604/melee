'use client'

import { useEffect, useState } from 'react'
import { cn } from '@/lib/cn'

interface Highlighter {
  codeToHtml: (code: string, options: { lang: string; theme: string }) => string
}

let highlighterPromise: Promise<Highlighter> | null = null

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = (async () => {
      const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
        import('shiki/core'),
        import('shiki/engine/javascript'),
      ])
      const [python, theme] = await Promise.all([
        import('@shikijs/langs/python'),
        import('@shikijs/themes/github-dark'),
      ])
      const core = await createHighlighterCore({
        themes: [theme.default],
        langs: [python.default],
        engine: createJavaScriptRegexEngine(),
      })
      return {
        codeToHtml: (code, options) => core.codeToHtml(code, options),
      }
    })()
  }
  return highlighterPromise
}

export interface CodeBlockProps {
  code: string
  className?: string
}

export function CodeBlock({ code, className }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null)

  useEffect(() => {
    let disposed = false
    void (async () => {
      try {
        const highlighter = await getHighlighter()
        if (disposed) return
        setHtml(highlighter.codeToHtml(code, { lang: 'python', theme: 'github-dark' }))
      } catch {
        // 高亮失败回退纯文本
      }
    })()
    return () => {
      disposed = true
    }
  }, [code])

  if (!html) {
    return (
      <pre
        className={cn(
          'max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-void p-3 font-mono text-[12.5px] leading-[1.7] text-ink/90',
          className,
        )}
      >
        {code}
      </pre>
    )
  }
  return (
    <div
      className={cn(
        'max-h-96 overflow-auto rounded-lg [&_code]:font-mono [&_code]:text-[12.5px] [&_code]:leading-[1.7] [&_pre]:m-0 [&_pre]:p-3',
        className,
      )}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
