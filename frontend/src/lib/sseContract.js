export const STREAM_SCHEMA_VERSION = 'deepagents-ui'

const allowedTypes = new Set([
  'status',
  'message.delta',
  'message.final',
  'step',
  'tool',
  'skill',
  'subagent',
  'sandbox',
  'error',
])

function asString(value) {
  if (value === undefined || value === null) {
    return ''
  }
  return String(value)
}

function messageFromData(data) {
  const message = data.message
  if (!message || typeof message !== 'object') {
    return null
  }
  return message
}

function statusFromLabel(type, label) {
  if (type === 'message.final' || label.endsWith('.completed')) {
    return 'completed'
  }
  if (label.endsWith('.started')) {
    return 'in_progress'
  }
  return ''
}

export function normalizeStreamEnvelope(payload) {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const data = payload.data && typeof payload.data === 'object' ? payload.data : {}
  const eventId = asString(payload.event_id)
  const type = asString(payload.type)
  const runId = asString(payload.run_id)
  const sessionId = asString(payload.session_id)
  const timestamp = asString(payload.timestamp)
  const label = asString(payload.label)
  const detail = asString(payload.detail)

  if (!eventId || !type || !allowedTypes.has(type)) {
    return null
  }

  const status = asString(data.status) || statusFromLabel(type, label)

  return {
    version: STREAM_SCHEMA_VERSION,
    eventId,
    type,
    runId,
    sessionId,
    timestamp,
    status,
    stepId: '',
    label,
    detail,
    data,
    delta: type === 'message.delta' ? asString(data.delta || data.text) : '',
    message: type === 'message.final' ? messageFromData(data) : null,
    terminal: type === 'error' || (type === 'status' && data.terminal === true),
  }
}
