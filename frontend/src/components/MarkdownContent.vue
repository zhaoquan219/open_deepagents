<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'

import { hydrateMermaidBlocks, renderMarkdownToHtml } from '../lib/markdown.js'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const container = ref(null)

async function renderContent() {
  if (!container.value) {
    return
  }

  container.value.innerHTML = renderMarkdownToHtml(props.content)
  await nextTick()
  await hydrateMermaidBlocks(container.value)
}

watch(
  () => props.content,
  async () => {
    await renderContent()
  },
  { flush: 'post' },
)

onMounted(async () => {
  await renderContent()
})
</script>

<template>
  <div ref="container" class="markdown-content"></div>
</template>
