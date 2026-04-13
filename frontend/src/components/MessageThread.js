import { defineComponent, nextTick, ref, watch } from 'vue'
import { formatDateTime } from '../lib/time.js'
import MarkdownContent from './MarkdownContent.js'

export default defineComponent({
  name: 'MessageThread',
  components: {
    MarkdownContent,
  },
  props: {
    messages: {
      type: Array,
      default: () => [],
    },
  },
  setup(props) {
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
        return '我'
      }
      if (role === 'assistant') {
        return '智能助手'
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
        return '正在生成内容...'
      }
      return '（空消息）'
    }

    async function scrollToLatest() {
      await nextTick()
      if (!threadRef.value) {
        return
      }
      threadRef.value.scrollTop = threadRef.value.scrollHeight
    }

    watch(
      () => props.messages,
      async () => {
        await scrollToLatest()
      },
      { deep: true, immediate: true },
    )

    return {
      displayContent,
      formatDateTime,
      roleLabel,
      threadRef,
    }
  },
  template: `
    <div ref="threadRef" class="message-thread">
      <article v-for="message in messages" :key="message.id" class="message-row" :data-role="message.role">
        <div class="message-bubble">
          <div class="message-header">
            <strong>{{ roleLabel(message.role) }}</strong>
            <span>{{ formatDateTime(message.createdAt) }}</span>
          </div>
          <markdown-content :content="displayContent(message)" />
          <p v-if="message.streaming" class="streaming-indicator">正在生成回复…</p>
          <ul v-if="message.attachments && message.attachments.length" class="attachment-list">
            <li v-for="attachment in message.attachments" :key="attachment.id">{{ attachment.name }}</li>
          </ul>
        </div>
      </article>
    </div>
  `,
})
