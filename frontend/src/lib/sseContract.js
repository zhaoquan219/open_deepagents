export const STREAM_SCHEMA_VERSION = 'deepagents-ui.v1'

const allowedTypes = new Set([
  'status',
  'message.delta',
  'message.final',
  'step',
  'tool',
  'skill',
  'sandbox',
  'error',
])

function asString(value) {
  if (value === undefined || value === null) {
    return ''
  }
  return String(value)
}

function normalizeType(rawType, data) {
  const type = asString(rawType)
  if (type === 'message') {
    if (data.final || data.message) {
      return 'message.final'
    }
    return 'message.delta'
  }
  if (type === 'run') {
    return 'status'
  }
  return type
}

export function normalizeStreamEnvelope(payload) {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const data = payload.data && typeof payload.data === 'object' ? payload.data : {}
  const type = normalizeType(payload.type ?? payload.event_type ?? payload.eventType, data)
  const eventId = asString(payload.event_id ?? payload.eventId ?? payload.id)
  const runId = asString(payload.run_id ?? payload.runId ?? payload.run?.id ?? data.run_id ?? data.runId)
  const sessionId = asString(payload.session_id ?? payload.sessionId ?? payload.session?.id ?? data.session_id ?? data.sessionId)
  const timestamp = asString(payload.timestamp ?? payload.created_at ?? payload.createdAt ?? new Date().toISOString())
  const version = asString(payload.version ?? payload.schema_version ?? payload.schemaVersion ?? STREAM_SCHEMA_VERSION)
  const status = asString(payload.status ?? payload.run_status ?? payload.runStatus ?? data.status)
  const stepId = asString(payload.step_id ?? payload.stepId ?? data.step_id ?? data.stepId)
  const label = asString(payload.label ?? data.label ?? data.name ?? type)
  const detail = asString(payload.detail ?? data.detail ?? data.summary ?? data.message ?? '')
  const message = data.message && typeof data.message === 'object' ? data.message : null
  const delta = asString(data.delta ?? payload.delta)

  if (!eventId || !type || !allowedTypes.has(type)) {
    return null
  }

  return {
    version,
    eventId,
    type,
    runId,
    sessionId,
    timestamp,
    status,
    stepId,
    label,
    detail,
    data,
    delta,
    message,
  }
}
