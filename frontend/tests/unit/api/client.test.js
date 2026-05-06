import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildUploadContentUrl, createApiClient, normalizeRuntimeOptions } from '../../../src/api/client.js'
import { uiCopy } from '../../../src/lib/copy.js'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('createApiClient session normalization', () => {
  it('normalizes runtime options and sends model selection only', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          default_model_id: 'openai/gpt-5-4',
          models: [{ id: 'openai/gpt-5-4', name: 'GPT-5.4', provider_name: 'OpenAI' }],
          profiles: [{ id: 'default', label: 'Default', subagent_ids: ['reviewer'] }],
          subagents: [{ id: 'reviewer', name: 'reviewer', description: 'Review code' }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        body: new globalThis.ReadableStream({
          start(controller) {
            controller.enqueue(
              new globalThis.TextEncoder().encode(
                'id: 1\ndata: {"event_id":"1","type":"status","run_id":"run-1","session_id":"session-1","status":"running"}\n\n',
              ),
            )
            controller.close()
          },
        }),
      })

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = createApiClient('/api')
    const options = await client.getRuntimeOptions()
    await client.startRun({
      sessionId: 'session-1',
      prompt: 'hello',
      attachments: [],
      modelId: options.defaultModelId,
    })

    expect(options.models[0]).toEqual(
      expect.objectContaining({ id: 'openai/gpt-5-4', providerName: 'OpenAI' }),
    )
    expect(options).not.toHaveProperty('profiles')
    expect(options).not.toHaveProperty('subagents')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/sessions/session-1/runs/stream')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      prompt: 'hello',
      model_id: 'openai/gpt-5-4',
    })
  })

  it('fails closed when the event stream ends without a run id', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }
    const onError = vi.fn()

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 201,
        body: new globalThis.ReadableStream({
          start(controller) {
            controller.enqueue(
              new globalThis.TextEncoder().encode(
                'id: 1\ndata: {"event_id":"1","type":"status","session_id":"session-1","status":"running"}\n\n',
              ),
            )
            controller.close()
          },
        }),
      })),
    )

    await expect(
      createApiClient('/api').startRun({
        sessionId: 'session-1',
        prompt: 'hello',
        attachments: [],
        modelId: '',
        onError,
      }),
    ).rejects.toThrow(uiCopy.api.invalidStreamEvent)
    expect(onError).not.toHaveBeenCalled()
  })

  it('normalizes runtime option fallback payload shapes', () => {
    expect(normalizeRuntimeOptions({ data: { models: [{ id: 'm1' }] } })).toEqual(
      expect.objectContaining({
        defaultModelId: 'm1',
        models: [expect.objectContaining({ id: 'm1' })],
      }),
    )
  })

  it('normalizes default english session titles to chinese', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [{
            id: 'session-1',
            title: 'New session',
            updated_at: '2026-04-13T00:00:00Z',
            metadata: { source: 'test' },
          }],
        }),
      })),
    )

    const sessions = await createApiClient('/api').listSessions()

    expect(sessions).toEqual([
      expect.objectContaining({
        id: 'session-1',
        title: uiCopy.sessionTitles.defaultTitle,
        metadata: { source: 'test' },
      }),
    ])
  })

  it('updates session metadata through the session patch route', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        id: 'session-2',
        title: 'Prompt Session',
        updated_at: '2026-04-27T00:00:00Z',
        status: 'idle',
        metadata: { source: 'manual' },
      }),
    }))

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('fetch', fetchMock)

    const session = await createApiClient('/api').updateSession('session-2', {
      metadata: { source: 'manual' },
    })

    expect(session).toEqual(expect.objectContaining({
      id: 'session-2',
      metadata: { source: 'manual' },
    }))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      metadata: { source: 'manual' },
    })
  })

  it('uses centralized attachment fallback copy when attachment names are missing', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          events: [{
            id: 'event-1',
            seq: 1,
            session_id: 'session-1',
            kind: 'user.message',
            role: 'user',
            content: 'hello',
            payload: { attachments: [{}] },
          }],
        }),
      })),
    )

    const messages = await createApiClient('/api').getSessionMessages('session-1')

    expect(messages[0].attachments).toEqual([
      expect.objectContaining({
        name: uiCopy.api.unnamedAttachment,
        downloadUrl: '',
      }),
    ])
  })

  it('projects message events and ignores runtime-only events', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          events: [
            { id: 'step-1', seq: 1, kind: 'tool.start', payload: { input: {} } },
            {
              id: 'msg-1',
              seq: 2,
              session_id: 'session-1',
              kind: 'assistant.message',
              role: 'assistant',
              content: 'done',
              payload: {},
            },
          ],
        }),
      })),
    )

    const messages = await createApiClient('/api').getSessionMessages('session-1')

    expect(messages).toEqual([
      expect.objectContaining({
        id: 'msg-1',
        role: 'assistant',
        content: 'done',
      }),
    ])
    expect(messages).toHaveLength(1)
  })

  it('replays historical process events into the assistant message shape used by live runs', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          events: [
            {
              id: 'user-1',
              seq: 1,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'user.message',
              type: 'step',
              role: 'user',
              content: '检查项目',
              payload: { message: { role: 'user', content: '检查项目' } },
              created_at: '2026-05-05T00:00:00.000Z',
            },
            {
              id: 'tool-1',
              seq: 2,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'tool.completed',
              type: 'tool',
              tool_name: 'read_file',
              payload: {
                tool_name: 'read_file',
                status: 'completed',
                output: { text: '读取完成' },
              },
              created_at: '2026-05-05T00:00:01.000Z',
            },
            {
              id: 'sandbox-1',
              seq: 3,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'sandbox.completed',
              type: 'sandbox',
              payload: {
                status: 'completed',
                input: { command: 'npm test' },
                output: { stdout: 'passed' },
              },
              created_at: '2026-05-05T00:00:02.000Z',
            },
            {
              id: 'delta-1',
              seq: 4,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'assistant.delta',
              type: 'message.delta',
              role: 'assistant',
              content: '验证',
              payload: { delta: '验证' },
              created_at: '2026-05-05T00:00:03.000Z',
            },
            {
              id: 'delta-2',
              seq: 5,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'assistant.delta',
              type: 'message.delta',
              role: 'assistant',
              content: '完成',
              payload: { delta: '完成' },
              created_at: '2026-05-05T00:00:04.000Z',
            },
            {
              id: 'assistant-1',
              seq: 6,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'assistant.message',
              type: 'message.final',
              role: 'assistant',
              payload: { message: { role: 'assistant', content: '验证完成' } },
              created_at: '2026-05-05T00:00:05.000Z',
            },
            {
              id: 'done-1',
              seq: 7,
              session_id: 'session-history',
              run_id: 'run-history',
              kind: 'run.completed',
              type: 'status',
              payload: { status: 'completed', terminal: true },
              created_at: '2026-05-05T00:00:06.000Z',
            },
          ],
        }),
      })),
    )

    const messages = await createApiClient('/api').getSessionMessages('session-history')

    expect(messages).toEqual([
      expect.objectContaining({ role: 'user', content: '检查项目' }),
      expect.objectContaining({
        role: 'assistant',
        content: '验证完成',
        streaming: false,
        processes: [
          expect.objectContaining({ kind: 'tool', title: 'read_file', summary: '读取完成' }),
          expect.objectContaining({ kind: 'sandbox', title: 'npm test', summary: expect.stringContaining('passed') }),
        ],
      }),
    ])
  })

  it('keeps a compact assistant process row when historical events end without a final message', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          events: [
            {
              id: 'user-1',
              seq: 1,
              session_id: 'session-terminal',
              run_id: 'run-terminal',
              kind: 'user.message',
              type: 'step',
              role: 'user',
              content: '只返回流式片段',
              payload: { message: { role: 'user', content: '只返回流式片段' } },
              created_at: '2026-05-05T01:00:00.000Z',
            },
            {
              id: 'skill-1',
              seq: 2,
              session_id: 'session-terminal',
              run_id: 'run-terminal',
              kind: 'skill.completed',
              type: 'skill',
              payload: {
                status: 'completed',
                skills: [{ name: 'runner', description: 'Run tasks' }],
              },
              created_at: '2026-05-05T01:00:01.000Z',
            },
            {
              id: 'delta-1',
              seq: 3,
              session_id: 'session-terminal',
              run_id: 'run-terminal',
              kind: 'assistant.delta',
              type: 'message.delta',
              role: 'assistant',
              content: '片段',
              payload: { delta: '片段' },
              created_at: '2026-05-05T01:00:02.000Z',
            },
            {
              id: 'done-1',
              seq: 4,
              session_id: 'session-terminal',
              run_id: 'run-terminal',
              kind: 'run.completed',
              type: 'status',
              payload: { status: 'completed', terminal: true },
              created_at: '2026-05-05T01:00:03.000Z',
            },
          ],
        }),
      })),
    )

    const messages = await createApiClient('/api').getSessionMessages('session-terminal')

    expect(messages).toEqual([
      expect.objectContaining({ role: 'user', content: '只返回流式片段' }),
      expect.objectContaining({
        id: 'stream:run-terminal',
        role: 'assistant',
        content: '片段',
        streaming: false,
        processes: [
          expect.objectContaining({
            kind: 'skill',
            title: '已加载 1 个技能',
            summary: 'runner: Run tasks',
          }),
        ],
      }),
    ])
  })

  it('does not synthesize removed upload download URLs', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })

    expect(buildUploadContentUrl('/api', 'upload-1')).toBe('')
  })

  it('sends the bearer token without anonymous actor headers', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ sessions: [] }),
    }))

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('fetch', fetchMock)

    await createApiClient('/api').listSessions()

    expect(storage.setItem).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer token-123')
    expect(fetchMock.mock.calls[0][1].headers['X-Deepagents-Actor-Id']).toBeUndefined()
  })
})

describe('createApiClient fallback errors', () => {
  it('uses centralized request failure copy when the backend returns an empty error body', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 503,
        text: async () => '',
      })),
    )

    await expect(createApiClient('/api').listSessions()).rejects.toThrow(uiCopy.api.requestFailedStatus(503))
  })

  it('keeps selected files local because upload endpoints are not in the current backend API', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    const file = Object.assign(new globalThis.Blob(['hello'], { type: 'text/plain' }), {
      name: 'notes.txt',
    })

    await expect(createApiClient('/api').uploadFiles('session-1', [file])).resolves.toEqual([
      expect.objectContaining({ name: 'notes.txt', status: 'submitted', downloadUrl: '' }),
    ])
  })

  it('treats deleting local pending uploads as a client-only operation', async () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    await expect(createApiClient('/api').deleteUpload('upload-1')).resolves.toBeUndefined()
  })
})
