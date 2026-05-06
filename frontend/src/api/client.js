import { uiCopy } from '../lib/copy.js'
import { logEntryFromEnvelope } from '../lib/processLog.js'
import { normalizeSessionTitle } from '../lib/sessionTitle.js'
import { normalizeStreamEnvelope } from '../lib/sseContract.js'

function resolveApiBaseUrl() {
  if (import.meta.env.VITE_API_BASE_URL) {
    return String(import.meta.env.VITE_API_BASE_URL)
  }
  const meta = document.querySelector('meta[name="api-base-url"]')
  return meta?.getAttribute('content') || '/api'
}

function resolveAccessToken() {
  return window.localStorage.getItem('deepagents.admin.token') || ''
}

function buildUploadContentUrl(baseUrl, uploadId) {
  void baseUrl
  void uploadId
  return ''
}

function unwrapCollection(payload, preferredKey) {
  if (Array.isArray(payload)) {
    return payload
  }

  if (!payload || typeof payload !== 'object') {
    return []
  }

  const direct = payload[preferredKey] ?? payload.items ?? payload.data
  if (Array.isArray(direct)) {
    return direct
  }

  return []
}

function normalizeContent(value) {
  if (value === undefined || value === null) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeContent(item)).join('')
  }
  if (typeof value === 'object') {
    if ('content' in value) {
      return normalizeContent(value.content)
    }
    if (typeof value.text === 'string') {
      return value.text
    }
    if (Array.isArray(value.parts)) {
      return value.parts.map((part) => normalizeContent(part)).join('')
    }
  }
  return String(value)
}

function normalizeAttachments(attachments, baseUrl = '') {
  if (!Array.isArray(attachments)) {
    return []
  }

  return attachments.map((attachment, index) => {
    const rawId = attachment?.id ?? attachment?.attachment_id ?? attachment?.attachmentId
    const id = String(rawId ?? `attachment-${index}`)
    return {
      id,
      name: String(
        attachment?.name ?? attachment?.filename ?? attachment?.title ?? uiCopy.api.unnamedAttachment,
      ),
      size: Number(attachment?.size ?? attachment?.size_bytes ?? attachment?.sizeBytes ?? 0),
      status: String(attachment?.status ?? 'uploaded'),
      downloadUrl: baseUrl && rawId ? buildUploadContentUrl(baseUrl, id) : String(attachment?.download_url ?? attachment?.downloadUrl ?? ''),
    }
  })
}

function normalizeRuntimeOptions(payload) {
  const record = payload?.data ?? payload ?? {}
  const models = unwrapCollection(record.models ?? [], 'models').map((model) => ({
    id: String(model.id ?? ''),
    name: String(model.name ?? model.label ?? model.id ?? ''),
    provider: String(model.provider ?? ''),
    providerName: String(model.provider_name ?? model.providerName ?? model.provider ?? ''),
  })).filter((model) => model.id).map((model) => ({
    ...model,
    displayName: model.provider ? `${model.provider}/${model.id.split('/').at(-1)}` : model.id,
  }))
  return {
    defaultModelId: String(record.default_model_id ?? record.defaultModelId ?? models[0]?.id ?? ''),
    models,
  }
}

function normalizeMessageFromEvent(event, baseUrl = '') {
  const kind = String(event?.kind ?? event?.label ?? '')
  const role = String(event?.role ?? event?.payload?.message?.role ?? '')
  if (!['user.message', 'assistant.message'].includes(kind) || !role) {
    return null
  }
  const content = normalizeContent(event.content ?? event?.payload?.message?.content ?? '')
  return {
    id: String(event.id ?? `${event.session_id}:${event.seq}`),
    role,
    content,
    createdAt: String(event.created_at ?? event.createdAt ?? ''),
    attachments: normalizeAttachments(event.attachments ?? event?.payload?.attachments, baseUrl),
    streaming: false,
  }
}

function normalizeHistoryEnvelope(event) {
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
  return normalizeStreamEnvelope({
    event_id: String(event?.seq ?? event?.id ?? ''),
    type: String(event?.type ?? ''),
    run_id: String(event?.run_id ?? event?.runId ?? payload.run_id ?? ''),
    session_id: String(event?.session_id ?? event?.sessionId ?? ''),
    timestamp: String(event?.created_at ?? event?.createdAt ?? ''),
    label: String(event?.kind ?? event?.label ?? ''),
    detail: String(event?.content ?? event?.tool_name ?? event?.toolName ?? event?.kind ?? ''),
    data: payload,
  })
}

function createHistoryAssistantPlaceholder(runId, timestamp, content = '') {
  return {
    id: `stream:${runId}`,
    role: 'assistant',
    content,
    createdAt: timestamp || new Date().toISOString(),
    startedAt: timestamp || new Date().toISOString(),
    attachments: [],
    processes: [],
    streaming: true,
  }
}

