<script setup>
import { RefreshRight } from '@element-plus/icons-vue'

import { uiCopy } from '../lib/copy.js'

defineEmits(['new-session', 'refresh', 'select-session', 'delete-session'])

const props = defineProps({
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
})
</script>

<template>
  <div class="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-heading">
        <p class="eyebrow">{{ uiCopy.sidebar.eyebrow }}</p>
        <h2>{{ uiCopy.sidebar.title }}</h2>
        <p class="muted-copy sidebar-header-copy">
          {{ props.currentSessionId ? uiCopy.sidebar.currentHint : uiCopy.sidebar.emptyHint }}
        </p>
      </div>
    </div>

    <div class="sidebar-toolbar">
      <div class="sidebar-toolbar-actions">
        <el-button
          class="sidebar-toolbar-button sidebar-new-button"
          type="primary"
          @click="$emit('new-session')"
        >
          <span class="button-plus-sign" aria-hidden="true">+</span>
          <span>{{ uiCopy.sidebar.newSession }}</span>
        </el-button>
        <el-button
          class="sidebar-toolbar-button sidebar-refresh-button"
          :icon="RefreshRight"
          plain
          @click="$emit('refresh')"
        >
          {{ uiCopy.sidebar.refresh }}
        </el-button>
      </div>
    </div>

    <p v-if="props.error" class="inline-error sidebar-hint">{{ props.error }}</p>

    <el-empty
      v-if="props.loading"
      class="sidebar-empty"
      :image-size="72"
      :description="uiCopy.sidebar.loading"
    />
    <el-empty
      v-else-if="props.sessions.length === 0"
      class="sidebar-empty"
      :image-size="72"
      :description="uiCopy.sidebar.empty"
    />

    <el-scrollbar v-else class="session-list-scrollbar">
      <ul class="session-list">
        <li v-for="session in props.sessions" :key="session.id" class="session-item">
          <div class="session-card" :class="{ active: session.id === props.currentSessionId }">
            <button class="session-button" type="button" @click="$emit('select-session', session.id)">
              <span class="session-state-dot" :class="{ active: session.id === props.currentSessionId }"></span>
              <span class="session-copy">
                <span class="session-title">{{ session.title || uiCopy.sidebar.unnamed }}</span>
                <span class="session-meta">
                  {{ session.id === props.currentSessionId ? uiCopy.sidebar.currentSession : uiCopy.sidebar.continueSession }}
                </span>
              </span>
            </button>
            <el-button
              class="session-delete"
              size="small"
              text
              type="danger"
              :loading="props.deletingSessionId === session.id"
              @click="$emit('delete-session', session.id)"
            >
              {{ props.deletingSessionId === session.id ? uiCopy.sidebar.deleting : uiCopy.sidebar.remove }}
            </el-button>
          </div>
        </li>
      </ul>
    </el-scrollbar>
  </div>
</template>
