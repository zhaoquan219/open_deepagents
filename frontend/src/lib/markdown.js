import mermaid from 'mermaid'
import { marked } from 'marked'

const DISALLOWED_TAGS = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta'])

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  theme: 'neutral',
})

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
      if (name.startsWith('on')) {
        element.removeAttribute(attribute.name)
        continue
      }
      if ((name === 'href' || name === 'src') && value.startsWith('javascript:')) {
        element.removeAttribute(attribute.name)
        continue
      }
      if (name.startsWith('data-')) {
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

export function renderMarkdownFragmentToHtml(markdown) {
  const rendered = marked.parse(String(markdown || ''), { breaks: true, gfm: true })
  return sanitizeHtml(rendered)
}

export async function renderMermaidSvg(id, source) {
  const result = await mermaid.render(id, source)
  return typeof result === 'string' ? result : result.svg
}
