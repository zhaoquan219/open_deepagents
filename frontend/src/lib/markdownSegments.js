function normalizeBlockContent(value) {
  return String(value || '').replace(/^\s*\n/, '').replace(/\n\s*$/, '')
}

function hashString(value) {
  let hash = 5381
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index)
  }
  return (hash >>> 0).toString(16)
}

function createSegmentKey(type, index, content) {
  return `${type}-${index}-${hashString(content)}`
}

const SPECIAL_BLOCK_PATTERN =
  /```([^\n`]*)\s*\n?([\s\S]*?)```|<(thinking|reasoning)>([\s\S]*?)<\/\3>/gi

export function parseMarkdownSegments(markdown) {
  const source = String(markdown || '')
  const segments = []
  let lastIndex = 0
  let markdownIndex = 0
  let mermaidIndex = 0
  let thinkingIndex = 0

  const pushMarkdown = (content) => {
    if (!content || !content.trim()) {
      return
    }
    segments.push({
      type: 'markdown',
      content,
      key: createSegmentKey('markdown', markdownIndex, content),
    })
    markdownIndex += 1
  }

  for (const match of source.matchAll(SPECIAL_BLOCK_PATTERN)) {
    const matchIndex = match.index ?? 0
    if (matchIndex > lastIndex) {
      pushMarkdown(source.slice(lastIndex, matchIndex))
    }

    if (match[1] !== undefined) {
      const kind = String(match[1] || '').trim().toLowerCase()
      const content = normalizeBlockContent(match[2])
      if (kind === 'mermaid') {
        segments.push({
          type: 'mermaid',
          source: content,
          key: createSegmentKey('mermaid', mermaidIndex, content),
        })
        mermaidIndex += 1
      } else if (kind === 'thinking' || kind === 'reasoning') {
        segments.push({
          type: 'thinking',
          kind,
          content,
          key: createSegmentKey(kind, thinkingIndex, content),
        })
        thinkingIndex += 1
      } else {
        pushMarkdown(match[0])
      }
    } else {
      const kind = String(match[3]).toLowerCase()
      const content = normalizeBlockContent(match[4])
      segments.push({
        type: 'thinking',
        kind,
        content,
        key: createSegmentKey(kind, thinkingIndex, content),
      })
      thinkingIndex += 1
    }

    lastIndex = matchIndex + match[0].length
  }

  if (lastIndex < source.length) {
    pushMarkdown(source.slice(lastIndex))
  }

  return segments
}
