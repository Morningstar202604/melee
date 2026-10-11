import type { Metadata } from 'next'
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
      {/* ── Hero ──────────────────────────────────────────────── */}
      <header className="relative overflow-hidden border-b border-zinc-200 bg-gradient-to-br from-orange-50 via-amber-50 to-white dark:border-zinc-800 dark:from-zinc-950 dark:via-zinc-900 dark:to-zinc-950">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(234,88,12,0.08),transparent_50%)] dark:bg-[radial-gradient(circle_at_30%_20%,rgba(251,146,60,0.12),transparent_50%)]" />
        <div className="relative mx-auto max-w-5xl px-5 py-12 sm:py-16">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <span className="text-3xl sm:text-4xl">⚔️</span>
                <h1 className="text-3xl font-black tracking-tight text-zinc-900 sm:text-4xl dark:text-zinc-50">
                  melee · AI 大乱斗
                </h1>
              </div>
              <p className="max-w-lg text-base leading-relaxed text-zinc-600 dark:text-zinc-400">
                把多个大模型拉进同一个擂台，给它们同一个题目，看谁回答得更好 —
                实时流式输出、AI 裁判打分、观众投票定胜负。
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2 rounded-full bg-white/80 px-4 py-2 text-xs font-medium text-zinc-500 shadow-sm backdrop-blur dark:bg-zinc-800/80 dark:text-zinc-400">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-glow-pulse" />
              无需注册 · 立即可用
            </div>
          </div>

          {/* 三步引导 */}
          <div className="mt-10 grid gap-4 sm:grid-cols-3">
            {[
              { icon: '🎯', title: '选赛道', desc: '软战场辩论创意 OR 硬战场代码实战' },
              { icon: '👥', title: '点大将', desc: 'pick 模型 + provider，配 API Key' },
              { icon: '⚔️', title: '开打观战', desc: '实时流式输出，裁判 + 观众双轨判定' },
            ].map((step) => (
              <div
                key={step.title}
                className="rounded-xl border border-zinc-200 bg-white/70 p-4 backdrop-blur dark:border-zinc-800 dark:bg-zinc-800/50"
              >
                <div className="flex items-center gap-2 text-base font-bold text-zinc-900 dark:text-zinc-50">
                  <span>{step.icon}</span>
                  {step.title}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
                  {step.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </header>

      {/* ── Arena Setup ─────────────────────────────────────────── */}
      <main className="mx-auto max-w-5xl px-5 py-8">
        <ArenaSetup
          providers={PROVIDERS}
          softTopics={SOFT_TOPICS}
          codeTopics={CODE_TOPICS}
        />
      </main>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <footer className="mt-auto border-t border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto max-w-5xl px-5 py-6 text-center text-xs text-zinc-400 dark:text-zinc-600">
          <p>melee · 开源 AI 模型对战平台 · 数据仅供即时对战，不做持久化排名</p>
          <p className="mt-1">支持 {PROVIDERS.length} 个模型 Provider · {SOFT_TOPICS.length} 道软战场题 · {CODE_TOPICS.length} 道硬战场题</p>
        </div>
      </footer>
    </div>
  )
}
