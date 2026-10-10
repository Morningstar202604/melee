import type { Metadata } from 'next'
import Link from 'next/link'
import { BattleShell } from '@/components/arena/battle-shell'
import { fontWeight, radius, spacing, themeCss, typeScale, v } from '@/components/arena/theme'
import { getSnapshot } from '@/lib/db/repo'
import { PROVIDERS } from '@/lib/providers/registry'

export const metadata: Metadata = {
  title: '观战 · melee · AI 大乱斗',
  description: '实时观战多个大模型同场竞技',
}

function MissingBattle({ id }: { id: string }) {
  return (
    <div style={{ maxWidth: '42rem', margin: '0 auto', padding: '4rem 1.25rem' }}>
      <style dangerouslySetInnerHTML={{ __html: themeCss }} />
      <h1 style={{ fontSize: typeScale.scoreboard, fontWeight: fontWeight.black, color: v('ink') }}>
        对战不存在
      </h1>
      <p
        style={{
          marginTop: spacing.md,
          fontSize: typeScale.body,
          lineHeight: 1.7,
          color: v('ink-muted'),
        }}
      >
        id 为 <span style={{ fontFamily: 'var(--font-geist-mono, monospace)' }}>{id}</span>{' '}
        的对战没有记录，可能从未创建，也可能已被清理。请回擂台首页重新开一场。
      </p>
      <Link
        href="/"
        style={{
          marginTop: spacing.xl,
          display: 'inline-block',
          padding: `${spacing.md} ${spacing.xl}`,
          borderRadius: radius.md,
          backgroundColor: v('scoreboard-accent'),
          color: v('scoreboard'),
          fontSize: typeScale.label,
          fontWeight: fontWeight.black,
          textDecoration: 'none',
        }}
      >
        回擂台首页
      </Link>
    </div>
  )
}

export default async function BattlePage({ params }: PageProps<'/battle/[id]'>) {
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
