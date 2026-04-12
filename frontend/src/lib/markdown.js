import mermaid from 'mermaid'
import { marked } from 'marked'

const DISALLOWED_TAGS = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta'])

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  theme: 'neutral',
})

function injectMermaidPlaceholders(markdown) {
  return markdown.replace(/```mermaid\s*([\s\S]*?)```/g, (_match, code) => {
    const encoded = encodeURIComponent(code.trim())
    return `
<div class="mermaid-block" data-mermaid-source="${encoded}"></div>
`
  })
}

function sanitizeHtml(html) {
  const parser = new DOMParser()
  const documentNode = parser.parseFromString(html, 'text/html')

  for (const element of documentNode.body.querySelectorAll('*')) {
    const tagName = element.tagName.toLowerCase()
    if (DISALLOWED_TAGS.has(tagName)) {
      element.remove()
      continue
    }

    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase()
      const value = attribute.value.trim().toLowerCase()
      const allowedMermaid = name === 'data-mermaid-source' && element.classList.contains('mermaid-block')
      if (name.startsWith('on')) {
        element.removeAttribute(attribute.name)
        continue
      }
      if ((name === 'href' || name === 'src') && value.startsWith('javascript:')) {
        element.removeAttribute(attribute.name)
        continue
      }
      if (name.startsWith('data-') && !allowedMermaid) {
        element.removeAttribute(attribute.name)
      }
    }

    if (element.tagName.toLowerCase() === 'a') {
      element.setAttribute('target', '_blank')
      element.setAttribute('rel', 'noreferrer noopener')
    }
  }

  return documentNode.body.innerHTML
}

export function renderMarkdownToHtml(markdown) {
  const source = injectMermaidPlaceholders(markdown || '')
  const rendered = marked.parse(source, { breaks: true, gfm: true })
  return sanitizeHtml(rendered)
}

export async function hydrateMermaidBlocks(rootNode) {
  const blocks = [...rootNode.querySelectorAll('.mermaid-block')]
  for (const [index, block] of blocks.entries()) {
    const rawSource = block.getAttribute('data-mermaid-source')
    if (!rawSource) {
      continue
    }

    try {
      const source = decodeURIComponent(rawSource)
      const result = await mermaid.render(`mermaid-${index}-${Date.now()}`, source)
      const svg = typeof result === 'string' ? result : result.svg
      block.innerHTML = svg
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to render mermaid diagram.'
      block.innerHTML = `<pre class="mermaid-error">${message}</pre>`
    }
  }
}
