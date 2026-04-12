import { afterEach, describe, expect, it, vi } from 'vitest'

import { createApiClient } from './client.js'


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

    stream.close()
    eventSource.onerror()

    expect(onError).not.toHaveBeenCalled()
  })
})
