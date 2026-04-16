<script setup>
import { onMounted, ref, watch } from 'vue'

import { renderMermaidSvg } from '../lib/markdown.js'

const props = defineProps({
  source: {
    type: String,
    default: '',
  },
  diagramKey: {
    type: String,
    default: 'mermaid',
  },
})

const container = ref(null)
let renderSequence = 0

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

async function hydrate() {
  if (!container.value) {
    return
  }

  const source = String(props.source || '').trim()
  if (!source) {
    container.value.innerHTML = ''
    return
  }

  const currentSequence = ++renderSequence

  try {
    const svg = await renderMermaidSvg(`${props.diagramKey}-${currentSequence}`, source)
    if (!container.value || currentSequence !== renderSequence) {
      return
    }
    container.value.innerHTML = svg
  } catch (error) {
    if (!container.value || currentSequence !== renderSequence) {
      return
    }
    const message = error instanceof Error ? error.message : 'Unable to render mermaid diagram.'
    container.value.innerHTML = `<pre class="mermaid-error">${escapeHtml(message)}</pre>`
  }
}

watch(
  () => props.source,
  async (nextSource, previousSource) => {
    if (nextSource === previousSource && container.value?.innerHTML) {
      return
    }
    await hydrate()
  },
)

onMounted(async () => {
  await hydrate()
})
</script>

<template>
  <div ref="container" class="mermaid-block" :data-mermaid-key="props.diagramKey"></div>
</template>
