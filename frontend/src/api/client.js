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

function normalizeSession(session) {
  return {
    id: String(session.id ?? session.session_id ?? session.sessionId),
    title: String(session.title ?? session.name ?? 'Untitled session'),
    updatedAt: String(session.updated_at ?? session.updatedAt ?? session.created_at ?? session.createdAt ?? ''),
    status: String(session.status ?? 'idle'),
  }
}

function normalizeMessage(message) {
  return {
    id: String(message.id ?? message.message_id ?? message.messageId),
    role: String(message.role ?? 'assistant'),
    content: String(message.content ?? message.text ?? ''),
    createdAt: String(message.created_at ?? message.createdAt ?? ''),
    attachments: Array.isArray(message.attachments) ? message.attachments : [],
    streaming: Boolean(message.streaming),
  }
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
    const message = await response.text()
    throw new Error(message || `Request failed with status ${response.status}`)
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

    async getSessionMessages(sessionId) {
      const payload = await fetchJson(`${baseUrl}/sessions/${encodeURIComponent(sessionId)}/messages`)
      return unwrapCollection(payload, 'messages').map(normalizeMessage)
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
          const message = await response.text()
          throw new Error(message || `Upload failed for ${file.name}`)
        }

        const payload = await response.json()
        const record = payload.attachment ?? payload.data ?? payload
        uploaded.push({
          id: String(record.id ?? record.attachment_id ?? record.attachmentId ?? file.name),
          name: String(record.name ?? record.filename ?? file.name),
          size: Number(record.size ?? file.size ?? 0),
          status: 'uploaded',
        })
      }

      return uploaded
    },

    async startRun({ sessionId, prompt, attachments }) {
      const payload = await fetchJson(`${baseUrl}/runs`, {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          prompt,
          attachments,
        }),
      })
      const record = payload.run ?? payload.data ?? payload
      return {
        runId: String(record.run_id ?? record.runId ?? record.id),
        sessionId: String(record.session_id ?? record.sessionId ?? sessionId),
      }
    },

    openRunStream(runId, { lastEventId, onOpen, onEvent, onError }) {
      const streamUrl = new URL(`${baseUrl}/runs/${encodeURIComponent(runId)}/stream`, window.location.origin)
      const accessToken = resolveAccessToken()
      let closed = false
      if (accessToken) {
        streamUrl.searchParams.set('access_token', accessToken)
      }
      if (lastEventId) {
        streamUrl.searchParams.set('last_event_id', lastEventId)
      }

      const eventSource = new EventSource(streamUrl.toString(), { withCredentials: true })
      eventSource.onopen = () => onOpen?.()
      eventSource.onmessage = (event) => {
        if (closed) {
          return
        }
        try {
          onEvent?.(JSON.parse(event.data))
        } catch (error) {
          onError?.(error instanceof Error ? error : new Error('Invalid SSE payload.'))
        }
      }
      eventSource.onerror = () => {
        if (closed || eventSource.readyState === EventSource.CLOSED) {
          return
        }
        onError?.(new Error('SSE connection closed unexpectedly.'))
      }

      return {
        close() {
          closed = true
          eventSource.close()
        },
      }
    },
  }
}
