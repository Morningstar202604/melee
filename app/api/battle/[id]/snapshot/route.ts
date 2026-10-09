import { getSnapshot } from '../../../../../lib/db/repo.ts'

export const runtime = 'nodejs'

export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params
  const snapshot = getSnapshot(id)
  if (!snapshot) {
    return Response.json({ error: '对战不存在' }, { status: 404 })
  }
  return Response.json(snapshot)
}
