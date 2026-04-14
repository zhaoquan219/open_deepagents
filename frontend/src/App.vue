<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'

import ChatWorkspace from './components/ChatWorkspace.vue'
import ProgressTimeline from './components/ProgressTimeline.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import { createApiClient } from './api/client.js'
import { normalizeStreamEnvelope } from './lib/sseContract.js'
import { createRunStore } from './store/runStore.js'
import { createSessionStore } from './store/sessionStore.js'

function logRuntime(scope, detail, payload, level = 'debug') {
  if (level === 'debug' && !import.meta.env.DEV) {
    return
  }

  const logger = console[level] || console.log
  logger(`[deepagents-ui] ${scope}: ${detail}`, payload ?? '')
}

const apiClient = createApiClient()
const sessionStore = createSessionStore(apiClient)
const runStore = createRunStore()
if (import.meta.env.DEV && typeof window !== 'undefined') {
  window.__deepagentsDebug = {
    sessionStore,
    runStore,
  }
}
const activeStream = ref(null)
const authUsername = ref('admin')
const authPassword = ref('')
const authError = ref('')
const authLoading = ref(false)
const isAuthenticated = ref(false)
const isWideLayout = ref(true)
const stoppingRunId = ref('')
const timelinePanelOpen = ref(true)
let viewportMediaQuery = null

const sessions = computed(() => sessionStore.state.sessions)
const currentSessionId = computed(() => sessionStore.state.currentSessionId)
const currentSession = computed(() => sessionStore.getCurrentSession())
const currentMessages = computed(() => sessionStore.getCurrentMessages())
const pendingUploads = computed(() => sessionStore.getPendingUploads())
const loadingSessions = computed(() => sessionStore.state.loadingSessions)
const loadingMessages = computed(() => sessionStore.state.loadingMessages)
const uploading = computed(() => sessionStore.state.uploading)
const submitting = computed(() => sessionStore.state.submitting)
const deletingSessionId = computed(() => sessionStore.state.deletingSessionId)
const sessionError = computed(() => sessionStore.state.error)
const combinedError = computed(
  () => runStore.state.error || sessionStore.state.error || sessionStore.state.uploadError,
)
const activeRun = computed(() => runStore.state.activeRun)
const connectionState = computed(
  () => runStore.state.activeRun?.connectionState || runStore.state.connectionState,
)
const runtimeDiagnostics = computed(() => runStore.state.diagnostics)
const runError = computed(() => runStore.state.error)
const runStatus = computed(() => runStore.state.activeRun?.status || 'idle')
const canStopRun = computed(
  () => ['queued', 'running'].includes(runStatus.value) && Boolean(activeRun.value?.runId),
)
const stoppingRun = computed(
  () => Boolean(stoppingRunId.value) && stoppingRunId.value === String(activeRun.value?.runId || ''),
)
const sessionCount = computed(() => sessions.value.length)
const currentMessageCount = computed(() => currentMessages.value.length)
const pendingUploadCount = computed(() => pendingUploads.value.length)
const topbarStatusCopy = computed(() => {
  if (runStatus.value === 'running') {
    return '当前任务正在处理，回复、工具动作和连接状态会在侧边运行面板里持续更新。'
  }
  if (runStatus.value === 'completed') {
    return '本轮任务已经完成，当前会话里可以直接继续追问或回溯上下文。'
  }
  if (runStatus.value === 'cancelled') {
    return '上一轮运行已手动停止，可以调整提示或附件后重新发起。'
  }
  if (runStatus.value === 'failed') {
    return '上一轮处理被中断，先查看运行面板里的错误，再决定是否重试。'
  }
  return '工作台已就绪，左侧管理会话，中间专注对话，运行细节按需查看。'
})
const runStatusLabel = computed(() => {
  const status = runStatus.value
  if (status === 'idle') return '待命'
  if (status === 'queued') return '排队中'
  if (status === 'running') return '处理中'
  if (status === 'completed') return '已完成'
  if (status === 'cancelling') return '停止中'
  if (status === 'cancelled') return '已停止'
  if (status === 'failed') return '失败'
  return '处理中'
})
const showTimelinePanel = computed(() => isWideLayout.value || timelinePanelOpen.value)
const timelineToggleLabel = computed(() =>
  showTimelinePanel.value ? '收起运行面板' : '查看运行面板',
)

function applyViewportLayout(matches) {
  isWideLayout.value = matches
  timelinePanelOpen.value = matches
}

function handleViewportChange(event) {
  applyViewportLayout(Boolean(event.matches))
}

function setupViewportTracking() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    applyViewportLayout(true)
    return
  }

  viewportMediaQuery = window.matchMedia('(min-width: 1320px)')
  applyViewportLayout(viewportMediaQuery.matches)

  if (typeof viewportMediaQuery.addEventListener === 'function') {
    viewportMediaQuery.addEventListener('change', handleViewportChange)
  } else {
    viewportMediaQuery.addListener(handleViewportChange)
  }
}

