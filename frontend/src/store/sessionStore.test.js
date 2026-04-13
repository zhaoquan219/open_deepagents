import { describe, expect, it, vi } from 'vitest'
import { createSessionStore, finalizeAssistantMessage, mergeAssistantDelta } from './sessionStore.js'

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

  it('consumes finalized events even when the payload only contains a top-level message', () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.messagesBySession['session-1'] = mergeAssistantDelta([], { runId: 'run-8', delta: '片段' })
    store.consumeRunEvent({
      type: 'message.final',
      runId: 'run-8',
      sessionId: 'session-1',
      timestamp: '2026-04-13T10:00:00.000Z',
      message: {
        id: 'msg-8',
        role: 'assistant',
        content: '完整回复',
      },
      data: {},
    })

    expect(store.state.messagesBySession['session-1']).toEqual([
      expect.objectContaining({
        id: 'msg-8',
        content: '完整回复',
        streaming: false,
      }),
    ])
  })

  it('deletes a session and clears its local transcript state', async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.sessions = [
      { id: 'session-1', title: '会话一', updatedAt: '2026-04-13T00:00:00Z', status: 'idle' },
      { id: 'session-2', title: '会话二', updatedAt: '2026-04-12T00:00:00Z', status: 'idle' },
    ]
    store.state.currentSessionId = 'session-1'
    store.state.messagesBySession['session-1'] = [{ id: 'msg-1', content: 'hello' }]

    await store.deleteSession('session-1')

    expect(apiClient.deleteSession).toHaveBeenCalledWith('session-1')
    expect(store.state.sessions.map((session) => session.id)).toEqual(['session-2'])
    expect(store.state.messagesBySession['session-1']).toBeUndefined()
    expect(store.state.currentSessionId).toBe('session-2')
  })
})
