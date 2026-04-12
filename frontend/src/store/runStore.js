import { reactive } from 'vue'

function createClientId() {
  return globalThis.crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function createEmptyRunState() {
  return {
    activeRun: null,
    error: '',
  }
}

export function createInitialRun(runId, sessionId) {
  return {
    runId,
    sessionId,
    status: 'running',
    connected: false,
    startedAt: new Date().toISOString(),
    timeline: [],
    lastEventId: '',
  }
}

function appendTimeline(activeRun, envelope, fallbackLabel) {
  activeRun.timeline.push({
    id: envelope.eventId,
    kind: envelope.type,
    label: envelope.label || fallbackLabel,
    detail: envelope.detail,
    status: envelope.status || 'in_progress',
    timestamp: envelope.timestamp,
  })
}

export function reduceRunState(activeRun, envelope) {
  const next = activeRun
    ? {
        ...activeRun,
        timeline: [...activeRun.timeline],
      }
    : createInitialRun(envelope.runId, envelope.sessionId)

  next.lastEventId = envelope.eventId

  if (envelope.type === 'status') {
    next.status = envelope.status || next.status
    appendTimeline(next, envelope, `Run ${next.status}`)
    return next
  }

  if (envelope.type === 'error') {
    next.status = 'failed'
    appendTimeline(next, envelope, 'Run failed')
    return next
  }

  if (envelope.type === 'message.final') {
    next.status = next.status === 'failed' ? 'failed' : 'completed'
    appendTimeline(next, envelope, 'Assistant message finalized')
    return next
  }

  appendTimeline(next, envelope, envelope.type)
  return next
}

export function createRunStore() {
  const state = reactive(createEmptyRunState())
  const seenEventIdsByRun = new Map()
  const lastEventIds = new Map()

  function ensureSeenSet(runId) {
    if (!seenEventIdsByRun.has(runId)) {
      seenEventIdsByRun.set(runId, new Set())
    }
    return seenEventIdsByRun.get(runId)
  }

  function beginRun({ runId, sessionId }) {
    state.error = ''
    state.activeRun = createInitialRun(runId, sessionId)
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
    return true
  }

  function markConnected(runId) {
    if (state.activeRun && state.activeRun.runId === runId) {
      state.activeRun.connected = true
    }
  }

  function markErrored(runId, message) {
    state.error = message
    if (!state.activeRun || state.activeRun.runId !== runId) {
      state.activeRun = createInitialRun(runId, '')
    }
    state.activeRun.status = 'failed'
    state.activeRun.timeline.push({
      id: createClientId(),
      kind: 'error',
      label: 'Run failed',
      detail: message,
      status: 'failed',
      timestamp: new Date().toISOString(),
    })
  }

  function getLastEventId(runId) {
    return lastEventIds.get(runId) || ''
  }

  function clear() {
    state.activeRun = null
    state.error = ''
  }

  return {
    state,
    beginRun,
    clear,
    consume,
    getLastEventId,
    markConnected,
    markErrored,
  }
}