function teardownViewportTracking() {
  if (!viewportMediaQuery) {
    return
  }

  if (typeof viewportMediaQuery.removeEventListener === 'function') {
    viewportMediaQuery.removeEventListener('change', handleViewportChange)
  } else {
    viewportMediaQuery.removeListener(handleViewportChange)
  }

  viewportMediaQuery = null
}

function toggleTimelinePanel() {
  if (isWideLayout.value) {
    return
  }
  timelinePanelOpen.value = !timelinePanelOpen.value
}

function closeTimelinePanel() {
  if (isWideLayout.value) {
    return
  }
  timelinePanelOpen.value = false
}

function closeStream({ markDisconnected = false, detail = '' } = {}) {
  const runId = runStore.state.activeRun?.runId || ''
  if (activeStream.value) {
    activeStream.value.close()
    activeStream.value = null
  }
  if (markDisconnected && runId) {
    runStore.markDisconnected(runId, detail || '实时连接已关闭。')
  }
}

function syncSessionTranscript(sessionId) {
  if (String(currentSessionId.value || '') !== String(sessionId || '')) {
    return
  }
  void sessionStore.selectSession(String(sessionId))
}

function isTerminalEnvelope(envelope) {
  return (
    envelope.type === 'error' ||
    (envelope.type === 'status' && ['completed', 'failed', 'cancelled'].includes(envelope.status))
  )
}

async function handleLogin() {
  authLoading.value = true
  authError.value = ''
  try {
    await apiClient.login({
      username: authUsername.value.trim(),
      password: authPassword.value,
    })
    await apiClient.getAdminProfile()
    isAuthenticated.value = true
    await sessionStore.loadSessions()
    if (sessionStore.state.currentSessionId) {
      await sessionStore.selectSession(sessionStore.state.currentSessionId)
    }
  } catch (error) {
    apiClient.logout()
    isAuthenticated.value = false
    authError.value = error instanceof Error ? error.message : '登录失败，请检查账号信息。'
  } finally {
    authLoading.value = false
  }
}

function connectRunStream(runId, sessionId) {
  closeStream()
  logRuntime('sse.connect', '正在连接实时事件流', { runId, sessionId })
  const terminalDetail = (envelope) =>
    envelope.type === 'error'
      ? '运行异常，实时连接已终止。'
      : envelope.status === 'cancelled'
        ? '当前运行已手动停止，实时连接已关闭。'
      : '本轮输出已结束，实时连接已关闭。'
  activeStream.value = apiClient.openRunStream(runId, {
    lastEventId: runStore.getLastEventId(runId),
    onOpen() {
      logRuntime('sse.open', '实时事件流已连接', { runId, sessionId })
      runStore.markConnected(runId)
    },
    onRetry() {
      logRuntime('sse.retry', '实时事件流正在自动恢复', { runId, sessionId }, 'warn')
      runStore.markConnecting(runId, '实时连接短暂中断，正在自动恢复。')
    },
    onEvent(payload) {
      const envelope = normalizeStreamEnvelope(payload)
      if (!envelope) {
        logRuntime('sse.drop', '收到无法识别的 SSE 事件', payload, 'warn')
        return
      }

      logRuntime('sse.event', `${envelope.type}`, envelope)

      const accepted = runStore.consume(envelope)
      if (!accepted) {
        if (isTerminalEnvelope(envelope)) {
          syncSessionTranscript(sessionId)
          if (envelope.type === 'status' && envelope.status === 'completed') {
            runStore.markCompleted(runId, '检测到终态事件重放，已同步最终回复。')
          } else if (envelope.type === 'status' && envelope.status === 'cancelled') {
            runStore.markCancelled(runId, '运行已被手动停止。')
          }
          closeStream({
            markDisconnected: true,
            detail: terminalDetail(envelope),
          })
        }
        return
      }

      if (envelope.sessionId && envelope.sessionId !== String(sessionId)) {
        return
      }

      sessionStore.consumeRunEvent(envelope)
      if (envelope.type === 'message.final' && !String(envelope.message?.content || '').trim()) {
        syncSessionTranscript(sessionId)
      }

      if (isTerminalEnvelope(envelope)) {
        if (envelope.type === 'status' && envelope.status === 'completed') {
          syncSessionTranscript(sessionId)
        }
        closeStream({
          markDisconnected: true,
          detail: terminalDetail(envelope),
        })
      }
    },
    onError(error) {
      if (['completed', 'cancelled'].includes(runStore.state.activeRun?.status || '')) {
        logRuntime('sse.closed', '实时事件流在完成后关闭', { runId, sessionId })
        closeStream({
          markDisconnected: true,
          detail:
            runStore.state.activeRun?.status === 'cancelled'
              ? '运行已手动停止，会话流已正常结束。'
              : '最终回复已写入，会话流已正常结束。',
        })
        return
      }
      logRuntime('sse.error', error.message, { runId, sessionId }, 'error')
      runStore.markErrored(runId, error.message)
      sessionStore.addSystemNotice(String(sessionId), `实时连接恢复失败：${error.message}`)
    },
  })
}

