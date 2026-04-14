<script setup>
import { Plus, RefreshRight } from '@element-plus/icons-vue'

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
        <p class="eyebrow">会话</p>
        <h2>历史会话</h2>
        <p class="sidebar-summary">
          {{ props.currentSessionId ? '切换任意会话都可以继续它的上下文。' : '新建一条会话后即可开始对话。' }}
        </p>
      </div>
      <el-button size="small" type="primary" :icon="Plus" @click="$emit('new-session')">新建</el-button>
    </div>

    <div class="sidebar-toolbar">
      <span class="muted-copy">按最近更新时间排序</span>
      <el-button size="small" :icon="RefreshRight" plain @click="$emit('refresh')">刷新</el-button>
    </div>

    <p v-if="props.error" class="inline-error sidebar-hint">{{ props.error }}</p>

    <el-empty
      v-if="props.loading"
      class="sidebar-empty"
      :image-size="72"
      description="正在加载会话…"
    />
    <el-empty
      v-else-if="props.sessions.length === 0"
      class="sidebar-empty"
      :image-size="72"
      description="还没有历史会话，开始一段新对话吧。"
    />

    <el-scrollbar v-else class="session-list-scrollbar">
      <ul class="session-list">
        <li v-for="session in props.sessions" :key="session.id" class="session-item">
          <div class="session-card" :class="{ active: session.id === props.currentSessionId }">
            <button class="session-button" type="button" @click="$emit('select-session', session.id)">
              <span class="session-state-dot" :class="{ active: session.id === props.currentSessionId }"></span>
              <span class="session-copy">
                <span class="session-title">{{ session.title || '未命名会话' }}</span>
                <span class="session-meta">
                  {{ session.id === props.currentSessionId ? '当前会话' : '点击继续对话' }}
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
              {{ props.deletingSessionId === session.id ? '删除中' : '移除' }}
            </el-button>
          </div>
        </li>
      </ul>
    </el-scrollbar>
  </div>
</template>
