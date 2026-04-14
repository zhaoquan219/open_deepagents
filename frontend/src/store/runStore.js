import { reactive } from 'vue'

function createClientId() {
  return globalThis.crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function createEmptyRunState() {
  return {
    activeRun: null,
    diagnostics: [],
    connectionState: 'idle',
    error: '',
  }
}

export function createInitialRun(runId, sessionId) {
  return {
    runId,
    sessionId,
    status: 'running',
    connected: false,
    connectionState: 'connecting',
    startedAt: new Date().toISOString(),
    finishedAt: '',
    lastError: '',
    timeline: [],
    lastEventId: '',
  }
}

function statusTimelineLabel(status) {
  if (status === 'queued') {
    return '排队中'
  }
  if (status === 'running') {
    return '处理中'
  }
  if (status === 'completed') {
    return '处理完成'
  }
  if (status === 'failed') {
    return '处理失败'
  }
  return '状态更新'
}

function shouldCollapseDuplicate(previous, entry) {
  return (
    previous &&
    previous.kind === entry.kind &&
    previous.label === entry.label &&
    previous.detail === entry.detail &&
    previous.status === entry.status
  )
}

function appendTimeline(activeRun, envelope, fallbackLabel) {
  const entry = {
    id: envelope.eventId || createClientId(),
    kind: envelope.type,
    label: envelope.label || fallbackLabel,
    detail: envelope.detail,
    status: envelope.status || 'in_progress',
    timestamp: envelope.timestamp || new Date().toISOString(),
    aggregateCount: 1,
  }
  const previous = activeRun.timeline.at(-1)

  if (previous?.kind === 'message.delta' && entry.kind === 'message.delta') {
    const aggregateCount = Number(previous.aggregateCount || 1) + 1
    previous.aggregateCount = aggregateCount
    previous.timestamp = entry.timestamp
    previous.status = entry.status
    previous.detail = `已连续接收 ${aggregateCount} 段回复内容。`
    return
  }

  if (shouldCollapseDuplicate(previous, entry)) {
    previous.aggregateCount = Number(previous.aggregateCount || 1) + 1
    previous.timestamp = entry.timestamp
    return
  }

  activeRun.timeline.push(entry)
}

export function reduceRunState(activeRun, envelope) {
  const next = activeRun
    ? {
        ...activeRun,
        timeline: [...activeRun.timeline],
      }
    : createInitialRun(envelope.runId, envelope.sessionId)

  next.lastEventId = envelope.eventId

  if (envelope.type === 'connection') {
    next.connectionState = envelope.connectionState || next.connectionState
    next.connected = next.connectionState === 'open'
    appendTimeline(next, envelope, envelope.label || '连接状态更新')
    return next
  }

  if (envelope.type === 'status') {
    next.status = envelope.status || next.status
    if (next.status === 'completed' || next.status === 'failed') {
      next.finishedAt = envelope.timestamp || next.finishedAt
    }
    appendTimeline(next, envelope, statusTimelineLabel(next.status))
    return next
  }

  if (envelope.type === 'error') {
    next.status = 'failed'
    next.connectionState = 'error'
    next.connected = false
    next.finishedAt = envelope.timestamp || next.finishedAt
    next.lastError = envelope.detail || next.lastError
    appendTimeline(next, envelope, '处理失败')
    return next
  }

  if (envelope.type === 'message.final') {
    next.status = next.status === 'failed' ? 'failed' : 'completed'
    next.finishedAt = envelope.timestamp || next.finishedAt
    appendTimeline(next, envelope, '最终回复已写入会话')
    return next
  }

  appendTimeline(next, envelope, envelope.type)
  return next
}

export function createRunStore() {
  const state = reactive(createEmptyRunState())
  const seenEventIdsByRun = new Map()
  const lastEventIds = new Map()

  function appendDiagnostic({ label, detail, status = 'info', sessionId = '', runId = '' }) {
    state.diagnostics = [
      ...state.diagnostics,
      {
        id: createClientId(),
        kind: 'client',
        label,
        detail,
        status,
        sessionId,
        runId,
        timestamp: new Date().toISOString(),
      },
    ].slice(-12)
  }

  function ensureSeenSet(runId) {
    if (!seenEventIdsByRun.has(runId)) {
      seenEventIdsByRun.set(runId, new Set())
    }
    return seenEventIdsByRun.get(runId)
  }

  function beginRun({ runId, sessionId }) {
    state.error = ''
    state.activeRun = createInitialRun(runId, sessionId)
    state.connectionState = 'connecting'
    appendTimeline(state.activeRun, {
      type: 'status',
      label: '运行已启动',
      detail: '已提交请求，正在建立实时连接。',
      status: 'running',
      timestamp: state.activeRun.startedAt,
    })
  }

  function consume(envelope) {
    const runId = String(envelope.runId || state.activeRun?.runId || '')
    if (!runId) {
      return false
    }

    const seen = ensureSeenSet(runId)
    if (seen.has(envelope.eventId)) {
      return false
    }

    seen.add(envelope.eventId)
    lastEventIds.set(runId, envelope.eventId)
    state.activeRun = reduceRunState(state.activeRun, envelope)
    state.connectionState = state.activeRun?.connectionState || state.connectionState
    return true
  }

  function markConnected(runId) {
    if (state.activeRun && state.activeRun.runId === runId) {
      if (state.activeRun.connectionState === 'open') {
        return
      }
      state.activeRun.connected = true
      state.activeRun.connectionState = 'open'
      state.connectionState = 'open'
      appendTimeline(state.activeRun, {
        type: 'connection',
        label: '实时连接已建立',
        detail: '已连接到实时事件流，正在接收运行状态。',
        status: 'completed',
        timestamp: new Date().toISOString(),
        connectionState: 'open',
      })
    }
  }

  function markConnecting(runId, detail = '实时连接恢复中。') {
    if (!state.activeRun || state.activeRun.runId !== runId) {
      return
    }
    if (state.activeRun.connectionState === 'connecting') {
      return
    }
    state.activeRun.connected = false
    state.activeRun.connectionState = 'connecting'
    state.connectionState = 'connecting'
    appendTimeline(state.activeRun, {
      type: 'connection',
      label: '实时连接恢复中',
      detail,
      status: 'running',
      timestamp: new Date().toISOString(),
      connectionState: 'connecting',
    })
  }

  function markDisconnected(runId, detail = '实时连接已关闭。') {
    if (!state.activeRun || state.activeRun.runId !== runId) {
      return
    }
    if (state.activeRun.connectionState === 'closed') {
      return
    }
    state.activeRun.connected = false
    state.activeRun.connectionState = 'closed'
    state.connectionState = 'closed'
    appendTimeline(state.activeRun, {
      type: 'connection',
      label: '实时连接已关闭',
      detail,
      status: state.activeRun.status === 'failed' ? 'failed' : 'completed',
      timestamp: new Date().toISOString(),
      connectionState: 'closed',
    })
  }

  function markCompleted(runId, detail = '本轮处理已完成。') {
    if (!state.activeRun || state.activeRun.runId !== runId) {
      return
    }
    state.error = ''
    state.activeRun.status = 'completed'
    state.activeRun.connected = false
    state.activeRun.connectionState = 'closed'
    state.activeRun.finishedAt = new Date().toISOString()
    state.connectionState = 'closed'
    appendTimeline(state.activeRun, {
      type: 'status',
      label: '处理完成',
      detail,
      status: 'completed',
      timestamp: state.activeRun.finishedAt,
    })
  }

  function markErrored(runId, message) {
    state.error = message
    state.connectionState = 'error'
    if (!state.activeRun || state.activeRun.runId !== runId) {
      state.activeRun = createInitialRun(runId, '')
    }
    state.activeRun.status = 'failed'
    state.activeRun.connected = false
    state.activeRun.connectionState = 'error'
    state.activeRun.lastError = message
    state.activeRun.finishedAt = new Date().toISOString()
    state.activeRun.timeline.push({
      id: createClientId(),
      kind: 'error',
      label: '运行失败',
      detail: message,
      status: 'failed',
      timestamp: new Date().toISOString(),
    })
  }

  function recordClientIssue({ sessionId = '', label, detail }) {
    state.error = detail
    appendDiagnostic({
      label,
      detail,
      status: 'failed',
      sessionId,
    })
  }

  function recordClientNotice({ sessionId = '', runId = '', label, detail, status = 'info' }) {
    appendDiagnostic({
      label,
      detail,
      status,
      sessionId,
      runId,
    })
  }

  function getLastEventId(runId) {
    return lastEventIds.get(runId) || ''
  }

  function clear() {
    state.activeRun = null
    state.diagnostics = []
    state.connectionState = 'idle'
    state.error = ''
  }

  return {
    state,
    beginRun,
    clear,
    consume,
    getLastEventId,
    markConnected,
    markCompleted,
    markConnecting,
    markDisconnected,
    markErrored,
    recordClientIssue,
    recordClientNotice,
  }
}