async function ensureSession() {
  if (sessionStore.state.currentSessionId) {
    return sessionStore.getCurrentSession()
  }

  return handleCreateSession()
}

async function handleRefreshSessions() {
  await sessionStore.loadSessions({ preserveSelection: true })
}

async function handleCreateSession() {
  closeStream()
  runStore.clear()
  const session = await sessionStore.createSession()
  await sessionStore.selectSession(session.id)
  return session
}

async function handleSelectSession(sessionId) {
  closeStream()
  runStore.clear()
  await sessionStore.selectSession(sessionId)
}

async function handleDeleteSession(sessionId) {
  const session = sessions.value.find((item) => item.id === sessionId)
  const title = session?.title || '当前会话'

  try {
    await ElMessageBox.confirm(`确定要删除“${title}”吗？此操作无法撤销。`, '删除会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  if (currentSessionId.value === sessionId) {
    closeStream()
    runStore.clear()
  }
  await sessionStore.deleteSession(sessionId)
}

async function handleUpload(files) {
  const session = await ensureSession()
  const result = await sessionStore.uploadFiles(String(session.id), files)
  if (!result?.ok) {
    const message = result?.error?.message || '上传附件失败。'
    logRuntime('upload.error', message, { sessionId: session.id, files }, 'error')
    runStore.recordClientIssue({
      sessionId: String(session.id),
      label: '附件上传失败',
      detail: message,
    })
    return
  }

  logRuntime('upload.success', '附件上传完成', {
    sessionId: session.id,
    count: result.records.length,
  })
  runStore.recordClientNotice({
    sessionId: String(session.id),
    label: '附件上传完成',
    detail: `已上传 ${result.records.length} 个附件，将附加到下一次发送。`,
    status: 'completed',
  })
}

async function handleSubmit({ prompt }) {
  if (['queued', 'running'].includes(runStatus.value)) {
    return
  }
  const text = String(prompt || '').trim()
  if (!text) {
    return
  }

  const session = await ensureSession()
  const sessionId = String(session.id)
  sessionStore.addOptimisticUserMessage(sessionId, text)
  sessionStore.setSubmitting(true)
  logRuntime('run.start', '正在创建运行', {
    sessionId,
    attachmentCount: sessionStore.getPendingUploads(sessionId).length,
  })

  try {
    const run = await apiClient.startRun({
      sessionId,
      prompt: text,
      attachments: sessionStore.getPendingUploads(sessionId),
    })

    sessionStore.clearPendingUploads(sessionId)
    runStore.beginRun({ runId: run.runId, sessionId })
    runStore.recordClientNotice({
      sessionId,
      runId: run.runId,
      label: '运行创建成功',
      detail: '后端已接受请求，正在建立实时连接。',
      status: 'completed',
    })
    connectRunStream(run.runId, sessionId)
  } catch (error) {
    const message = error instanceof Error ? error.message : '发送失败，请稍后再试。'
    logRuntime('run.start.error', message, { sessionId }, 'error')
    runStore.markErrored('pending', message)
    runStore.recordClientIssue({
      sessionId,
      label: '运行启动失败',
      detail: message,
    })
    sessionStore.addSystemNotice(sessionId, message)
  } finally {
    sessionStore.setSubmitting(false)
  }
}

async function handleStopRun() {
  const runId = String(activeRun.value?.runId || '')
  const sessionId = String(activeRun.value?.sessionId || currentSessionId.value || '')
  if (!runId || !canStopRun.value || stoppingRunId.value) {
    return
  }

  stoppingRunId.value = runId
  try {
    const result = await apiClient.cancelRun(runId)
    if (result.status === 'completed') {
      runStore.markCompleted(runId, '停止请求到达前，本轮处理已经完成。')
      syncSessionTranscript(sessionId)
    } else if (result.status === 'failed') {
      runStore.markErrored(runId, '停止请求到达前，本轮处理已经失败。')
    } else {
      runStore.markCancelled(runId, '已手动停止当前运行。')
    }
    closeStream()
  } catch (error) {
    const message = error instanceof Error ? error.message : '停止运行失败，请稍后再试。'
    runStore.recordClientIssue({
      sessionId,
      label: '停止运行失败',
      detail: message,
    })
  } finally {
    stoppingRunId.value = ''
  }
}

onMounted(async () => {
  setupViewportTracking()
  try {
    await apiClient.getAdminProfile()
    isAuthenticated.value = true
    await sessionStore.loadSessions()
    if (sessionStore.state.currentSessionId) {
      await sessionStore.selectSession(sessionStore.state.currentSessionId)
    }
  } catch {
    apiClient.logout()
    isAuthenticated.value = false
  }
})

onBeforeUnmount(() => {
  closeStream()
  teardownViewportTracking()
})
</script>

<template>
  <div class="app-shell">
    <template v-if="isAuthenticated">
      <header class="app-toolbar">
        <div class="toolbar-brand">
          <p class="eyebrow">DeepAgents</p>
          <h1>对话工作台</h1>
          <p class="topbar-copy">{{ topbarStatusCopy }}</p>
        </div>

        <dl class="toolbar-metrics" aria-label="工作台概览">
          <div>
            <dt>会话</dt>
            <dd>{{ sessionCount }}</dd>
          </div>
          <div>
            <dt>消息</dt>
            <dd>{{ currentMessageCount }}</dd>
          </div>
          <div>
            <dt>附件</dt>
            <dd>{{ pendingUploadCount }}</dd>
          </div>
        </dl>

        <div class="toolbar-actions">
          <el-tag
            size="small"
            :type="
              runStatus === 'failed'
                ? 'danger'
                : runStatus === 'completed'
                  ? 'success'
                  : runStatus === 'cancelled' || runStatus === 'cancelling'
                    ? 'warning'
                    : 'primary'
            "
            effect="light"
          >
            {{ runStatusLabel }}
          </el-tag>
          <el-button v-if="!isWideLayout" size="small" plain @click="toggleTimelinePanel">
            {{ timelineToggleLabel }}
          </el-button>
        </div>
      </header>

      <main class="layout-grid">
        <aside class="sidebar-shell">
          <SessionSidebar
            :sessions="sessions"
            :current-session-id="currentSessionId"
            :loading="loadingSessions"
            :error="sessionError"
            :deleting-session-id="deletingSessionId"
            @new-session="handleCreateSession"
            @refresh="handleRefreshSessions"
            @select-session="handleSelectSession"
            @delete-session="handleDeleteSession"
          />
        </aside>

        <section class="workspace-shell" :class="{ 'workspace-shell--wide': isWideLayout }" aria-label="聊天工作区">
          <ChatWorkspace
            :can-stop="canStopRun"
            :current-session="currentSession"
            :messages="currentMessages"
            :pending-uploads="pendingUploads"
            :loading="loadingMessages"
            :uploading="uploading"
            :submitting="submitting"
            :active-run="activeRun"
            :run-status="runStatus"
            :run-status-label="runStatusLabel"
            :stopping="stoppingRun"
            :error="combinedError"
            @submit="handleSubmit"
            @stop-run="handleStopRun"
            @upload="handleUpload"
          />

          <div
            v-if="!isWideLayout && showTimelinePanel"
            class="timeline-backdrop"
            @click="closeTimelinePanel"
          />

          <aside
            v-if="showTimelinePanel"
            class="timeline-shell"
            :class="{ 'timeline-shell--overlay': !isWideLayout }"
            aria-label="运行状态"
          >
            <ProgressTimeline
              :active-run="activeRun"
              :can-stop="canStopRun"
              :connection-state="connectionState"
              :diagnostics="runtimeDiagnostics"
              :runtime-error="runError"
              :dismissible="!isWideLayout"
              :stopping="stoppingRun"
              @close="closeTimelinePanel"
            />
          </aside>
        </section>
      </main>
    </template>

    <main v-else class="auth-layout">
      <el-card class="auth-panel card-shell" shadow="never">
        <div class="auth-card">
          <p class="eyebrow">安全登录</p>
          <h2>进入智能工作台</h2>
          <p class="auth-copy">使用管理员账号建立受保护连接，进入稳定的会话与运行工作区。</p>

          <label class="composer-label" for="admin-username">用户名</label>
          <el-input
            id="admin-username"
            v-model="authUsername"
            autocomplete="username"
            placeholder="请输入管理员用户名"
          />

          <label class="composer-label" for="admin-password">密码</label>
          <el-input
            id="admin-password"
            v-model="authPassword"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入密码"
          />

          <el-alert v-if="authError" :closable="false" type="error" show-icon :title="authError" />

          <div class="composer-actions auth-actions">
            <span class="muted-copy">登录后会自动恢复最近会话。</span>
            <el-button type="primary" :loading="authLoading" @click="handleLogin">
              {{ authLoading ? '登录中…' : '进入工作台' }}
            </el-button>
          </div>
        </div>
      </el-card>
    </main>
  </div>
</template>
