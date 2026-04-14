import { reactive } from 'vue'

import {
  distillSessionTitle,
  isPlaceholderSessionTitle,
  normalizeSessionTitle,
} from '../lib/sessionTitle.js'

function createClientId() {
  return globalThis.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function createEmptyState() {
  return {
    sessions: [],
    currentSessionId: null,
    messagesBySession: {},
    pendingUploadsBySession: {},
    loadingSessions: false,
    loadingMessages: false,
    submitting: false,
    uploading: false,
    error: '',
    uploadError: '',
    deletingSessionId: '',
  }
}

function sortSessions(sessions) {
  return [...sessions].sort((left, right) => {
    return String(right.updatedAt || '').localeCompare(String(left.updatedAt || ''))
  })
}

function ensureTranscriptMap(messagesBySession, sessionId) {
  if (!messagesBySession[sessionId]) {
    messagesBySession[sessionId] = []
  }
  return messagesBySession[sessionId]
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

function messageSignature(message) {
  const attachments = Array.isArray(message?.attachments)
    ? message.attachments.map((attachment) => attachment?.name || attachment?.id || '').join('|')
    : ''
  return [
    String(message?.role || ''),
    normalizeContent(message?.content ?? message?.text ?? ''),
    attachments,
  ].join('::')
}

export function reconcileMessages(localMessages, fetchedMessages) {
  const local = Array.isArray(localMessages) ? [...localMessages] : []
  const fetched = Array.isArray(fetchedMessages) ? [...fetchedMessages] : []

  if (local.length === 0) {
    return fetched
  }
  if (fetched.length === 0) {
    return local
  }

  const fetchedIsPrefixOfLocal =
    fetched.length <= local.length &&
    fetched.every((message, index) => messageSignature(message) === messageSignature(local[index]))

  if (fetchedIsPrefixOfLocal) {
    return [...fetched, ...local.slice(fetched.length)]
  }

  return fetched
}

export function mergeAssistantDelta(messages, { runId, delta }) {
  const streamId = `stream:${runId}`
  const transcript = [...messages]
  const existingIndex = transcript.findIndex((message) => message.id === streamId)
  if (existingIndex === -1) {
    transcript.push({
      id: streamId,
      role: 'assistant',
      content: delta,
      createdAt: new Date().toISOString(),
      streaming: true,
      attachments: [],
    })
    return transcript
  }

  transcript[existingIndex] = {
    ...transcript[existingIndex],
    content: `${transcript[existingIndex].content}${delta}`,
    streaming: true,
  }
  return transcript
}

export function finalizeAssistantMessage(messages, { runId, message }) {
  const streamId = `stream:${runId}`
  const transcript = messages.filter((entry) => entry.id !== streamId)
  const extra = message?.extra && typeof message.extra === 'object' ? message.extra : {}
  const nextMessage = {
    id: String(message.id ?? `final:${runId}`),
    role: String(message.role ?? 'assistant'),
    content: normalizeContent(message.content ?? message.text ?? extra.content ?? ''),
    createdAt: String(message.createdAt ?? message.created_at ?? new Date().toISOString()),
    attachments: normalizeAttachments(message.attachments ?? extra.attachments),
    streaming: false,
  }
  const existingIndex = transcript.findIndex((entry) => entry.id === nextMessage.id)
  if (existingIndex === -1) {
    transcript.push(nextMessage)
    return transcript
  }

  transcript[existingIndex] = {
    ...transcript[existingIndex],
    ...nextMessage,
  }
  return transcript
}

function addSystemNotice(messages, content) {
  return [
    ...messages,
    {
      id: createClientId(),
      role: 'system',
      content,
      createdAt: new Date().toISOString(),
      attachments: [],
      streaming: false,
    },
  ]
}

export function createSessionStore(apiClient) {
  const state = reactive(createEmptyState())

  function getCurrentSession() {
    return state.sessions.find((session) => session.id === state.currentSessionId) || null
  }

  function getCurrentMessages() {
    if (!state.currentSessionId) {
      return []
    }
    return state.messagesBySession[state.currentSessionId] || []
  }

  function getPendingUploads(sessionId = state.currentSessionId) {
    if (!sessionId) {
      return []
    }
    return state.pendingUploadsBySession[sessionId] || []
  }

  function touchSession(sessionId, titleFallback) {
    const existing = state.sessions.find((session) => session.id === sessionId)
    const updatedAt = new Date().toISOString()
    const nextTitle = titleFallback ? distillSessionTitle(titleFallback) : ''
    if (existing) {
      existing.updatedAt = updatedAt
      existing.title =
        nextTitle && isPlaceholderSessionTitle(existing.title)
          ? nextTitle
          : normalizeSessionTitle(existing.title)
    } else {
      state.sessions = sortSessions([
        ...state.sessions,
        { id: sessionId, title: nextTitle || '新会话', updatedAt, status: 'idle' },
      ])
    }
    state.sessions = sortSessions(state.sessions)
  }

  async function loadSessions({ preserveSelection = false } = {}) {
    state.loadingSessions = true
    state.error = ''
    try {
      state.sessions = sortSessions(await apiClient.listSessions())
      if (!preserveSelection) {
        state.currentSessionId = state.sessions[0]?.id || null
      } else if (!state.sessions.some((session) => session.id === state.currentSessionId)) {
        state.currentSessionId = state.sessions[0]?.id || null
      }
    } catch (error) {
      state.error = error instanceof Error ? error.message : '加载会话失败。'
      state.sessions = []
      state.currentSessionId = null
    } finally {
      state.loadingSessions = false
    }
  }

  async function createSession() {
    const session = await apiClient.createSession()
    state.sessions = sortSessions([session, ...state.sessions.filter((entry) => entry.id !== session.id)])
    state.currentSessionId = session.id
    ensureTranscriptMap(state.messagesBySession, session.id)
    return session
  }

  async function deleteSession(sessionId) {
    const normalizedId = String(sessionId)
    state.deletingSessionId = normalizedId
    state.error = ''
    try {
      await apiClient.deleteSession(normalizedId)
      state.sessions = state.sessions.filter((session) => session.id !== normalizedId)
      delete state.messagesBySession[normalizedId]
      delete state.pendingUploadsBySession[normalizedId]

      if (state.currentSessionId === normalizedId) {
        state.currentSessionId = state.sessions[0]?.id || null
        if (state.currentSessionId) {
          await selectSession(state.currentSessionId)
        }
      }
    } catch (error) {
      state.error = error instanceof Error ? error.message : '删除会话失败。'
    } finally {
      state.deletingSessionId = ''
    }
  }

  async function selectSession(sessionId) {
    const normalizedId = String(sessionId)
    const localMessages = state.messagesBySession[normalizedId] || []
    state.currentSessionId = normalizedId
    state.loadingMessages = true
    state.error = ''
    try {
      const messages = await apiClient.getSessionMessages(normalizedId)
      state.messagesBySession[normalizedId] = reconcileMessages(localMessages, messages)
    } catch (error) {
      state.error = error instanceof Error ? error.message : '加载消息失败。'
      state.messagesBySession[normalizedId] = state.messagesBySession[normalizedId] || []
    } finally {
      state.loadingMessages = false
    }
  }

  async function uploadFiles(sessionId, files) {
    const normalizedId = String(sessionId)
    state.uploading = true
    state.uploadError = ''
    try {
      const records = await apiClient.uploadFiles(normalizedId, files)
      state.pendingUploadsBySession[normalizedId] = [
        ...(state.pendingUploadsBySession[normalizedId] || []),
        ...records,
      ]
      touchSession(normalizedId)
      return {
        ok: true,
        records,
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '上传附件失败。'
      state.uploadError = message
      return {
        ok: false,
        error: new Error(message),
      }
    } finally {
      state.uploading = false
    }
  }

  function markUploadsSubmitted(sessionId) {
    const normalizedId = String(sessionId)
    state.pendingUploadsBySession[normalizedId] = (state.pendingUploadsBySession[normalizedId] || []).map((upload) => ({
      ...upload,
      status: 'submitted',
    }))
  }

  function addOptimisticUserMessage(sessionId, prompt) {
    const normalizedId = String(sessionId)
    const transcript = ensureTranscriptMap(state.messagesBySession, normalizedId)
    transcript.push({
      id: createClientId(),
      role: 'user',
      content: prompt,
      createdAt: new Date().toISOString(),
      attachments: getPendingUploads(normalizedId),
      streaming: false,
    })
    touchSession(normalizedId, prompt)
  }

  function addSystemNoticeToSession(sessionId, content) {
    const normalizedId = String(sessionId)
    const transcript = ensureTranscriptMap(state.messagesBySession, normalizedId)
    state.messagesBySession[normalizedId] = addSystemNotice(transcript, content)
    touchSession(normalizedId)
  }

  function setSubmitting(submitting) {
    state.submitting = Boolean(submitting)
  }

  function consumeRunEvent(envelope) {
    if (!envelope.sessionId) {
      return
    }

    const sessionId = String(envelope.sessionId)
    const transcript = ensureTranscriptMap(state.messagesBySession, sessionId)

    if (envelope.type === 'message.delta' && envelope.delta) {
      state.messagesBySession[sessionId] = mergeAssistantDelta(transcript, {
        runId: envelope.runId,
        delta: envelope.delta,
      })
      touchSession(sessionId)
      return
    }

    if (envelope.type === 'message.final') {
      const finalMessage =
        envelope.message ||
        (envelope.data?.message && typeof envelope.data.message === 'object' ? envelope.data.message : null) ||
        {
          id: `final:${envelope.runId}`,
          role: 'assistant',
          content: envelope.data?.text ?? envelope.detail ?? '',
          createdAt: envelope.timestamp,
          attachments: [],
        }

      state.messagesBySession[sessionId] = finalizeAssistantMessage(transcript, {
        runId: envelope.runId,
        message: finalMessage,
      })
      touchSession(sessionId)
      return
    }

    if (envelope.type === 'error') {
      state.messagesBySession[sessionId] = addSystemNotice(transcript, envelope.detail || '本轮处理失败。')
      touchSession(sessionId)
    }
  }

  return {
    state,
    addOptimisticUserMessage,
    addSystemNotice: addSystemNoticeToSession,
    consumeRunEvent,
    createSession,
    deleteSession,
    getCurrentMessages,
    getCurrentSession,
    getPendingUploads,
    loadSessions,
    markUploadsSubmitted,
    selectSession,
    setSubmitting,
    uploadFiles,
  }
}