function appendHistoryProcessMessage(messages, envelope) {
  const processEvent = logEntryFromEnvelope(envelope)
  const runId = String(envelope?.runId || '')
  if (!processEvent || !runId) {
    return messages
  }

  const streamId = `stream:${runId}`
  const transcript = [...messages]
  let targetIndex = transcript.findIndex((message) => message.id === streamId)
  if (targetIndex === -1) {
    transcript.push(createHistoryAssistantPlaceholder(runId, processEvent.timestamp))
    targetIndex = transcript.length - 1
  }

  const message = transcript[targetIndex]
  const processes = Array.isArray(message.processes) ? message.processes : []
  const existingIndex = processes.findIndex((item) => item.id === processEvent.id)
  const nextProcesses =
    existingIndex === -1
      ? [...processes, processEvent]
      : processes.map((item, index) => (index === existingIndex ? { ...item, ...processEvent } : item))

  transcript[targetIndex] = {
    ...message,
    processes: nextProcesses,
  }
  return transcript
}

function mergeHistoryDelta(messages, envelope) {
  if (!envelope?.delta || !envelope.runId) {
    return messages
  }

  const streamId = `stream:${envelope.runId}`
  const transcript = [...messages]
  const existingIndex = transcript.findIndex((message) => message.id === streamId)
  if (existingIndex === -1) {
    transcript.push(createHistoryAssistantPlaceholder(envelope.runId, envelope.timestamp, envelope.delta))
    return transcript
  }

  transcript[existingIndex] = {
    ...transcript[existingIndex],
    content: `${transcript[existingIndex].content || ''}${envelope.delta}`,
    streaming: true,
  }
  return transcript
}

function finalizeHistoryAssistantMessage(messages, event, baseUrl = '') {
  const message = normalizeMessageFromEvent(event, baseUrl)
  if (!message) {
    return messages
  }

  const runId = String(event?.run_id ?? event?.runId ?? '')
  const streamId = runId ? `stream:${runId}` : ''
  const streamingMessage = streamId ? messages.find((entry) => entry.id === streamId) : null
  const transcript = streamId ? messages.filter((entry) => entry.id !== streamId) : [...messages]
  const nextMessage = {
    ...message,
    startedAt: String(streamingMessage?.startedAt ?? streamingMessage?.createdAt ?? message.createdAt),
    processes: Array.isArray(message.processes)
      ? message.processes
      : Array.isArray(streamingMessage?.processes)
        ? streamingMessage.processes
        : [],
    streaming: false,
  }
  const existingIndex = transcript.findIndex((entry) => entry.id === nextMessage.id)
  if (existingIndex === -1) {
    return [...transcript, nextMessage]
  }

  transcript[existingIndex] = {
    ...transcript[existingIndex],
    ...nextMessage,
  }
  return transcript
}

function settleHistoryStreamingMessage(messages, envelope) {
  if (!envelope?.terminal || !envelope.runId) {
    return messages
  }
  const streamId = `stream:${envelope.runId}`
  return messages.map((message) => (
    message.id === streamId ? { ...message, streaming: false } : message
  ))
}

function normalizeMessagesFromEvents(events, baseUrl = '') {
  let transcript = []
  for (const event of events) {
    const kind = String(event?.kind ?? event?.label ?? '')
    if (kind === 'user.message') {
      const message = normalizeMessageFromEvent(event, baseUrl)
      if (message) {
        transcript.push(message)
      }
      continue
    }

    const envelope = normalizeHistoryEnvelope(event)
    if (envelope) {
      transcript = appendHistoryProcessMessage(transcript, envelope)
      transcript = mergeHistoryDelta(transcript, envelope)
    }

    if (kind === 'assistant.message') {
      transcript = finalizeHistoryAssistantMessage(transcript, event, baseUrl)
      continue
    }

    transcript = settleHistoryStreamingMessage(transcript, envelope)
  }
  return transcript
}

function normalizeSession(session) {
  const metadata = session?.metadata && typeof session.metadata === 'object' ? { ...session.metadata } : {}
  return {
    id: String(session.id ?? session.session_id ?? session.sessionId),
    title: normalizeSessionTitle(session.title ?? session.name),
    updatedAt: String(session.updated_at ?? session.updatedAt ?? session.created_at ?? session.createdAt ?? ''),
    status: String(session.status ?? 'idle'),
    metadata,
  }
}

function normalizeErrorDetail(detail) {
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => normalizeErrorDetail(item))
      .filter(Boolean)
      .join('；')
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string') {
      return detail.message
    }
    if (typeof detail.msg === 'string') {
      return detail.msg
    }
  }
  return ''
}

