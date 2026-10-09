const SECRET_PATTERNS = [/\bsk-[A-Za-z0-9_-]{8,}/g, /\bBearer\s+[A-Za-z0-9._-]{8,}/g]

const REDACTED = '***'

export function redact(text: string, secrets: readonly string[] = []): string {
  let out = text
  for (const secret of secrets) {
    if (typeof secret === 'string' && secret.length >= 6) {
      out = out.split(secret).join(REDACTED)
    }
  }
  for (const pattern of SECRET_PATTERNS) {
    out = out.replace(pattern, REDACTED)
  }
  return out
}
