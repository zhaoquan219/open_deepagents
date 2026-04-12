import { describe, expect, it } from 'vitest'
import { finalizeAssistantMessage, mergeAssistantDelta } from './sessionStore.js'

describe('sessionStore transcript helpers', () => {
  it('merges assistant deltas into a transient streaming message', () => {
    const initial = []
    const firstPass = mergeAssistantDelta(initial, { runId: 'run-1', delta: 'Hello' })
    const secondPass = mergeAssistantDelta(firstPass, { runId: 'run-1', delta: ' world' })

    expect(secondPass).toHaveLength(1)
    expect(secondPass[0]).toMatchObject({
      id: 'stream:run-1',
      content: 'Hello world',
      streaming: true,
    })
  })

  it('replaces the transient message with the finalized assistant row', () => {
    const streaming = mergeAssistantDelta([], { runId: 'run-2', delta: 'Partial' })
    const finalized = finalizeAssistantMessage(streaming, {
      runId: 'run-2',
      message: {
        id: 'msg-final',
        content: 'Final answer',
      },
    })

    expect(finalized).toEqual([
      expect.objectContaining({
        id: 'msg-final',
        content: 'Final answer',
        streaming: false,
      }),
    ])
  })
})
