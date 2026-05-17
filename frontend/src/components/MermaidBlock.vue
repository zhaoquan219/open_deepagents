<script setup>
import { CopyDocument, Picture } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { onMounted, ref, watch } from "vue";

import { copyRenderedSvgAsPng, copyText } from "../lib/clipboard.js";
import { uiCopy } from "../lib/copy.js";
import { renderMermaidSvg } from "../lib/markdown.js";

const props = defineProps({
  source: {
    type: String,
    default: "",
  },
  diagramKey: {
    type: String,
    default: "mermaid",
  },
});

const emit = defineEmits(["rendered"]);

const container = ref(null);
const renderIdPrefix = `mermaid-${Math.random().toString(36).slice(2)}`;
let renderSequence = 0;

async function copySource() {
  try {
    await copyText(props.source);
    ElMessage.success(uiCopy.mermaid.copySourceSuccess);
  } catch {
    ElMessage.error(uiCopy.mermaid.copyFailure);
  }
}

async function copyImage() {
  const svgElement = container.value?.querySelector("svg");
  try {
    await copyRenderedSvgAsPng(svgElement);
    ElMessage.success(uiCopy.mermaid.copyImageSuccess);
  } catch {
    ElMessage.error(uiCopy.mermaid.copyFailure);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function hydrate() {
  if (!container.value) {
    return;
  }

  const source = String(props.source || "").trim();
  if (!source) {
    container.value.innerHTML = "";
    return;
  }

  const currentSequence = ++renderSequence;

  try {
    const svg = await renderMermaidSvg(
      `${renderIdPrefix}-${currentSequence}`,
      source,
    );
    if (!container.value || currentSequence !== renderSequence) {
      return;
    }
    container.value.replaceChildren();
    container.value.insertAdjacentHTML("beforeend", svg);
    emit("rendered");
  } catch (error) {
    if (!container.value || currentSequence !== renderSequence) {
      return;
    }
    const message =
      error instanceof Error
        ? error.message
        : "Unable to render mermaid diagram.";
    container.value.innerHTML = `<pre class="mermaid-error">${escapeHtml(message)}</pre>`;
    emit("rendered");
  }
}

watch(
  () => props.source,
  async (nextSource, previousSource) => {
    if (nextSource === previousSource && container.value?.innerHTML) {
      return;
    }
    await hydrate();
  },
);

onMounted(async () => {
  await hydrate();
});
</script>

<template>
  <div class="mermaid-shell">
    <div class="mermaid-toolbar">
      <el-button
        text
        size="small"
        :icon="CopyDocument"
        :aria-label="uiCopy.mermaid.copySource"
        @click="copySource"
      />
      <el-button
        text
        size="small"
        :icon="Picture"
        :aria-label="uiCopy.mermaid.copyImage"
        @click="copyImage"
      />
    </div>
    <div
      ref="container"
      class="mermaid-block"
      :data-mermaid-key="props.diagramKey"
    ></div>
  </div>
</template>
