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

function normalizeSessionTitle(value) {
  const title = String(value ?? '').trim()
  const englishDefaults = new Set(['new session', 'new chat', 'untitled', 'untitled session'])

  if (!title || englishDefaults.has(title.toLowerCase())) {
    return '新会话'
  }

  return title
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

function normalizeAttachments(attachments) {
  if (!Array.isArray(attachments)) {
    return []
  }

  return attachments.map((attachment, index) => ({
    id: String(attachment?.id ?? attachment?.attachment_id ?? attachment?.attachmentId ?? `attachment-${index}`),
    name: String(attachment?.name ?? attachment?.filename ?? attachment?.title ?? '未命名附件'),
    size: Number(attachment?.size ?? attachment?.size_bytes ?? attachment?.sizeBytes ?? 0),
    status: String(attachment?.status ?? 'uploaded'),
  }))
}

function normalizeSession(session) {
  return {
    id: String(session.id ?? session.session_id ?? session.sessionId),
    title: normalizeSessionTitle(session.title ?? session.name),
    updatedAt: String(session.updated_at ?? session.updatedAt ?? session.created_at ?? session.createdAt ?? ''),
    status: String(session.status ?? 'idle'),
  }
}

function normalizeMessage(message) {
  const extra = message?.extra && typeof message.extra === 'object' ? message.extra : {}
  return {
    id: String(message.id ?? message.message_id ?? message.messageId),
    role: String(message.role ?? 'assistant'),
    content: normalizeContent(message.content ?? message.text ?? extra.content ?? ''),
    createdAt: String(message.created_at ?? message.createdAt ?? ''),
    attachments: normalizeAttachments(message.attachments ?? extra.attachments),
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
    throw new Error(message || `请求失败（状态码 ${response.status}）`)
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
          throw new Error(message || `上传附件失败：${file.name}`)
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
          onError?.(new Error('实时连接恢复失败，请稍后重试。'))
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
          onError?.(error instanceof Error ? error : new Error('实时事件格式无效。'))
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
