<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { uiCopy } from '../lib/copy.js'
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
const lastSessionId = ref('')
const pendingHistoryLoadSessionId = ref('')

const isLiveRunSession = computed(
  () =>
    Boolean(props.sessionId) &&
    props.sessionId === props.activeRunSessionId &&
    ['queued', 'running'].includes(props.runStatus),
)

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
  if (content.trim()) {
    return content
  }
  if (message?.streaming) {
    return uiCopy.messageThread.streaming
  }
  return uiCopy.messageThread.empty
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
  autoFollowLatest.value = isNearBottom(scrollMetrics(wrap, scrollTopOverride))
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
    if (shouldForceFollowLatest(previousMessages, messages, { suppressUserAppend: suppressHistoryLoad })) {
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
  { deep: true, flush: 'post' },
)

watch(
  () => props.messageSendScrollKey,
  async (key, previousKey) => {
    if (!key || key === previousKey) {
      return
    }
    pendingHistoryLoadSessionId.value = ''
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
      await scrollToTop()
    }
  },
  { immediate: true, flush: 'post' },
)

onMounted(async () => {
  if (isLiveRunSession.value) {
    autoFollowLatest.value = true
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
      v-for="message in props.messages"
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
        </div>
        <MarkdownContent :content="displayContent(message)" @content-rendered="handleRenderedContent" />
        <p v-if="message.streaming" class="streaming-indicator">{{ uiCopy.messageThread.streaming }}</p>
        <ul v-if="message.attachments && message.attachments.length" class="attachment-list">
          <li v-for="attachment in message.attachments" :key="attachment.id">{{ attachment.name }}</li>
        </ul>
      </div>
    </article>
  </el-scrollbar>
</template>
