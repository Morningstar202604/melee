import type { Metadata } from 'next'
import Link from 'next/link'
import { Flame, MessageSquare } from 'lucide-react'
import { ArenaSetup } from '@/components/arena/arena-setup'
import { BANTER_TOPICS, CODE_TOPICS, DEBATE_TOPICS, SOFT_TOPICS } from '@/lib/arena/topics'
import { CHESS_PUZZLES } from '@/lib/arena/chess'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: 'melee · AI 大乱斗',
  description: '五种玩法：同题抢答、AI 互辩、互怼大会、代码实战、残局杀法 — 多模型同场竞技',
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
          <span className="ml-auto flex items-center gap-4">
            <Link
              href="/solo"
              className="inline-flex items-center gap-1.5 rounded-lg border border-hairline px-3 py-1.5 text-[12px] font-semibold text-ash transition-colors hover:border-dust hover:text-ink"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              单挑台
            </Link>
            <span className="hidden items-center gap-1.5 font-mono text-[11px] text-dust sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
              免登录 · 用户自带 Key · {PROVIDERS.length} 个 provider
            </span>
          </span>
        </div>
      </header>

      <main className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-8 sm:py-10">
        <section className="flex max-w-2xl flex-col gap-2.5">
          <h1 className="text-2xl font-bold leading-snug tracking-tight sm:text-[28px]">
            拉几个大模型，丢进同一个擂台。
          </h1>
          <p className="text-sm leading-relaxed text-ash">
            同题抢答、AI 互辩、互怼大会、代码实战、残局杀法——实时并排输出，观众投票、押注与 AI 裁判双轨判定，
            顺带欣赏各家模型的花式翻车。
          </p>
        </section>

        <ArenaSetup
          providers={PROVIDERS}
          softTopics={SOFT_TOPICS}
          debateTopics={DEBATE_TOPICS}
          banterTopics={BANTER_TOPICS}
          codeTopics={CODE_TOPICS}
          chessPuzzles={CHESS_PUZZLES}
        />
      </main>

      <footer className="mt-auto border-t border-hairline">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-5 py-5 font-mono text-[11px] text-dust">
          <span>melee · AI 大乱斗</span>
          <span>{SOFT_TOPICS.length} 道软战场题</span>
          <span>{DEBATE_TOPICS.length} 道辩题</span>
          <span>{BANTER_TOPICS.length} 个吵架场景</span>
          <span>{CODE_TOPICS.length} 道硬战场题</span>
          <span>{CHESS_PUZZLES.length} 个残局</span>
          <span className="ml-auto">数据仅供即时对战，不做持久化排名</span>
        </div>
      </footer>
    </div>
  )
}
