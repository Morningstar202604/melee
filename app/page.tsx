import type { Metadata } from 'next'
import { Flame } from 'lucide-react'
import { ArenaSetup } from '@/components/arena/arena-setup'
import { CODE_TOPICS, SOFT_TOPICS } from '@/lib/arena/topics'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: 'melee · AI 大乱斗',
  description: '选多个大模型 + 一个题目，实时看它们同场竞技',
}

export default function Home() {
  return (
    <div className="min-h-full">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-5 py-4">
          <span className="flex items-center gap-2">
            <Flame className="h-4 w-4 text-blaze" />
            <span className="font-mono text-sm font-bold tracking-tight">melee</span>
          </span>
          <span className="h-4 w-px bg-hairline" aria-hidden />
          <span className="text-sm font-semibold text-ash">AI 大乱斗</span>
          <span className="ml-auto hidden items-center gap-1.5 font-mono text-[11px] text-dust sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
            免登录 · 用户自带 Key · {PROVIDERS.length} 个 provider
          </span>
        </div>
      </header>

      <main className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-8 sm:py-10">
        <section className="flex max-w-2xl flex-col gap-2.5">
          <h1 className="text-2xl font-bold leading-snug tracking-tight sm:text-[28px]">
            拉几个大模型，丢进同一个擂台。
          </h1>
          <p className="text-sm leading-relaxed text-ash">
            同一道题，实时并排输出；观众投票与 AI 裁判双轨判定——顺带欣赏各家模型的花式翻车。
          </p>
        </section>

        <ArenaSetup
          providers={PROVIDERS}
          softTopics={SOFT_TOPICS}
          codeTopics={CODE_TOPICS}
        />
      </main>

      <footer className="mt-auto border-t border-hairline">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-5 py-5 font-mono text-[11px] text-dust">
          <span>melee · AI 大乱斗</span>
          <span>{SOFT_TOPICS.length} 道软战场题</span>
          <span>{CODE_TOPICS.length} 道硬战场题</span>
          <span className="ml-auto">数据仅供即时对战，不做持久化排名</span>
        </div>
      </footer>
    </div>
  )
}
