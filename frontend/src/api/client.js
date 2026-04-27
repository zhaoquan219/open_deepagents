import { uiCopy } from '../lib/copy.js'
import { normalizeSessionTitle } from '../lib/sessionTitle.js'

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
  const url = new URL(`${baseUrl}/uploads/${encodeURIComponent(uploadId)}/content`, window.location.origin)
  const accessToken = resolveAccessToken()
  if (accessToken) {
    url.searchParams.set('access_token', accessToken)
  }
  return url.toString()
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
  const record = payload?.runtime ?? payload?.data ?? payload ?? {}
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

function normalizeSession(session) {
  return {
    id: String(session.id ?? session.session_id ?? session.sessionId),
    title: normalizeSessionTitle(session.title ?? session.name),
    updatedAt: String(session.updated_at ?? session.updatedAt ?? session.created_at ?? session.createdAt ?? ''),
    status: String(session.status ?? 'idle'),
  }
}

function normalizeMessage(message, baseUrl = '') {
  const extra = message?.extra && typeof message.extra === 'object' ? message.extra : {}
  return {
    id: String(message.id ?? message.message_id ?? message.messageId),
    role: String(message.role ?? 'assistant'),
    content: normalizeContent(message.content ?? message.text ?? extra.content ?? ''),
    createdAt: String(message.created_at ?? message.createdAt ?? ''),
    attachments: normalizeAttachments(message.attachments ?? extra.attachments, baseUrl),
    streaming: Boolean(message.streaming),
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
      const payload = await fetchJson(`${baseUrl}/admin/login`, {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      const token = String(payload.access_token || '')
      window.localStorage.setItem('deepagents.admin.token', token)
      return payload
    },

    async getAdminProfile() {
      return fetchJson(`${baseUrl}/admin/me`)
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

    async getSessionMessages(sessionId) {
      const payload = await fetchJson(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}/messages`)
      return unwrapCollection(payload, 'messages').map((message) => normalizeMessage(message, baseUrl))
    },

    async getRuntimeOptions() {
      return normalizeRuntimeOptions(await fetchJson(`${baseUrl}/runtime/options`))
    },

    async uploadFiles(sessionId, files) {
      const uploaded = []
      for (const file of files) {
        const formData = new FormData()
        formData.append('file', file)

        const accessToken = resolveAccessToken()
        const response = await fetch(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}/uploads`, {
          method: 'POST',
          body: formData,
          credentials: 'include',
          headers: {
            Accept: 'application/json',
            ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
          },
        })

        if (!response.ok) {
          const message = await readErrorMessage(response)
          throw new Error(message || uiCopy.api.uploadFailedForFile(file.name))
        }

        const payload = await response.json()
        const record = payload.attachment ?? payload.data ?? payload
        uploaded.push({
          id: String(record.id ?? record.attachment_id ?? record.attachmentId ?? file.name),
          name: String(record.name ?? record.filename ?? file.name),
          size: Number(record.size ?? file.size ?? 0),
          status: 'uploaded',
          downloadUrl: buildUploadContentUrl(baseUrl, String(record.id ?? record.attachment_id ?? record.attachmentId ?? file.name)),
        })
      }

      return uploaded
    },

    async deleteUpload(uploadId) {
      await fetchJson(`${baseUrl}/uploads/${encodeURIComponent(uploadId)}`, {
        method: 'DELETE',
      })
    },

    async startRun({ sessionId, prompt, attachments, modelId }) {
      const payload = await fetchJson(`${baseUrl}/runs`, {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          prompt,
          attachments,
          model_id: modelId || null,
        }),
      })
      const record = payload.run ?? payload.data ?? payload
      return {
        runId: String(record.run_id ?? record.runId ?? record.id),
        sessionId: String(record.session_id ?? record.sessionId ?? sessionId),
        status: String(record.status ?? 'running'),
      }
    },

    async cancelRun(runId) {
      const payload = await fetchJson(`${baseUrl}/runs/${encodeURIComponent(runId)}/cancel`, {
        method: 'POST',
      })
      const record = payload.run ?? payload.data ?? payload
      return {
        runId: String(record.run_id ?? record.runId ?? record.id ?? runId),
        sessionId: String(record.session_id ?? record.sessionId ?? ''),
        status: String(record.status ?? 'cancelled'),
      }
    },

    openRunStream(runId, options = {}) {
      const streamUrl = new URL(`${baseUrl}/runs/${encodeURIComponent(runId)}/stream`, window.location.origin)
      const accessToken = resolveAccessToken()
      let closed = false
      let recoveryTimer = 0
      let recovering = false
      const {
        lastEventId = '',
        onOpen,
        onEvent,
        onError,
        onRetry,
        reconnectGraceMs = 6000,
      } = options
      if (accessToken) {
        streamUrl.searchParams.set('access_token', accessToken)
      }
      if (lastEventId) {
        streamUrl.searchParams.set('last_event_id', lastEventId)
      }

      const eventSource = new EventSource(streamUrl.toString(), { withCredentials: true })
      const clearRecoveryWindow = () => {
        if (!recoveryTimer) {
          return
        }
        globalThis.clearTimeout(recoveryTimer)
        recoveryTimer = 0
      }

      const startRecoveryWindow = () => {
        if (closed || recoveryTimer) {
          return
        }

        recovering = true
        onRetry?.()
        recoveryTimer = globalThis.setTimeout(() => {
          recoveryTimer = 0
          recovering = false
          if (closed) {
            return
          }
          closed = true
          eventSource.close()
          onError?.(new Error(uiCopy.api.streamRecoveryFailed))
        }, reconnectGraceMs)
      }

      eventSource.onopen = () => {
        const resumed = recovering
        clearRecoveryWindow()
        recovering = false
        onOpen?.({ resumed })
      }
      eventSource.onmessage = (event) => {
        if (closed) {
          return
        }
        try {
          onEvent?.(JSON.parse(event.data))
        } catch (error) {
          onError?.(error instanceof Error ? error : new Error(uiCopy.api.invalidStreamEvent))
        }
      }
      eventSource.onerror = () => {
        if (closed || eventSource.readyState === EventSource.CLOSED) {
          return
        }
        startRecoveryWindow()
      }

      return {
        close() {
          closed = true
          clearRecoveryWindow()
          recovering = false
          eventSource.close()
        },
      }
    },
  }
}

export { buildUploadContentUrl, normalizeRuntimeOptions }
