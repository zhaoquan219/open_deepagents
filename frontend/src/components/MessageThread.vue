<script setup>
import { CopyDocument, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { copyText } from '../lib/clipboard.js'
import { uiCopy } from '../lib/copy.js'
import {
  groupProcessLogs,
  thinkingEntriesFromContent,
  visibleAssistantContent,
} from '../lib/processLog.js'
import { isNearBottom, scrollMetrics, shouldForceFollowLatest } from '../lib/scroll.js'
import { formatDateTime } from '../lib/time.js'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps({
  activeRunSessionId: {
    type: String,
    default: '',
  },
  messages: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  messageSendScrollKey: {
    type: Number,
    default: 0,
  },
  runStatus: {
    type: String,
    default: 'idle',
  },
  sessionId: {
    type: String,
    default: '',
  },
})

const threadRef = ref(null)
const autoFollowLatest = ref(false)
const userScrollLocked = ref(false)
const lastSessionId = ref('')
const pendingHistoryLoadSessionId = ref('')

const isLiveRunSession = computed(
  () =>
    Boolean(props.sessionId) &&
    props.sessionId === props.activeRunSessionId &&
    ['queued', 'running'].includes(props.runStatus),
)
const visibleMessages = computed(() => props.messages.filter(shouldRenderMessage))

function flattenMessageContent(value) {
  if (value === undefined || value === null) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => flattenMessageContent(item)).join('')
  }
  if (typeof value === 'object') {
    if ('content' in value) {
      return flattenMessageContent(value.content)
    }
    if (typeof value.text === 'string') {
      return value.text
    }
    if (Array.isArray(value.parts)) {
      return value.parts.map((part) => flattenMessageContent(part)).join('')
    }
  }
  return String(value)
}

function roleLabel(role) {
  if (role === 'user') {
    return uiCopy.messageThread.roles.user
  }
  if (role === 'assistant') {
    return uiCopy.messageThread.roles.assistant
  }
  return uiCopy.messageThread.roles.system
}

function displayContent(message) {
  const content = flattenMessageContent(
    message?.content ?? message?.text ?? message?.detail ?? message?.extra?.content ?? '',
  )
  const visible = message?.role === 'assistant' ? visibleAssistantContent(content) : content
  if (visible.trim()) {
    return visible
  }
  if (message?.streaming) {
    return ''
  }
  if (message?.role === 'assistant' && hasProcesses(message)) {
    return ''
  }
  if (message?.role === 'assistant') {
    return ''
  }
  return uiCopy.messageThread.empty
}

function shouldRenderMessage(message) {
  if (message?.role !== 'assistant') {
    return true
  }
  if (message?.streaming || hasProcesses(message)) {
    return true
  }
  if (Array.isArray(message?.attachments) && message.attachments.length > 0) {
    return true
  }
  return Boolean(displayContent(message).trim())
}

function processStatusLabel(status) {
  if (status === 'completed') return uiCopy.common.completed
  if (status === 'failed') return uiCopy.common.failed
  if (status === 'cancelled') return uiCopy.common.cancelled
  return uiCopy.common.running
}

function hasProcesses(message) {
  return processGroups(message).length > 0
}

function processEntries(message) {
  if (!message || message.role !== 'assistant') {
    return []
  }
  const content = flattenMessageContent(
    message?.content ?? message?.text ?? message?.detail ?? message?.extra?.content ?? '',
  )
  return [
    ...thinkingEntriesFromContent(content, message.startedAt || message.createdAt),
    ...(Array.isArray(message.processes) ? message.processes : []),
  ]
}

function processGroups(message) {
  return groupProcessLogs(processEntries(message))
}

function timestampKey(value) {
  return String(value || '')
}

function processBlocks(entries, prefix) {
  return groupProcessLogs(entries).map((group) => ({
    id: `${prefix}:${group.id}`,
    type: 'process',
    group,
  }))
}

function messageBlocks(message) {
  const content = displayContent(message)
  const entries = processEntries(message)
  const contentTimestamp = timestampKey(message.createdAt || message.startedAt)

  if (!content.trim()) {
    return processBlocks(entries, 'process-only')
  }

  const beforeContent = []
  const afterContent = []
  for (const entry of entries) {
    if (timestampKey(entry.timestamp) <= contentTimestamp) {
      beforeContent.push(entry)
    } else {
      afterContent.push(entry)
    }
  }

  return [
    ...processBlocks(beforeContent, 'before-content'),
    {
      id: `content:${message.id}`,
      type: 'content',
      content,
    },
    ...processBlocks(afterContent, 'after-content'),
  ]
}

function groupSummary(group) {
  return uiCopy.messageThread.process.groupSummary(group.items.length)
}

function itemText(item) {
  return String(item.summary || item.title || '').trim()
}

function attachmentDownloadUrl(attachment) {
  if (attachment?.downloadUrl) {
    return attachment.downloadUrl
  }
  return ''
}

async function copyMessage(message) {
  try {
    await copyText(displayContent(message))
    ElMessage.success(uiCopy.messageThread.copy.success)
  } catch {
    ElMessage.error(uiCopy.messageThread.copy.failure)
  }
}

function threadWrap() {
  return threadRef.value?.wrapRef || null
}

async function scrollToLatest() {
  await nextTick()
  const wrap = threadWrap()
  if (!wrap) {
    return
  }
  wrap.scrollTop = wrap.scrollHeight
}

