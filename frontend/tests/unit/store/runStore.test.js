import { describe, expect, it } from 'vitest'
import { createInitialRun, createRunStore, reduceRunState } from '../../../src/store/runStore.js'

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

  it('tracks a reconnecting state without marking the run as failed', () => {
    const store = createRunStore()
    store.beginRun({ runId: 'run-4', sessionId: 'session-4' })

    store.markConnected('run-4')
    store.markConnecting('run-4', '实时连接短暂中断，正在自动恢复。')

    expect(store.state.activeRun).toMatchObject({
      runId: 'run-4',
      status: 'running',
      connectionState: 'connecting',
      connected: false,
    })
    expect(store.state.activeRun.timeline.at(-1)).toMatchObject({
      label: '实时连接恢复中',
      detail: '实时连接短暂中断，正在自动恢复。',
      status: 'running',
    })
  })

  it('can recover a completed run after a duplicate terminal event replay', () => {
    const store = createRunStore()
    store.beginRun({ runId: 'run-5', sessionId: 'session-5' })
    store.markConnecting('run-5', '实时连接短暂中断，正在自动恢复。')

    store.markCompleted('run-5', '检测到终态事件重放，已同步最终回复。')

    expect(store.state.activeRun).toMatchObject({
      runId: 'run-5',
      status: 'completed',
      connectionState: 'closed',
      connected: false,
    })
    expect(store.state.activeRun.timeline.at(-1)).toMatchObject({
      label: '处理完成',
      detail: '检测到终态事件重放，已同步最终回复。',
      status: 'completed',
    })
  })

  it('coalesces consecutive message delta entries in the timeline', () => {
    const started = createInitialRun('run-3', 'session-3')
    const afterFirstDelta = reduceRunState(started, {
      eventId: 'evt-3',
      type: 'message.delta',
      runId: 'run-3',
      sessionId: 'session-3',
      timestamp: '2026-04-12T14:00:01.000Z',
      detail: '第一段',
    })
    const afterSecondDelta = reduceRunState(afterFirstDelta, {
      eventId: 'evt-4',
      type: 'message.delta',
      runId: 'run-3',
      sessionId: 'session-3',
      timestamp: '2026-04-12T14:00:02.000Z',
      detail: '第二段',
    })

    expect(afterSecondDelta.timeline).toHaveLength(1)
    expect(afterSecondDelta.timeline[0]).toMatchObject({
      kind: 'message.delta',
      aggregateCount: 2,
      detail: '已连续接收 2 段回复内容。',
      timestamp: '2026-04-12T14:00:02.000Z',
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
