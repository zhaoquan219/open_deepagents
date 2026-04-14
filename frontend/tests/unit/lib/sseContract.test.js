import { describe, expect, it } from 'vitest'
import { normalizeStreamEnvelope } from '../../../src/lib/sseContract.js'

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

  it('keeps explicit running message.final payloads non-terminal', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-3b',
      type: 'message.final',
      run_id: 'run-9b',
      session_id: 'session-4b',
      status: 'running',
      message: {
        id: 'msg-9b',
        role: 'assistant',
        content: '让我先检查一下',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-3b',
      type: 'message.final',
      status: 'running',
      terminal: false,
      message: {
        id: 'msg-9b',
        content: '让我先检查一下',
      },
    })
  })

  it('maps step payloads with tool labels into tool events', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-4',
      type: 'step',
      run_id: 'run-10',
      session_id: 'session-5',
      label: 'tool.started',
      detail: 'echo_tool',
      data: {
        name: 'echo_tool',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-4',
      type: 'tool',
      runId: 'run-10',
      sessionId: 'session-5',
      detail: 'echo_tool',
    })
  })

  it('maps step payloads with message.completed into finalized assistant messages', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-5',
      type: 'step',
      run_id: 'run-11',
      session_id: 'session-6',
      label: 'message.completed',
      data: {
        text: 'echo:ping',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-5',
      type: 'message.final',
      status: 'running',
      terminal: false,
      message: {
        id: 'message:evt-5',
        content: 'echo:ping',
      },
    })
  })

  it('keeps intermediate message.completed steps as step events when no final text exists', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-5b',
      type: 'step',
      run_id: 'run-11b',
      session_id: 'session-6b',
      label: 'message.completed',
      detail: 'Model completed without a direct text payload.',
      data: {
        node: 'model',
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-5b',
      type: 'step',
      detail: 'Model completed without a direct text payload.',
    })
  })

  it('extracts finalized assistant text from nested output payloads', () => {
    const envelope = normalizeStreamEnvelope({
      event_id: 'evt-6',
      type: 'step',
      run_id: 'run-12',
      session_id: 'session-7',
      label: 'message.completed',
      data: {
        output: {
          messages: [{ content: [{ text: 'echo:ping' }] }],
        },
      },
    })

    expect(envelope).toMatchObject({
      eventId: 'evt-6',
      type: 'message.final',
      status: 'running',
      message: {
        id: 'message:evt-6',
        content: 'echo:ping',
      },
    })
  })
})
