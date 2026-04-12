import { describe, expect, it } from 'vitest'
import { normalizeStreamEnvelope } from './sseContract.js'

describe('normalizeStreamEnvelope', () => {
  it('normalizes snake_case payloads', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-1',
      type: 'message',
      run_id: 'run-1',
      session_id: 'session-1',
      data: {
        delta: 'hello',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-1',
      type: 'message.delta',
      runId: 'run-1',
      sessionId: 'session-1',
      delta: 'hello',
    })
  })

  it('rejects unsupported event payloads', () => {
    expect(normalizeStreamEnvelope({ event_id: 'evt-2', type: 'unknown' })).toBeNull()
  })
})
