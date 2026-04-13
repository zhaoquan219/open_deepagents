import { describe, expect, it } from 'vitest'
import { createInitialRun, createRunStore, reduceRunState } from './runStore.js'

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

  it('marks the run as connected and closed around stream lifecycle', () => {
    const store = createRunStore()
    store.beginRun({ runId: 'run-2', sessionId: 'session-2' })

    store.markConnected('run-2')
    store.markDisconnected('run-2', '最终回复已返回。')

    expect(store.state.activeRun).toMatchObject({
      runId: 'run-2',
      connectionState: 'closed',
      connected: false,
    })
    expect(store.state.activeRun.timeline.at(-1)).toMatchObject({
      label: '实时连接已关闭',
      detail: '最终回复已返回。',
    })
  })

  it('records client-side failures outside the active stream timeline', () => {
    const store = createRunStore()

    store.recordClientIssue({
      sessionId: 'session-9',
      label: '附件上传失败',
      detail: '网络中断',
    })

    expect(store.state.error).toBe('网络中断')
    expect(store.state.diagnostics).toEqual([
      expect.objectContaining({
        sessionId: 'session-9',
        label: '附件上传失败',
        detail: '网络中断',
        status: 'failed',
      }),
    ])
  })
})
