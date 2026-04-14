const PLACEHOLDER_SESSION_TITLES = new Set([
  '',
  'new session',
  'new chat',
  'untitled',
  'untitled session',
  '新会话',
  '新聊天',
  '未命名会话',
])

function normalizeWhitespace(value) {
  return String(value ?? '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function isPlaceholderSessionTitle(value) {
  const title = normalizeWhitespace(value)
  return !title || PLACEHOLDER_SESSION_TITLES.has(title.toLowerCase())
}

export function normalizeSessionTitle(value) {
  const title = normalizeWhitespace(value)
  return isPlaceholderSessionTitle(title) ? '新会话' : title
}

export function distillSessionTitle(value, maxLength = 32) {
  const raw = String(value ?? '')
  const lines = raw.split(/\r?\n/).map((line) => normalizeWhitespace(line))
  const title = lines.find(Boolean) || normalizeWhitespace(raw)

  if (!title) {
    return '新会话'
  }
  if (title.length <= maxLength) {
    return title
  }
  return `${title.slice(0, maxLength - 3).trimEnd()}...`
}
