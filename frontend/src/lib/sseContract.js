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

function normalizeStepLikeType(label, detail, data) {
  const normalizedLabel = asString(label).toLowerCase()
  const normalizedDetail = asString(detail).toLowerCase()

  if (normalizedLabel === 'message.final') {
    return 'message.final'
  }
  if (normalizedLabel === 'message.completed') {
    return extractText(data.text ?? data.output ?? data.payload ?? data) ? 'message.final' : 'step'
  }
  if (normalizedLabel === 'message.delta') {
    return 'message.delta'
  }
  if (
    normalizedLabel.startsWith('tool.') ||
    normalizedDetail.includes('tool') ||
    normalizedDetail === 'tools'
  ) {
    return 'tool'
  }
  if (normalizedLabel.startsWith('skill.') || normalizedDetail.includes('skill')) {
    return 'skill'
  }
  if (normalizedLabel.startsWith('sandbox.') || normalizedDetail.includes('sandbox')) {
    return 'sandbox'
  }
  if (normalizedLabel === 'run.started' || normalizedLabel === 'run.completed') {
    return 'status'
  }
  if (normalizedLabel === 'run.failed') {
    return 'error'
  }
  return 'step'
}

function extractText(value) {
  if (value === undefined || value === null) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => extractText(item)).join('')
  }
  if (typeof value === 'object') {
    if ('messages' in value && Array.isArray(value.messages)) {
      return value.messages.map((message) => extractText(message)).join('')
    }
    if ('content' in value) {
      return extractText(value.content)
    }
    if (typeof value.text === 'string') {
      return value.text
    }
    if ('output' in value) {
      return extractText(value.output)
    }
    if ('payload' in value) {
      return extractText(value.payload)
    }
  }
  return ''
}

function normalizeType(rawType, label, detail, data) {
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
  if (type === 'step') {
    return normalizeStepLikeType(label, detail, data)
  }
  return type
}

function isTerminalAssistantMessage(label, payload, data) {
  const explicitStatus = asString(payload.status ?? data.status).toLowerCase()
  if (explicitStatus) {
    return explicitStatus === 'completed' || explicitStatus === 'failed'
  }

  if (typeof data.final === 'boolean') {
    return data.final
  }

  if (payload.type === 'message.final' || payload.event_type === 'message.final' || payload.eventType === 'message.final') {
    return true
  }

  const normalizedLabel = asString(label).toLowerCase()
  if (normalizedLabel === 'message.final') {
    return true
  }

  return Boolean(data.final)
}

function extractMessagePayload(payload, data, type, timestamp, runId, eventId, terminal) {
  const messagePayload = payload.message && typeof payload.message === 'object' ? payload.message : data.message
  if (messagePayload && typeof messagePayload === 'object') {
    return messagePayload
  }

  if (type !== 'message.final') {
    return null
  }

  const content = extractText(data.text ?? data.output ?? data.payload ?? data)
  if (!content) {
    return null
  }

  return {
    id: terminal ? `final:${runId || 'stream'}` : `message:${eventId || runId || 'stream'}`,
    role: 'assistant',
    content,
    createdAt: timestamp,
    attachments: [],
  }
}

export function normalizeStreamEnvelope(payload) {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const data = payload.data && typeof payload.data === 'object' ? payload.data : {}
  const rawType = payload.type ?? payload.event_type ?? payload.eventType
  const eventId = asString(payload.event_id ?? payload.eventId ?? payload.id)
  const runId = asString(payload.run_id ?? payload.runId ?? payload.run?.id ?? data.run_id ?? data.runId)
  const sessionId = asString(payload.session_id ?? payload.sessionId ?? payload.session?.id ?? data.session_id ?? data.sessionId)
  const timestamp = asString(payload.timestamp ?? payload.created_at ?? payload.createdAt ?? new Date().toISOString())
  const version = asString(payload.version ?? payload.schema_version ?? payload.schemaVersion ?? STREAM_SCHEMA_VERSION)
  const label = asString(payload.label ?? data.label ?? data.name ?? rawType)
  const detail = asString(payload.detail ?? data.detail ?? data.summary ?? data.node ?? data.message ?? '')
  const type = normalizeType(rawType, label, detail, data)
  const terminalAssistantMessage = isTerminalAssistantMessage(label, payload, data)
  const status =
    asString(payload.status ?? payload.run_status ?? payload.runStatus ?? data.status) ||
    (type === 'message.final' ? (terminalAssistantMessage ? 'completed' : 'running') : '')
  const stepId = asString(payload.step_id ?? payload.stepId ?? data.step_id ?? data.stepId)
  const delta = asString(data.delta ?? payload.delta) || extractText(data.chunk ?? data.text)
  const message = extractMessagePayload(payload, data, type, timestamp, runId, eventId, terminalAssistantMessage)

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
    terminal: terminalAssistantMessage,
  }
}
