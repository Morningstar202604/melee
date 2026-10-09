import { cancelBattle } from '../../../../../lib/battle/runner.ts'

export const runtime = 'nodejs'

export async function POST(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params
  const cancelled = cancelBattle(id)
  if (!cancelled) {
    return Response.json({ error: '对战不存在或已结束' }, { status: 404 })
  }
  return Response.json({ ok: true })
}
