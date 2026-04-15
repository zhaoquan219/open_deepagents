import { uiCopy } from './copy.js'

const PLACEHOLDER_SESSION_TITLES = new Set(
  ['', ...uiCopy.sessionTitles.placeholderAliases].map((title) => title.toLowerCase()),
)

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
  return isPlaceholderSessionTitle(title) ? uiCopy.sessionTitles.defaultTitle : title
}

export function distillSessionTitle(value, maxLength = 32) {
  const raw = String(value ?? '')
  const lines = raw.split(/\r?\n/).map((line) => normalizeWhitespace(line))
  const title = lines.find(Boolean) || normalizeWhitespace(raw)

  if (!title) {
    return uiCopy.sessionTitles.defaultTitle
  }
  if (title.length <= maxLength) {
    return title
  }
  return `${title.slice(0, maxLength - 3).trimEnd()}...`
}
