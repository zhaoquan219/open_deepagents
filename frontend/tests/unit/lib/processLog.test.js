import { describe, expect, it } from 'vitest'
import {
  groupProcessLogs,
  logEntryFromEnvelope,
  thinkingEntriesFromContent,
  visibleAssistantContent,
} from '../../../src/lib/processLog.js'

describe('processLog', () => {
  it('extracts thinking into process logs and keeps the visible answer clean', () => {
    const content = '<think>先分析问题</think>\n\n最终答案'

    expect(visibleAssistantContent(content)).toBe('最终答案')
    expect(thinkingEntriesFromContent(content, '2026-05-05T00:00:00.000Z')).toEqual([
      expect.objectContaining({
        kind: 'thinking',
        title: '思考过程',
        summary: '先分析问题',
      }),
    ])
  })

  it('summarizes skill metadata instead of showing an empty skills block', () => {
    const entry = logEntryFromEnvelope({
      eventId: 'evt-skill',
      type: 'skill',
      label: 'skill.completed',
      detail: 'Loaded 1 skills',
      status: 'completed',
      timestamp: '2026-05-05T00:00:01.000Z',
      data: {
        skills: [{ name: 'web-research', description: 'Search and synthesize web references' }],
      },
    })

    expect(entry).toMatchObject({
      kind: 'skill',
      title: '已加载 1 个技能',
      summary: 'web-research: Search and synthesize web references',
    })
  })

  it('keeps sandbox command input and output together', () => {
    const entry = logEntryFromEnvelope({
      eventId: 'evt-sandbox',
      type: 'sandbox',
      label: 'sandbox.completed',
      detail: 'execute',
      status: 'completed',
      timestamp: '2026-05-05T00:00:02.000Z',
      data: {
        input: { command: 'python script.py' },
        output: { stdout: 'ok', stderr: '' },
      },
    })

    expect(entry.title).toBe('python script.py')
    expect(entry.summary).toContain('输入: python script.py')
    expect(entry.summary).toContain('ok')
  })

  it('merges consecutive log events into chronological collapsed groups', () => {
    const groups = groupProcessLogs([
      { id: '1', kind: 'thinking', title: '思考过程', summary: 'A', timestamp: '2026-05-05T00:00:00Z' },
      { id: '2', kind: 'thinking', title: '思考过程', summary: 'B', timestamp: '2026-05-05T00:00:01Z' },
      { id: '3', kind: 'sandbox', title: 'python script.py', summary: 'ok', timestamp: '2026-05-05T00:00:02Z' },
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0]).toMatchObject({ kind: 'thinking', items: expect.arrayContaining([expect.objectContaining({ summary: 'A' })]) })
    expect(groups[0].items).toHaveLength(2)
  })
})
