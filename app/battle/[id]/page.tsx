import type { Metadata } from 'next'
import Link from 'next/link'
import { ChevronLeft } from 'lucide-react'
import { BattleShell } from '@/components/arena/battle-shell'
import { getSnapshot } from '@/lib/db/repo'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: '观战 · melee · AI 大乱斗',
  description: '实时观战多个大模型同场竞技',
}

function MissingBattle({ id }: { id: string }) {
  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4 px-5 py-20">
      <h1 className="text-xl font-bold">对战不存在</h1>
      <p className="text-sm leading-relaxed text-ash">
        id 为 <span className="font-mono text-ink">{id}</span> 的对战没有记录，可能从未创建，
        也可能已被清理。
      </p>
      <Link
        href="/"
        className="inline-flex w-fit items-center gap-1.5 rounded-lg bg-blaze px-4 py-2.5 text-sm font-bold text-void transition-colors hover:bg-blaze/90"
      >
        <ChevronLeft className="h-4 w-4" />
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
