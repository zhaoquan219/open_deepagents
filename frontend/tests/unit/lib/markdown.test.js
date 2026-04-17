import { describe, expect, it } from 'vitest'

import { parseMarkdownSegments } from '../../../src/lib/markdownSegments.js'

describe('parseMarkdownSegments', () => {
  it('splits markdown, mermaid, and trailing markdown into stable segments', () => {
    const segments = parseMarkdownSegments(`
Intro

\`\`\`mermaid
graph TD
A-->B
\`\`\`

Outro
`)

    expect(segments).toEqual([
      expect.objectContaining({
        type: 'markdown',
        content: expect.stringContaining('Intro'),
      }),
      expect.objectContaining({
        type: 'mermaid',
        source: 'graph TD\nA-->B',
      }),
      expect.objectContaining({
        type: 'markdown',
        content: expect.stringContaining('Outro'),
      }),
    ])
  })

  it('captures fenced thinking and reasoning blocks as collapsible segments', () => {
    const segments = parseMarkdownSegments(`
\`\`\`thinking
first pass
\`\`\`

\`\`\`reasoning
second pass
\`\`\`
`)

    expect(segments).toEqual([
      expect.objectContaining({
        type: 'thinking',
        kind: 'thinking',
        content: 'first pass',
      }),
      expect.objectContaining({
        type: 'thinking',
        kind: 'reasoning',
        content: 'second pass',
      }),
    ])
  })

  it('supports xml-style thinking tags without dropping surrounding markdown', () => {
    const segments = parseMarkdownSegments('Before<thinking>step one</thinking>After')

    expect(segments).toEqual([
      expect.objectContaining({
        type: 'markdown',
        content: 'Before',
      }),
      expect.objectContaining({
        type: 'thinking',
        kind: 'thinking',
        content: 'step one',
      }),
      expect.objectContaining({
        type: 'markdown',
        content: 'After',
      }),
    ])
  })

  it('supports common think tags as collapsible thinking segments', () => {
    const segments = parseMarkdownSegments('Before<think>private draft</think>After')

    expect(segments).toEqual([
      expect.objectContaining({
        type: 'markdown',
        content: 'Before',
      }),
      expect.objectContaining({
        type: 'thinking',
        kind: 'think',
        content: 'private draft',
      }),
      expect.objectContaining({
        type: 'markdown',
        content: 'After',
      }),
    ])
  })

  it('does not extract thinking tags from ordinary fenced code blocks', () => {
    const segments = parseMarkdownSegments('```html\n<thinking>literal</thinking>\n```')

    expect(segments).toEqual([
      expect.objectContaining({
        type: 'markdown',
        content: '```html\n<thinking>literal</thinking>\n```',
      }),
    ])
  })

  it('keeps stable keys for unchanged mermaid blocks when later text changes', () => {
    const first = parseMarkdownSegments('alpha\n\n```mermaid\ngraph TD\nA-->B\n```\n\nomega')
    const second = parseMarkdownSegments('alpha updated\n\n```mermaid\ngraph TD\nA-->B\n```\n\nomega\n\nnew tail')

    expect(first[1]).toMatchObject({
      type: 'mermaid',
    })
    expect(second[1]).toMatchObject({
      type: 'mermaid',
    })
    expect(second[1].key).toBe(first[1].key)
  })
})