async function readErrorMessage(response) {
  const body = await response.text()
  if (!body) {
    return ''
  }

  try {
    const payload = JSON.parse(body)
    if (typeof payload === 'string') {
      return payload
    }
    if (payload && typeof payload === 'object') {
      return normalizeErrorDetail(payload.detail) || normalizeErrorDetail(payload.message) || body
    }
  } catch {
    return body
  }

  return body
}

async function fetchJson(url, options = {}) {
  const accessToken = resolveAccessToken()
  const response = await fetch(url, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
    ...options,
  })

  if (!response.ok) {
    const message = await readErrorMessage(response)
    throw new Error(message || uiCopy.api.requestFailedStatus(response.status))
  }

  if (response.status === 204) {
    return null
  }

  return response.json()
}

export function createApiClient(baseUrl = resolveApiBaseUrl()) {
  return {
    async login({ username, password }) {
      const payload = await fetchJson(`${baseUrl}/auth/login`, {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      const token = String(payload.access_token || '')
      window.localStorage.setItem('deepagents.admin.token', token)
      return payload
    },

    async getAdminProfile() {
      return fetchJson(`${baseUrl}/auth/me`)
    },

    logout() {
      window.localStorage.removeItem('deepagents.admin.token')
    },

    async listSessions() {
      const payload = await fetchJson(`${baseUrl}/sessions`)
      return unwrapCollection(payload, 'sessions').map(normalizeSession)
    },

    async createSession() {
      const payload = await fetchJson(`${baseUrl}/sessions`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      return normalizeSession(payload.session ?? payload.data ?? payload)
    },

    async deleteSession(sessionId) {
      await fetchJson(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
      })
    },

    async updateSession(sessionId, payload) {
      const response = await fetchJson(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      })
      return normalizeSession(response.session ?? response.data ?? response)
    },

    async getSessionMessages(sessionId) {
      const payload = await fetchJson(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}/events`)
      return normalizeMessagesFromEvents(unwrapCollection(payload, 'events'), baseUrl)
    },

    async getRuntimeOptions() {
      return normalizeRuntimeOptions(await fetchJson(`${baseUrl}/models`))
    },

    async uploadFiles(sessionId, files) {
      void sessionId
      return files.map((file, index) => ({
        id: `local-${Date.now()}-${index}`,
        name: String(file.name || uiCopy.api.unnamedAttachment),
        size: Number(file.size || 0),
        status: 'submitted',
        downloadUrl: '',
      }))
    },

    async deleteUpload(uploadId) {
      void uploadId
    },

    async startRun({ sessionId, prompt, attachments, modelId, onOpen, onEvent, onError }) {
      void attachments
      const accessToken = resolveAccessToken()
      const controller = new globalThis.AbortController()
      const response = await fetch(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}/runs/stream`, {
        method: 'POST',
        credentials: 'include',
        signal: controller.signal,
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          prompt,
          model_id: modelId || null,
        }),
      })
      if (!response.ok || !response.body) {
        const message = await readErrorMessage(response)
        throw new Error(message || uiCopy.api.requestFailedStatus(response.status))
      }
      onOpen?.()
      let runId = ''
      let buffer = ''
      const reader = response.body.getReader()
      const decoder = new globalThis.TextDecoder()
      let resolveRunStarted
      let rejectRunStarted
      const runStarted = new Promise((resolve, reject) => {
        resolveRunStarted = resolve
        rejectRunStarted = reject
      })
      const settleRunStarted = (nextRunId) => {
        if (!runId && nextRunId) {
          runId = String(nextRunId)
          resolveRunStarted?.(runId)
        }
      }
      const pump = async () => {
        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const blocks = buffer.split('\n\n')
            buffer = blocks.pop() || ''
            for (const block of blocks) {
              const line = block.split('\n').find((entry) => entry.startsWith('data: '))
              if (!line) continue
              const payload = JSON.parse(line.slice(6))
              settleRunStarted(payload.run_id ?? payload.runId ?? '')
              onEvent?.(payload)
            }
          }
          if (!runId) {
            rejectRunStarted?.(new Error(uiCopy.api.invalidStreamEvent))
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            const normalizedError = error instanceof Error ? error : new Error(uiCopy.api.invalidStreamEvent)
            rejectRunStarted?.(normalizedError)
            onError?.(normalizedError)
          }
        }
      }
      const done = pump()
      await runStarted
      return {
        runId,
        sessionId: String(sessionId),
        status: 'running',
        close() {
          controller.abort()
        },
        done,
      }
    },

    async cancelRun(runId) {
      void runId
      return {
        runId: String(runId),
        sessionId: '',
        status: 'cancelled',
      }
    },

  }
}

export { buildUploadContentUrl, normalizeRuntimeOptions }
