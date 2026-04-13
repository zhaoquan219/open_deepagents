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

  it('reads finalized assistant messages from the top-level payload', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-3',
      type: 'message.final',
      run_id: 'run-9',
      session_id: 'session-4',
      message: {
        id: 'msg-9',
        role: 'assistant',
        content: '最终回复',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-3',
      type: 'message.final',
      runId: 'run-9',
      sessionId: 'session-4',
      message: {
        id: 'msg-9',
        content: '最终回复',
      },
    })
  })
})
