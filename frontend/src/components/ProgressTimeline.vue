<script setup>
import { computed } from 'vue'

const props = defineProps({
  activeRun: {
    type: Object,
    default: null,
  },
  canStop: {
    type: Boolean,
    default: false,
  },
  connectionState: {
    type: String,
    default: 'idle',
  },
  diagnostics: {
    type: Array,
    default: () => [],
  },
  runtimeError: {
    type: String,
    default: '',
  },
  dismissible: {
    type: Boolean,
    default: false,
  },
})
defineEmits(['close'])

function statusLabel(status) {
  if (status === 'info') return '信息'
  if (status === 'running' || status === 'in_progress') return '处理中'
  if (status === 'completed') return '已完成'
  if (status === 'cancelling') return '停止中'
  if (status === 'cancelled') return '已停止'
  if (status === 'failed') return '失败'
  if (status === 'queued') return '排队中'
  return '待命'
}

function panelStatus(activeRun, runtimeError, diagnostics) {
  if (activeRun?.status) return activeRun.status
  if (runtimeError || diagnostics.some((entry) => entry.status === 'failed')) return 'failed'
  return 'idle'
}

function connectionLabel(state) {
  if (state === 'open') return '已连接'
  if (state === 'connecting') return '连接中'
  if (state === 'closed') return '已关闭'
  if (state === 'error') return '连接异常'
  return '未连接'
}

function entryLabel(entry) {
  const label = String(entry?.label || '')
  const labelMap = {
    '运行已启动': '运行已启动',
    '运行已创建': '运行已创建',
    '运行创建成功': '运行创建成功',
    '运行启动失败': '运行启动失败',
    '运行失败': '运行失败',
    排队中: '排队中',
    处理中: '处理中',
    处理完成: '处理完成',
    处理失败: '处理失败',
    '最终回复已写入会话': '最终回复已写入会话',
    '实时连接已建立': '实时连接已建立',
    '实时连接已关闭': '实时连接已关闭',
    '附件上传完成': '附件上传完成',
    '附件上传失败': '附件上传失败',
    'Run started': '开始处理',
    'Run running': '正在处理',
    'Run completed': '处理完成',
    'Run cancelled': '已手动停止',
    'Run failed': '处理失败',
    'Assistant message finalized': '最终回复已写入会话',
  }
  if (labelMap[label]) return labelMap[label]
  if (entry?.kind === 'message.delta') return '正在生成回复'
  if (entry?.kind === 'message.final') {
    return entry?.status === 'completed' ? '回复生成完成' : '回复已更新'
  }
  if (entry?.kind === 'tool') return '工具执行'
  if (entry?.kind === 'skill') return '技能执行'
  if (entry?.kind === 'sandbox') return '沙箱执行'
  if (entry?.kind === 'connection') return '连接状态变更'
  if (entry?.kind === 'status') return '状态更新'
  if (entry?.kind === 'error') return '处理异常'
  return label || '进度更新'
}

function entryDetail(entry) {
  if (entry?.kind === 'message.delta') {
    const count = Number(entry?.aggregateCount || 1)
    if (count > 1) return `已连续接收 ${count} 段回复内容。`
    return entry?.detail || '正在持续生成回复。'
  }
  return entry?.detail || ''
}

const mergedEntries = computed(() => {
  const runEntries = Array.isArray(props.activeRun?.timeline) ? props.activeRun.timeline : []
  const entries = [...props.diagnostics, ...runEntries].sort((left, right) =>
    String(left.timestamp).localeCompare(String(right.timestamp)),
  )
  const merged = []

  for (const entry of entries) {
    const normalized = {
      ...entry,
      aggregateCount: Number(entry?.aggregateCount || 1),
    }
    const previous = merged.at(-1)

    if (normalized.kind === 'message.delta' && previous?.kind === 'message.delta') {
      previous.aggregateCount += normalized.aggregateCount
      previous.timestamp = normalized.timestamp
      previous.status = normalized.status || previous.status
      continue
    }

    if (
      previous &&
      previous.kind === normalized.kind &&
      previous.label === normalized.label &&
      previous.detail === normalized.detail &&
      previous.status === normalized.status
    ) {
      previous.aggregateCount += normalized.aggregateCount
      previous.timestamp = normalized.timestamp
      continue
    }

    merged.push(normalized)
  }

  return merged
})

const summaryStatus = computed(() =>
  panelStatus(props.activeRun, props.runtimeError, props.diagnostics),
)
const latestActivity = computed(() =>
  mergedEntries.value.length ? entryLabel(mergedEntries.value.at(-1)) : '暂无',
)
const shortRunId = computed(() => {
  const runId = String(props.activeRun?.runId || '')
  return runId ? runId.slice(-8).toUpperCase() : ''
})

function statusTagType(status) {
  if (status === 'failed') return 'danger'
  if (status === 'completed') return 'success'
  if (status === 'cancelled' || status === 'cancelling') return 'warning'
  return 'primary'
}
</script>

<template>
  <div class="runtime-card">
    <div class="runtime-header">
      <div class="runtime-heading">
        <p class="eyebrow">运行面板</p>
        <h3>执行脉络</h3>
        <p class="runtime-copy">
          {{
            props.connectionState === 'open'
              ? '已连接实时通道，工具动作和回复会持续滚动进来。'
              : props.connectionState === 'connecting'
                ? '正在恢复实时连接，界面会在恢复后继续接收进度。'
                : props.connectionState === 'error'
                  ? '实时连接恢复失败，请查看错误后重试。'
                  : '当前没有活跃运行，新的提问会在这里展示执行过程。'
          }}
        </p>
      </div>
      <div class="runtime-header-actions">
        <el-tag size="small" :type="statusTagType(summaryStatus)" effect="light">
          {{ statusLabel(summaryStatus) }}
        </el-tag>
        <el-button v-if="props.dismissible" size="small" text @click="$emit('close')">收起</el-button>
      </div>
    </div>

    <p v-if="props.activeRun && props.canStop" class="runtime-action-hint">
      停止当前运行请使用底部输入区的主按钮。
    </p>

    <el-empty
      v-if="!props.activeRun && props.diagnostics.length === 0"
      class="runtime-empty"
      :image-size="72"
      description="发起提问后，这里会显示本轮处理进度。"
    />

    <template v-else>
      <el-alert
        v-if="props.runtimeError"
        :closable="false"
        type="error"
        show-icon
        :title="props.runtimeError"
      />

      <dl class="runtime-summary-grid">
        <div>
          <dt>连接</dt>
          <dd>{{ connectionLabel(props.connectionState) }}</dd>
        </div>
        <div>
          <dt>运行</dt>
          <dd>{{ shortRunId || '暂无' }}</dd>
        </div>
        <div>
          <dt>最近活动</dt>
          <dd>{{ latestActivity }}</dd>
        </div>
      </dl>

      <el-scrollbar class="runtime-list-scrollbar">
        <ol class="runtime-list">
          <li
            v-for="entry in mergedEntries"
            :key="entry.id"
            class="runtime-entry"
            :data-status="entry.status"
          >
            <div class="runtime-entry-main">
              <p class="runtime-label">{{ entryLabel(entry) }}</p>
              <p v-if="entryDetail(entry)" class="runtime-detail">{{ entryDetail(entry) }}</p>
            </div>
            <el-tag size="small" effect="plain" :type="statusTagType(entry.status)">
              {{ statusLabel(entry.status) }}
            </el-tag>
          </li>
        </ol>
      </el-scrollbar>
    </template>
  </div>
</template>
