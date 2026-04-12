import { describe, expect, it } from 'vitest'
import { createInitialRun, reduceRunState } from './runStore.js'

describe('runStore reducer', () => {
  it('tracks status updates and timeline entries', () => {
    const started = createInitialRun('run-1', 'session-1')
    const running = reduceRunState(started, {
      eventId: 'evt-1',
      type: 'status',
      runId: 'run-1',
      sessionId: 'session-1',
      timestamp: '2026-04-12T14:00:00.000Z',
      status: 'running',
      label: 'Run started',
      detail: 'Booting runtime',
    })
    const completed = reduceRunState(running, {
      eventId: 'evt-2',
      type: 'message.final',
      runId: 'run-1',
      sessionId: 'session-1',
      timestamp: '2026-04-12T14:00:10.000Z',
      label: 'Assistant message finalized',
      detail: 'Stored final transcript row',
    })

    expect(running.status).toBe('running')
    expect(completed.status).toBe('completed')
    expect(completed.timeline).toHaveLength(2)
  })
})
