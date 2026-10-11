const SECRET_PATTERNS = [/sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}/g]

const REDACTED = '***'

export function redact(text: string, secrets: readonly string[] = []): string {
  let out = text
  const meaningfulSecrets = secrets.filter((s) => typeof s === 'string' && s.length >= 6)
  if (meaningfulSecrets.length === 1) {
    out = out.split(meaningfulSecrets[0]).join(REDACTED)
  } else if (meaningfulSecrets.length > 1) {
    const escaped = meaningfulSecrets.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    out = out.replace(new RegExp(escaped.join('|'), 'g'), REDACTED)
  }
  for (const pattern of SECRET_PATTERNS) {
    out = out.replace(pattern, REDACTED)
  }
  return out
}
