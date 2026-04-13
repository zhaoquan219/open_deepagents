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
    deletingSessionId: {
      type: String,
      default: '',
    },
  },
  emits: ['new-session', 'refresh', 'select-session', 'delete-session'],
  methods: {
    formatDateTime,
  },
  template: `
    <div class="sidebar">
      <div class="sidebar-header">
        <div>
          <p class="eyebrow">会话</p>
          <h2>聊天记录</h2>
        </div>
        <div class="sidebar-actions">
          <button class="secondary-button" type="button" @click="$emit('refresh')">刷新</button>
          <button class="primary-button" type="button" @click="$emit('new-session')">新建会话</button>
        </div>
      </div>

      <p v-if="error" class="inline-error">{{ error }}</p>
      <p v-if="loading" class="muted-copy sidebar-hint">正在加载会话…</p>
      <p v-else-if="sessions.length === 0" class="muted-copy sidebar-hint">还没有历史会话，开始一段新对话吧。</p>

      <ul class="session-list" v-else>
        <li v-for="session in sessions" :key="session.id">
          <div
            class="session-card"
            :class="{ active: session.id === currentSessionId }"
          >
            <button
              class="session-button"
              type="button"
              @click="$emit('select-session', session.id)"
            >
              <span class="session-title">{{ session.title }}</span>
              <span class="session-meta">{{ formatDateTime(session.updatedAt) }}</span>
            </button>
            <button
              class="session-delete"
              type="button"
              :disabled="deletingSessionId === session.id"
              @click="$emit('delete-session', session.id)"
            >
              {{ deletingSessionId === session.id ? '删除中' : '删除' }}
            </button>
          </div>
        </li>
      </ul>
    </div>
  `,
})
