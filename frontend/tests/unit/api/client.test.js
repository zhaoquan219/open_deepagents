import { afterEach, describe, expect, it, vi } from 'vitest'

import { createApiClient } from '../../../src/api/client.js'
import { uiCopy } from '../../../src/lib/copy.js'


class FakeEventSource {
  static CLOSED = 2
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = 1
    this.onopen = null
    this.onmessage = null
    this.onerror = null
    FakeEventSource.instances.push(this)
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }
}

afterEach(() => {
  FakeEventSource.instances = []
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('createApiClient.openRunStream', () => {
  it('does not report an error after the stream is intentionally closed', () => {
    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('EventSource', FakeEventSource)

    const onError = vi.fn()
    const stream = createApiClient('/api').openRunStream('run-1', {
      onError,
    })

    const eventSource = FakeEventSource.instances[0]
    expect(storage.getItem).toHaveBeenCalledWith('deepagents.admin.token')
    expect(eventSource.url).toContain('access_token=token-123')
    expect(eventSource.url).not.toContain('actor_id=')

    stream.close()
    eventSource.onerror()

    expect(onError).not.toHaveBeenCalled()
  })

  it('waits for the recovery window before reporting a stream failure', () => {
    vi.useFakeTimers()

    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('EventSource', FakeEventSource)

    const onError = vi.fn()
    const onRetry = vi.fn()
    createApiClient('/api').openRunStream('run-2', {
      onError,
      onRetry,
      reconnectGraceMs: 3000,
    })

    const eventSource = FakeEventSource.instances[0]
    eventSource.onerror()

    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()

    vi.advanceTimersByTime(2999)
    expect(onError).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: uiCopy.api.streamRecoveryFailed }))
  })

  it('clears the recovery window when the stream reconnects in time', () => {
    vi.useFakeTimers()

    const storage = {
      getItem: vi.fn(() => 'token-123'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173' },
      localStorage: storage,
    })
    vi.stubGlobal('EventSource', FakeEventSource)

    const onError = vi.fn()
    const onOpen = vi.fn()
    createApiClient('/api').openRunStream('run-3', {
      onError,
      onOpen,
      reconnectGraceMs: 3000,
    })

    const eventSource = FakeEventSource.instances[0]
    eventSource.onerror()
    vi.advanceTimersByTime(1000)
    eventSource.onopen()
    vi.advanceTimersByTime(3000)

    expect(onOpen).toHaveBeenCalledWith({ resumed: true })
    expect(onError).not.toHaveBeenCalled()
  })
})

describe('createApiClient session normalization', () => {
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
          sessions: [{ id: 'session-1', title: 'New session', updated_at: '2026-04-13T00:00:00Z' }],
        }),
      })),
    )

    const sessions = await createApiClient('/api').listSessions()

    expect(sessions).toEqual([
      expect.objectContaining({
        id: 'session-1',
        title: uiCopy.sessionTitles.defaultTitle,
      }),
    ])
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
          messages: [{ id: 'msg-1', attachments: [{}] }],
        }),
      })),
    )

    const messages = await createApiClient('/api').getSessionMessages('session-1')

    expect(messages[0].attachments).toEqual([
      expect.objectContaining({
        name: uiCopy.api.unnamedAttachment,
      }),
    ])
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

  it('uses centralized upload failure copy when upload endpoints return an empty error body', async () => {
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
        status: 400,
        text: async () => '',
      })),
    )

    const file = Object.assign(new globalThis.Blob(['hello'], { type: 'text/plain' }), {
      name: 'notes.txt',
    })

    await expect(createApiClient('/api').uploadFiles('session-1', [file])).rejects.toThrow(
      uiCopy.api.uploadFailedForFile('notes.txt'),
    )
  })

  it('uses backend detail when delete upload is rejected', async () => {
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
        status: 409,
        text: async () => JSON.stringify({ detail: 'Sent uploads cannot be deleted' }),
      })),
    )

    await expect(createApiClient('/api').deleteUpload('upload-1')).rejects.toThrow(
      'Sent uploads cannot be deleted',
    )
  })
})
