import { messages, uiCopy } from './copy.js'

function normalizeWhitespace(value) {
  return String(value ?? '')
    .replace(/\s+/g, ' ')
    .trim()
}

export function isPlaceholderSessionTitle(value) {
  const title = normalizeWhitespace(value)
  return !title || placeholderSessionTitles().has(title.toLowerCase())
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

function placeholderSessionTitles() {
  const aliases = Object.values(messages).flatMap((copy) => copy.sessionTitles.placeholderAliases)
  return new Set(['', ...aliases].map((title) => title.toLowerCase()))
}
