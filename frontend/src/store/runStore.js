import { reactive } from 'vue'

function createClientId() {
  return globalThis.crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function emptyState() {
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
    lastEventId: '',
  }
}

export function reduceRunState(activeRun, envelope) {
  const next = activeRun ? { ...activeRun } : createInitialRun(envelope.runId, envelope.sessionId)
  next.lastEventId = envelope.eventId

  if (envelope.type === 'connection') {
    next.connectionState = envelope.connectionState || next.connectionState
    next.connected = next.connectionState === 'open'
    return next
  }

  if (envelope.type === 'status') {
    next.status = envelope.status || next.status
    if (['completed', 'failed', 'cancelled'].includes(next.status)) {
      next.connected = false
      next.connectionState = 'closed'
      next.finishedAt = envelope.timestamp || next.finishedAt
    }
    return next
  }

  if (envelope.type === 'error') {
    next.status = 'failed'
    next.connected = false
    next.connectionState = 'error'
    next.finishedAt = envelope.timestamp || next.finishedAt
    next.lastError = envelope.detail || next.lastError
  }

  return next
}

export function createRunStore() {
  const state = reactive(emptyState())
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
    state.connectionState = 'connecting'
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
    if (state.activeRun?.runId !== runId || state.activeRun.connectionState === 'open') {
      return
    }
    state.activeRun.connected = true
    state.activeRun.connectionState = 'open'
    state.connectionState = 'open'
  }

  function markConnecting(runId) {
    if (state.activeRun?.runId !== runId || state.activeRun.connectionState === 'connecting') {
      return
    }
    state.activeRun.connected = false
    state.activeRun.connectionState = 'connecting'
    state.connectionState = 'connecting'
  }

  function markDisconnected(runId) {
    if (state.activeRun?.runId !== runId || state.activeRun.connectionState === 'closed') {
      return
    }
    state.activeRun.connected = false
    state.activeRun.connectionState = 'closed'
    state.connectionState = 'closed'
  }

  function markCancelling(runId) {
    if (state.activeRun?.runId !== runId) {
      return
    }
    state.error = ''
    state.activeRun.status = 'cancelling'
    state.activeRun.lastError = ''
  }

  function finish(runId, status, message = '') {
    if (!state.activeRun || state.activeRun.runId !== runId) {
      state.activeRun = createInitialRun(runId, '')
    }
    state.activeRun.status = status
    state.activeRun.connected = false
    state.activeRun.connectionState = status === 'failed' ? 'error' : 'closed'
    state.activeRun.finishedAt = new Date().toISOString()
    state.activeRun.lastError = status === 'failed' ? message : ''
    state.connectionState = state.activeRun.connectionState
    state.error = status === 'failed' ? message : ''
  }

  function recordClientIssue({ sessionId = '', label, detail }) {
    state.error = detail
    state.diagnostics = [
      ...state.diagnostics,
      { id: createClientId(), kind: 'client', label, detail, status: 'failed', sessionId },
    ]
  }

  function recordClientNotice({ clearError = true } = {}) {
    if (clearError) {
      state.error = ''
    }
  }

  function clear() {
    Object.assign(state, emptyState())
  }

  return {
    state,
    beginRun,
    clear,
    consume,
    getLastEventId: (runId) => lastEventIds.get(runId) || '',
    markConnected,
    markCancelling,
    markCompleted: (runId) => finish(runId, 'completed'),
    markCancelled: (runId) => finish(runId, 'cancelled'),
    markConnecting,
    markDisconnected,
    markErrored: (runId, message) => finish(runId, 'failed', message),
    recordClientIssue,
    recordClientNotice,
  }
}
