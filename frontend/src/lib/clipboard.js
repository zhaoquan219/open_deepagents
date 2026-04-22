export async function copyText(text) {
  const value = String(text || '')
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return 'clipboard'
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
  return 'fallback'
}

export async function copyBlob(blob, mimeType) {
  const ClipboardItemCtor = globalThis.ClipboardItem
  if (navigator.clipboard?.write && typeof ClipboardItemCtor !== 'undefined') {
    try {
      if (mimeType === 'image/png') {
        await copyPngBlobWithClipboard(blob, ClipboardItemCtor)
      } else {
        await navigator.clipboard.write([new ClipboardItemCtor({ [mimeType]: blob })])
      }
      return 'clipboard'
    } catch (error) {
      if (mimeType !== 'image/png') {
        throw error
      }
    }
  }
  if (mimeType === 'image/png') {
    return copyPngBlobWithExecCommand(blob)
  }
  throw new Error('Image clipboard is not supported.')
}

export async function copyRenderedSvgAsPng(svgElement) {
  const pngBlob = await svgToPngBlob(svgElement)
  await copyBlob(pngBlob, 'image/png')
  return 'png'
}

export async function svgToPngBlob(svgElement) {
  if (!svgElement) {
    throw new Error('No rendered diagram is available.')
  }
  const { source, width, height } = serializeSvgForImage(svgElement)
  const image = await loadImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`)
  const scale = Math.max(1, Math.min(2, globalThis.devicePixelRatio || 1))
  const canvas = document.createElement('canvas')
  canvas.width = Math.ceil(width * scale)
  canvas.height = Math.ceil(height * scale)
  const context = canvas.getContext('2d')
  if (!context) {
    throw new Error('Canvas rendering is not supported.')
  }
  context.setTransform(scale, 0, 0, scale, 0, 0)
  context.fillStyle = '#ffffff'
  context.fillRect(0, 0, width, height)
  context.drawImage(image, 0, 0, width, height)
  return canvasToBlob(canvas)
}

function serializeSvgForImage(svgElement) {
  const clone = svgElement.cloneNode(true)
  const bounds = svgElement.getBoundingClientRect()
  const viewBox = svgElement.viewBox?.baseVal
  const width = Math.max(1, Math.ceil(viewBox?.width || bounds.width || Number(svgElement.getAttribute('width')) || 1))
  const height = Math.max(1, Math.ceil(viewBox?.height || bounds.height || Number(svgElement.getAttribute('height')) || 1))
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  if (!clone.getAttribute('viewBox')) {
    clone.setAttribute('viewBox', `0 0 ${width} ${height}`)
  }
  clone.setAttribute('style', `${clone.getAttribute('style') || ''};background:#fff;`)
  return {
    source: new globalThis.XMLSerializer().serializeToString(clone),
    width,
    height,
  }
}

async function copyPngBlobWithExecCommand(blob) {
  const dataUrl = await blobToDataUrl(blob)
  const container = document.createElement('div')
  container.contentEditable = 'true'
  container.style.position = 'fixed'
  container.style.left = '-9999px'
  container.style.top = '0'
  const image = document.createElement('img')
  image.src = dataUrl
  container.appendChild(image)
  document.body.appendChild(container)
  const selection = globalThis.getSelection?.()
  const range = document.createRange()
  range.selectNode(image)
  selection?.removeAllRanges()
  selection?.addRange(range)
  const copied = document.execCommand?.('copy')
  selection?.removeAllRanges()
  document.body.removeChild(container)
  if (!copied) {
    throw new Error('Image clipboard is not supported.')
  }
  return 'fallback'
}

async function copyPngBlobWithClipboard(blob, ClipboardItemCtor) {
  if (typeof ClipboardItemCtor.supports === 'function' && !ClipboardItemCtor.supports('image/png')) {
    throw new Error('PNG clipboard is not supported.')
  }

  const pngBlob = blob.type === 'image/png' ? blob : blob.slice(0, blob.size, 'image/png')
  try {
    const dataUrl = await blobToDataUrl(pngBlob)
    const htmlBlob = new globalThis.Blob([`<img alt="" src="${dataUrl}">`], { type: 'text/html' })
    await navigator.clipboard.write([
      new ClipboardItemCtor({
        'image/png': pngBlob,
        'text/html': htmlBlob,
      }),
    ])
  } catch {
    await navigator.clipboard.write([new ClipboardItemCtor({ 'image/png': pngBlob })])
  }
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new globalThis.FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('Unable to prepare PNG image.'))
    reader.readAsDataURL(blob)
  })
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new globalThis.Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('Unable to load rendered SVG.'))
    image.src = url
  })
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('Unable to convert diagram to PNG.'))
        return
      }
      resolve(blob)
    }, 'image/png')
  })
}
