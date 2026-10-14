import type { Metadata } from 'next'
import Link from 'next/link'
import { ChevronLeft } from 'lucide-react'
import { SoloChat } from '@/components/arena/solo-chat'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: '单挑台 · melee · AI 大乱斗',
  description: '和任意一家模型一对一聊天（官方 useChat 协议）',
}

export default function SoloPage() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-5 py-8">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm font-semibold text-ash transition-colors hover:text-ink"
        >
          <ChevronLeft className="h-4 w-4" />
          回擂台
        </Link>
        <h1 className="text-xl font-bold">单挑台</h1>
        <span className="text-[13px] text-ash">一对一聊天 · 想先摸摸任何一家的底，就在这试</span>
      </header>
      <SoloChat providers={PROVIDERS} />
    </div>
  )
}
