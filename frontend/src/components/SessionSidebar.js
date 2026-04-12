import { defineComponent } from 'vue'
import { formatDateTime } from '../lib/time.js'

export default defineComponent({
  name: 'SessionSidebar',
  props: {
    currentSessionId: {
      type: String,
      default: '',
    },
    error: {
      type: String,
      default: '',
    },
    loading: {
      type: Boolean,
      default: false,
    },
    sessions: {
      type: Array,
      default: () => [],
    },
  },
  emits: ['new-session', 'refresh', 'select-session'],
  methods: {
    formatDateTime,
  },
  template: `
    <div class="sidebar">
      <div class="sidebar-header">
        <div>
          <p class="eyebrow">Sessions</p>
          <h2>History</h2>
        </div>
        <div class="sidebar-actions">
          <button class="secondary-button" type="button" @click="$emit('refresh')">Refresh</button>
          <button class="primary-button" type="button" @click="$emit('new-session')">New</button>
        </div>
      </div>

      <p v-if="error" class="inline-error">{{ error }}</p>
      <p v-if="loading" class="muted-copy">Loading sessions…</p>
      <p v-else-if="sessions.length === 0" class="muted-copy">No sessions yet. Start a conversation to create one.</p>

      <ul class="session-list" v-else>
        <li v-for="session in sessions" :key="session.id">
          <button
            class="session-button"
            :class="{ active: session.id === currentSessionId }"
            type="button"
            @click="$emit('select-session', session.id)"
          >
            <span class="session-title">{{ session.title }}</span>
            <span class="session-meta">{{ formatDateTime(session.updatedAt) }}</span>
          </button>
        </li>
      </ul>
    </div>
  `,
})
