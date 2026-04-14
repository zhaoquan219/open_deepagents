<script setup>
import { nextTick, ref, watch } from 'vue'

import { formatDateTime } from '../lib/time.js'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
})

const threadRef = ref(null)

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
    return '发起人'
  }
  if (role === 'assistant') {
    return '智能助理'
  }
  return '系统'
}

function displayContent(message) {
  const content = flattenMessageContent(
    message?.content ?? message?.text ?? message?.detail ?? message?.extra?.content ?? '',
  )
  if (content.trim()) {
    return content
  }
  if (message?.streaming) {
    return '正在生成回复…'
  }
  return '（空消息）'
}

async function scrollToLatest() {
  await nextTick()
  if (!threadRef.value) {
    return
  }
  const height = threadRef.value.wrapRef?.scrollHeight || 0
  threadRef.value.setScrollTop(height)
}

watch(
  () => props.messages,
  async () => {
    await scrollToLatest()
  },
  { deep: true, immediate: true },
)
</script>

<template>
  <el-scrollbar ref="threadRef" class="message-thread" role="log" aria-live="polite">
    <article
      v-for="message in props.messages"
      :key="message.id"
      class="message-row"
      :data-role="message.role"
    >
      <div class="message-bubble">
        <div class="message-header">
          <div class="message-header-main">
            <span class="message-role-pill">{{ roleLabel(message.role) }}</span>
            <span class="message-timestamp">{{ formatDateTime(message.createdAt) }}</span>
          </div>
        </div>
        <MarkdownContent :content="displayContent(message)" />
        <p v-if="message.streaming" class="streaming-indicator">正在生成回复…</p>
        <ul v-if="message.attachments && message.attachments.length" class="attachment-list">
          <li v-for="attachment in message.attachments" :key="attachment.id">{{ attachment.name }}</li>
        </ul>
      </div>
    </article>
  </el-scrollbar>
</template>
