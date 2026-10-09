import { getDb } from '@/lib/db/client'

export const runtime = 'nodejs'

export async function GET() {
  const db = getDb()
  const row = db.prepare('SELECT COUNT(*) AS battle_count FROM battles').get() as
    | Record<string, unknown>
    | undefined
  const battleCount = Number(row?.battle_count ?? 0)
  return Response.json({ ok: true, battleCount })
}
