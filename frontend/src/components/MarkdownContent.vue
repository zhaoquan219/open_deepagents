<script setup>
/* eslint-disable vue/no-v-html */
import { computed } from 'vue'

import { uiCopy } from '../lib/copy.js'
import { renderMarkdownFragmentToHtml } from '../lib/markdown.js'
import { parseMarkdownSegments } from '../lib/markdownSegments.js'
import MermaidBlock from './MermaidBlock.vue'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['content-rendered'])

const segments = computed(() =>
  parseMarkdownSegments(props.content).map((segment) => {
    if (segment.type === 'markdown' || segment.type === 'thinking') {
      return {
        ...segment,
        html: renderMarkdownFragmentToHtml(segment.content),
      }
    }
    return segment
  }),
)

function thinkingSummary(kind) {
  return kind === 'reasoning' ? uiCopy.common.reasoning : uiCopy.common.thinking
}
</script>

<template>
  <div class="markdown-content">
    <template v-for="segment in segments" :key="segment.key">
      <div
        v-if="segment.type === 'markdown'"
        class="markdown-segment"
        v-html="segment.html"
      ></div>
      <MermaidBlock
        v-else-if="segment.type === 'mermaid'"
        :diagram-key="segment.key"
        :source="segment.source"
        @rendered="emit('content-rendered')"
      />
      <details v-else class="thinking-block">
        <summary>{{ thinkingSummary(segment.kind) }}</summary>
        <div class="thinking-block-body markdown-segment" v-html="segment.html"></div>
      </details>
    </template>
  </div>
</template>
