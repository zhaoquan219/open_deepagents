import { defineComponent } from 'vue'
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
  methods: {
    formatDateTime,
  },
  template: `
    <div class="message-thread">
      <article v-for="message in messages" :key="message.id" class="message-card" :data-role="message.role">
        <div class="message-header">
          <strong>{{ message.role }}</strong>
          <span>{{ formatDateTime(message.createdAt) }}</span>
        </div>
        <markdown-content :content="message.content" />
        <p v-if="message.streaming" class="streaming-indicator">Streaming…</p>
        <ul v-if="message.attachments && message.attachments.length" class="attachment-list">
          <li v-for="attachment in message.attachments" :key="attachment.id">{{ attachment.name }}</li>
        </ul>
      </article>
    </div>
  `,
})
