import { describe, expect, it, vi } from 'vitest'
import {
  createSessionStore,
  finalizeAssistantMessage,
  mergeAssistantDelta,
  reconcileMessages,
} from '../../../src/store/sessionStore.js'

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

  it('updates an existing assistant row when the same message id is replayed', () => {
    const finalized = finalizeAssistantMessage(
      [
        {
          id: 'msg-final',
          role: 'assistant',
          content: '旧内容',
          createdAt: '2026-04-13T10:00:00.000Z',
          attachments: [],
          streaming: false,
        },
      ],
      {
        runId: 'run-2',
        message: {
          id: 'msg-final',
          content: '新内容',
        },
      },
    )

    expect(finalized).toEqual([
      expect.objectContaining({
        id: 'msg-final',
        content: '新内容',
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

  it('preserves newer local messages when a session refresh returns a stale prefix', () => {
    const reconciled = reconcileMessages(
      [
        { id: 'user-1', role: 'user', content: '看下你本地有哪些文件？', attachments: [] },
        { id: 'assistant-1', role: 'assistant', content: '当前目录为空，没有文件。', attachments: [] },
      ],
      [{ id: 'server-user-1', role: 'user', content: '看下你本地有哪些文件？', attachments: [] }],
    )

    expect(reconciled).toEqual([
      expect.objectContaining({ role: 'user', content: '看下你本地有哪些文件？' }),
      expect.objectContaining({ role: 'assistant', content: '当前目录为空，没有文件。' }),
    ])
  })

  it('merges a stale fetch without dropping local assistant output', async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => [
        { id: 'server-user-1', role: 'user', content: '第一问', attachments: [] },
      ]),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)
    store.state.currentSessionId = 'session-1'
    store.state.messagesBySession['session-1'] = [
      { id: 'local-user-1', role: 'user', content: '第一问', attachments: [] },
      { id: 'local-assistant-1', role: 'assistant', content: '第一答', attachments: [] },
    ]

    await store.selectSession('session-1')

    expect(store.state.messagesBySession['session-1']).toEqual([
      expect.objectContaining({ role: 'user', content: '第一问' }),
      expect.objectContaining({ role: 'assistant', content: '第一答' }),
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

  it('distills a placeholder session title once and preserves it on later prompts', () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.sessions = [
      { id: 'session-1', title: '新会话', updatedAt: '2026-04-13T00:00:00Z', status: 'idle' },
    ]

    store.addOptimisticUserMessage('session-1', '第一行标题候选\n第二行不应进入标题')
    expect(store.state.sessions[0].title).toBe('第一行标题候选')

    store.addOptimisticUserMessage('session-1', '第二条消息不能覆盖已有标题')
    expect(store.state.sessions[0].title).toBe('第一行标题候选')
  })

  it('clears pending uploads after a successful submission without stripping the user message attachments', () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.pendingUploadsBySession['session-1'] = [
      { id: 'upload-1', name: 'notes.txt', size: 12, status: 'uploaded' },
    ]

    store.addOptimisticUserMessage('session-1', '请看下这个文件里有什么')
    store.clearPendingUploads('session-1')

    expect(store.getPendingUploads('session-1')).toEqual([])
    expect(store.state.messagesBySession['session-1']).toEqual([
      expect.objectContaining({
        role: 'user',
        attachments: [{ id: 'upload-1', name: 'notes.txt', size: 12, status: 'uploaded' }],
      }),
    ])
  })

  it('removes a pending upload from local state after the backend delete succeeds', async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      deleteUpload: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.pendingUploadsBySession['session-1'] = [
      { id: 'upload-1', name: 'notes.txt', size: 12, status: 'uploaded' },
      { id: 'upload-2', name: 'spec.pdf', size: 24, status: 'uploaded' },
    ]

    const result = await store.deletePendingUpload('session-1', 'upload-1')

    expect(result).toEqual({ ok: true })
    expect(apiClient.deleteUpload).toHaveBeenCalledWith('upload-1')
    expect(store.getPendingUploads('session-1')).toEqual([
      { id: 'upload-2', name: 'spec.pdf', size: 24, status: 'uploaded' },
    ])
    expect(store.state.uploadError).toBe('')
  })

  it('clears stale upload errors when session context changes or uploads are consumed', async () => {
    const apiClient = {
      createSession: vi.fn(async () => ({
        id: 'session-new',
        title: '新会话',
        updatedAt: '2026-04-13T00:00:00Z',
      })),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    }
    const store = createSessionStore(apiClient)

    store.state.uploadError = 'File too large'
    await store.createSession()
    expect(store.state.uploadError).toBe('')

    store.state.uploadError = 'File too large'
    await store.selectSession('session-new')
    expect(store.state.uploadError).toBe('')

    store.state.uploadError = 'File too large'
    store.clearPendingUploads('session-new')
    expect(store.state.uploadError).toBe('')
  })
})
