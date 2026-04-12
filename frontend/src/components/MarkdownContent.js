import { defineComponent, nextTick, ref, watch } from 'vue'
import { hydrateMermaidBlocks, renderMarkdownToHtml } from '../lib/markdown.js'

export default defineComponent({
  name: 'MarkdownContent',
  props: {
    content: {
      type: String,
      default: '',
    },
  },
  setup(props) {
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
      { immediate: true },
    )

    return {
      container,
    }
  },
  template: '<div ref="container" class="markdown-content"></div>',
})