async function scrollToTop() {
  await nextTick()
  const wrap = threadWrap()
  if (!wrap) {
    return
  }
  wrap.scrollTop = 0
}

function syncAutoFollowState(scrollTopOverride) {
  const wrap = threadWrap()
  if (!wrap) {
    return
  }
  const nearBottom = isNearBottom(scrollMetrics(wrap, scrollTopOverride))
  autoFollowLatest.value = nearBottom
  userScrollLocked.value = !nearBottom
}

function handleThreadScroll({ scrollTop }) {
  syncAutoFollowState(scrollTop)
}

async function handleRenderedContent() {
  if (!autoFollowLatest.value) {
    return
  }
  await scrollToLatest()
}

watch(
  () => props.sessionId,
  async (sessionId) => {
    if (!sessionId || sessionId === lastSessionId.value) {
      return
    }
    lastSessionId.value = sessionId
    autoFollowLatest.value = isLiveRunSession.value
    userScrollLocked.value = false
    pendingHistoryLoadSessionId.value = isLiveRunSession.value ? '' : sessionId
    if (isLiveRunSession.value) {
      await scrollToLatest()
      return
    }
    await scrollToTop()
    if (!props.loading && pendingHistoryLoadSessionId.value === sessionId) {
      pendingHistoryLoadSessionId.value = ''
    }
  },
  { immediate: true, flush: 'post' },
)

watch(
  () => props.messages,
  async (messages, previousMessages) => {
    const suppressHistoryLoad = pendingHistoryLoadSessionId.value === props.sessionId
    if (
      shouldForceFollowLatest(previousMessages, messages, {
        suppressUserAppend: suppressHistoryLoad,
        forceLiveRun: isLiveRunSession.value,
        userScrollLocked: userScrollLocked.value,
      })
    ) {
      autoFollowLatest.value = true
    }
    if (suppressHistoryLoad) {
      autoFollowLatest.value = false
      await scrollToTop()
      return
    }
    if (!autoFollowLatest.value) {
      return
    }
    await scrollToLatest()
  },
  { flush: 'post' },
)

watch(
  () => props.messageSendScrollKey,
  async (key, previousKey) => {
    if (!key || key === previousKey) {
      return
    }
    pendingHistoryLoadSessionId.value = ''
    userScrollLocked.value = false
    autoFollowLatest.value = true
    await scrollToLatest()
  },
  { flush: 'post' },
)

watch(
  () => props.loading,
  async (loading) => {
    if (loading || pendingHistoryLoadSessionId.value !== props.sessionId) {
      return
    }
    pendingHistoryLoadSessionId.value = ''
    if (!isLiveRunSession.value) {
      autoFollowLatest.value = false
      userScrollLocked.value = false
      await scrollToTop()
    }
  },
  { immediate: true, flush: 'post' },
)

onMounted(async () => {
  if (isLiveRunSession.value) {
    autoFollowLatest.value = true
    userScrollLocked.value = false
    await scrollToLatest()
    return
  }
  await scrollToTop()
  syncAutoFollowState()
})
</script>

<template>
  <el-scrollbar ref="threadRef" class="message-thread" role="log" aria-live="polite" @scroll="handleThreadScroll">
    <article
      v-for="message in visibleMessages"
      :key="message.id"
      class="message-row"
      :data-role="message.role"
    >
      <div class="message-bubble">
        <div class="message-header">
          <div class="message-header-main">
            <strong>{{ roleLabel(message.role) }}</strong>
            <span class="message-timestamp">{{ formatDateTime(message.createdAt) }}</span>
          </div>
          <el-button
            class="message-copy-button"
            text
            size="small"
            :icon="CopyDocument"
            :aria-label="uiCopy.messageThread.copy.message"
            @click="copyMessage(message)"
          />
        </div>
        <template v-for="block in messageBlocks(message)" :key="block.id">
          <details
            v-if="block.type === 'process'"
            class="process-block"
            :data-kind="block.group.kind"
            :data-status="block.group.status"
          >
            <summary>
              <span>{{ block.group.title }}</span>
              <span>{{ groupSummary(block.group) }} · {{ processStatusLabel(block.group.status) }}</span>
            </summary>
            <ol class="process-list">
              <li v-for="item in block.group.items" :key="item.id">
                <details class="process-item" :data-kind="item.kind" :data-status="item.status">
                  <summary>
                    <strong>{{ item.title }}</strong>
                    <span>{{ processStatusLabel(item.status) }}</span>
                  </summary>
                  <pre v-if="itemText(item)">{{ itemText(item) }}</pre>
                </details>
              </li>
            </ol>
          </details>
          <MarkdownContent
            v-else-if="block.type === 'content'"
            :content="block.content"
            @content-rendered="handleRenderedContent"
          />
        </template>
        <p v-if="message.streaming" class="streaming-indicator">{{ uiCopy.messageThread.streaming }}</p>
        <ul v-if="message.attachments && message.attachments.length" class="attachment-list">
          <li v-for="attachment in message.attachments" :key="attachment.id">
            <span>{{ attachment.name }}</span>
            <el-button
              v-if="attachmentDownloadUrl(attachment)"
              class="attachment-download-button"
              tag="a"
              text
              size="small"
              :href="attachmentDownloadUrl(attachment)"
              :icon="Download"
              :aria-label="uiCopy.messageThread.download"
              :title="uiCopy.messageThread.download"
            />
          </li>
        </ul>
      </div>
    </article>
  </el-scrollbar>
</template>
