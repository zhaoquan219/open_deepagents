import { describe, expect, it } from 'vitest'
import { normalizeStreamEnvelope } from '../../../src/lib/sseContract.js'

describe('normalizeStreamEnvelope', () => {
  it('accepts the current backend SSE envelope', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-1',
      type: 'message.delta',
      run_id: 'run-1',
      session_id: 'session-1',
      timestamp: '2026-04-29T13:00:00Z',
      label: 'assistant.delta',
      detail: 'hello',
      data: {
        delta: 'hello',
      },
    })

    expect(envelope).toMatchObject({
      version: 'deepagents-ui',
      eventId: 'evt-1',
      type: 'message.delta',
      runId: 'run-1',
      sessionId: 'session-1',
      delta: 'hello',
    })
  })

  it('rejects unsupported or non-canonical event payloads', () => {
    expect(normalizeStreamEnvelope({ event_id: 'evt-2', type: 'unknown' })).toBeNull()
    expect(normalizeStreamEnvelope({ eventId: 'evt-2', type: 'message.delta' })).toBeNull()
    expect(normalizeStreamEnvelope({ event_id: 'evt-2', type: 'message' })).toBeNull()
  })

  it('reads finalized assistant messages from data.message', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-3',
      type: 'message.final',
      run_id: 'run-9',
      session_id: 'session-4',
      timestamp: '2026-04-29T13:00:00Z',
      label: 'assistant.message',
      detail: '最终回复',
      data: {
        message: {
          id: 'msg-9',
          role: 'assistant',
          content: '最终回复',
        },
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-3',
      type: 'message.final',
      status: 'completed',
      message: {
        id: 'msg-9',
        content: '最终回复',
      },
    })
  })

  it('marks terminal run statuses only when the backend sets terminal=true', () => {
    expect(
      normalizeStreamEnvelope({
        event_id: 'evt-4',
        type: 'status',
        run_id: 'run-1',
        session_id: 'session-1',
        timestamp: '2026-04-29T13:00:00Z',
        label: 'run.completed',
        detail: 'run.completed',
        data: { status: 'completed', terminal: true },
      }),
    ).toMatchObject({ terminal: true, status: 'completed' })

    expect(
      normalizeStreamEnvelope({
        event_id: 'evt-5',
        type: 'status',
        run_id: 'run-1',
        session_id: 'session-1',
        timestamp: '2026-04-29T13:00:01Z',
        label: 'run.completed',
        detail: "{'skills_metadata': []}",
        data: { status: 'completed', node: 'SkillsMiddleware.before_agent' },
      }),
    ).toMatchObject({ terminal: false, status: 'completed' })
  })
})
