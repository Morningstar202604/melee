import type { Metadata } from 'next'
import Link from 'next/link'
import { BattleShell } from '@/components/arena/battle-shell'
import { getSnapshot } from '@/lib/db/repo'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: '观战 · melee · AI 大乱斗',
  description: '实时观战多个大模型同场竞技',
}

function MissingBattle({ id }: { id: string }) {
  return (
    <div className="mx-auto max-w-lg px-5 py-16">
      <h1 className="text-3xl font-black text-zinc-900 dark:text-zinc-50">
        对战不存在
      </h1>
      <p className="mt-3 text-sm leading-[1.7] text-zinc-500">
        id 为 <span className="font-mono">{id}</span>{' '}
        的对战没有记录，可能从未创建，也可能已被清理。请回擂台首页重新开一场。
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-lg bg-amber-400 px-6 py-2.5 text-sm font-black text-zinc-900 no-underline transition-colors hover:bg-amber-500"
      >
        回擂台首页
      </Link>
    </div>
  )
}

type PageParams = { params: Promise<{ id: string }> }

export default async function BattlePage({ params }: PageParams) {
  const { id } = await params
  const snapshot = getSnapshot(id)

  if (!snapshot) {
    return <MissingBattle id={id} />
  }

  const providerLabels: Record<string, string> = {}
  for (const provider of PROVIDERS) providerLabels[provider.id] = provider.label

  return (
    <BattleShell
      battleId={snapshot.battle.id}
      topic={snapshot.battle.topic}
      arena={snapshot.battle.arena as 'soft' | 'hard-code'}
      initialSnapshot={snapshot}
      providerLabels={providerLabels}
    />
  )
}
