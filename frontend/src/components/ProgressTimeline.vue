<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { statusText, uiCopy } from '../lib/copy.js'
import { isNearBottom } from '../lib/scroll.js'

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
  stopping: {
    type: Boolean,
    default: false,
  },
})
defineEmits(['close'])

const timelineRef = ref(null)
const autoFollowLatest = ref(true)

function statusLabel(status) {
  if (status === 'info') return uiCopy.common.info
  if (status === 'in_progress') return uiCopy.common.running
  return statusText(status)
}

function panelStatus(activeRun, runtimeError, diagnostics) {
  if (activeRun?.status) return activeRun.status
  if (runtimeError || diagnostics.some((entry) => entry.status === 'failed')) return 'failed'
  return 'idle'
}

function connectionLabel(state) {
  if (state === 'open') return uiCopy.timeline.connection.open
  if (state === 'connecting') return uiCopy.timeline.connection.connecting
  if (state === 'closed') return uiCopy.timeline.connection.closed
  if (state === 'error') return uiCopy.timeline.connection.error
  return uiCopy.timeline.connection.idle
}

function entryLabel(entry) {
  const label = String(entry?.label || '')
  const labelMap = uiCopy.timeline.sourceLabels
  if (labelMap[label]) return labelMap[label]
  if (entry?.kind === 'message.delta') return uiCopy.timeline.labels.messageDelta
  if (entry?.kind === 'message.final') {
    return entry?.status === 'completed'
      ? uiCopy.timeline.labels.messageFinalCompleted
      : uiCopy.timeline.labels.messageFinalUpdated
  }
  if (entry?.kind === 'tool') return uiCopy.timeline.labels.tool
  if (entry?.kind === 'skill') return uiCopy.timeline.labels.skill
  if (entry?.kind === 'sandbox') return uiCopy.timeline.labels.sandbox
  if (entry?.kind === 'connection') return uiCopy.timeline.labels.connection
  if (entry?.kind === 'status') return uiCopy.timeline.labels.status
  if (entry?.kind === 'error') return uiCopy.timeline.labels.error
  return label || uiCopy.timeline.labels.progress
}

function entryDetail(entry) {
  if (entry?.kind === 'message.delta') {
    const count = Number(entry?.aggregateCount || 1)
    if (count > 1) return uiCopy.timeline.detail.deltaAggregate(count)
    return entry?.detail || uiCopy.timeline.detail.deltaStreaming
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
  mergedEntries.value.length ? entryLabel(mergedEntries.value.at(-1)) : uiCopy.common.emptyRecent,
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

function timelineWrap() {
  return timelineRef.value?.wrapRef || null
}

async function scrollToLatest() {
  await nextTick()
  const wrap = timelineWrap()
  if (!wrap) {
    return
  }
  wrap.scrollTop = wrap.scrollHeight
}

function syncAutoFollowState(scrollTopOverride) {
  const wrap = timelineWrap()
  if (!wrap) {
    return
  }
  autoFollowLatest.value = isNearBottom({
    scrollTop: scrollTopOverride ?? wrap.scrollTop,
    clientHeight: wrap.clientHeight,
    scrollHeight: wrap.scrollHeight,
  })
}

function handleTimelineScroll({ scrollTop }) {
  syncAutoFollowState(scrollTop)
}

watch(
  () => String(props.activeRun?.runId || ''),
  async (runId, previousRunId) => {
    if (runId && runId !== previousRunId) {
      autoFollowLatest.value = true
      await scrollToLatest()
    }
  },
)

watch(
  mergedEntries,
  async () => {
    if (!autoFollowLatest.value) {
      return
    }
    await scrollToLatest()
  },
  { deep: true, flush: 'post' },
)

onMounted(async () => {
  await scrollToLatest()
  syncAutoFollowState()
})
</script>

<template>
  <div class="runtime-card">
    <div class="runtime-header">
      <div class="runtime-heading">
        <p class="eyebrow">{{ uiCopy.timeline.eyebrow }}</p>
        <h3>{{ uiCopy.timeline.title }}</h3>
        <p class="runtime-copy">
          {{
            props.connectionState === 'open'
              ? uiCopy.timeline.copy.open
              : props.connectionState === 'connecting'
                ? uiCopy.timeline.copy.connecting
                : props.connectionState === 'error'
                  ? uiCopy.timeline.copy.error
                  : uiCopy.timeline.copy.idle
          }}
        </p>
      </div>
      <div class="runtime-header-actions">
        <el-tag size="small" :type="statusTagType(summaryStatus)" effect="light">
          {{ statusLabel(summaryStatus) }}
        </el-tag>
        <el-button v-if="props.dismissible" size="small" text @click="$emit('close')">
          {{ uiCopy.timeline.close }}
        </el-button>
      </div>
    </div>

    <el-empty
      v-if="!props.activeRun && props.diagnostics.length === 0"
      class="runtime-empty"
      :image-size="72"
      :description="uiCopy.timeline.empty"
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
          <dt>{{ uiCopy.timeline.summary.connection }}</dt>
          <dd>{{ connectionLabel(props.connectionState) }}</dd>
        </div>
        <div>
          <dt>{{ uiCopy.timeline.summary.run }}</dt>
          <dd>{{ shortRunId || uiCopy.common.emptyRecent }}</dd>
        </div>
        <div>
          <dt>{{ uiCopy.timeline.summary.latest }}</dt>
          <dd>{{ latestActivity }}</dd>
        </div>
      </dl>

      <el-scrollbar
        ref="timelineRef"
        class="runtime-list-scrollbar"
        @scroll="handleTimelineScroll"
      >
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
