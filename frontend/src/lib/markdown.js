import { marked } from "marked";

const ALLOWED_TAGS = new Set([
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "sub",
  "sup",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
]);
const DANGEROUS_CONTENT_TAGS = new Set([
  "embed",
  "iframe",
  "link",
  "math",
  "meta",
  "object",
  "script",
  "style",
  "svg",
]);
const VOID_TAGS = new Set(["br", "hr"]);
const ALLOWED_ATTRIBUTES = {
  a: new Set(["href", "title"]),
  ol: new Set(["start"]),
  td: new Set(["colspan", "rowspan"]),
  th: new Set(["colspan", "rowspan"]),
};
/** @type {Promise<import('mermaid').Mermaid> | undefined} */
let mermaidModulePromise;

function sanitizeHtml(html) {
  if (typeof DOMParser === "undefined") {
    return sanitizeHtmlFallback(html);
  }
  const parser = new DOMParser();
  const documentNode = parser.parseFromString(html, "text/html");

  for (const element of documentNode.body.querySelectorAll("*")) {
    const tagName = element.tagName.toLowerCase();
    if (DANGEROUS_CONTENT_TAGS.has(tagName)) {
      element.remove();
      continue;
    }
    if (!ALLOWED_TAGS.has(tagName)) {
      element.replaceWith(...element.childNodes);
      continue;
    }

    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (!isAllowedAttribute(tagName, name)) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (name === "href" && !isSafeUrl(attribute.value)) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (!isSafeNumericAttribute(name, attribute.value)) {
        element.removeAttribute(attribute.name);
      }
    }

    if (tagName === "a" && element.getAttribute("href")) {
      element.setAttribute("target", "_blank");
      element.setAttribute("rel", "noreferrer noopener");
    }
  }

  return documentNode.body.innerHTML;
}

function sanitizeHtmlFallback(html) {
  const stripped = stripDangerousContent(String(html || ""));
  return stripped.replace(
    /<\/?([A-Za-z][A-Za-z0-9:-]*)([^>]*)>/g,
    (match, rawTagName, rawAttributes) => {
      const tagName = String(rawTagName || "").toLowerCase();
      const isClosing = /^<\//.test(match);
      if (!ALLOWED_TAGS.has(tagName)) {
        return "";
      }
      if (isClosing) {
        return VOID_TAGS.has(tagName) ? "" : `</${tagName}>`;
      }
      const attributes = sanitizeAttributes(tagName, String(rawAttributes || ""));
      return `<${tagName}${attributes}>`;
    },
  );
}

function stripDangerousContent(html) {
  let next = String(html || "");
  for (const tagName of DANGEROUS_CONTENT_TAGS) {
    next = next.replace(
      new RegExp(`<${tagName}\\b[^>]*>[\\s\\S]*?<\\/${tagName}\\s*>`, "gi"),
      "",
    );
    next = next.replace(new RegExp(`<${tagName}\\b[^>]*\\/?>`, "gi"), "");
  }
  return next;
}

function sanitizeAttributes(tagName, rawAttributes) {
  const allowed = ALLOWED_ATTRIBUTES[tagName] || new Set();
  const attributes = [];
  const matcher =
    /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let match = matcher.exec(rawAttributes);
  while (match) {
    const name = String(match[1] || "").toLowerCase();
    const value = String(match[2] ?? match[3] ?? match[4] ?? "");
    if (
      allowed.has(name) &&
      (name !== "href" || isSafeUrl(value)) &&
      isSafeNumericAttribute(name, value)
    ) {
      attributes.push(`${name}="${escapeAttribute(value)}"`);
    }
    match = matcher.exec(rawAttributes);
  }
  if (tagName === "a" && attributes.some((value) => value.startsWith("href="))) {
    attributes.push('target="_blank"', 'rel="noreferrer noopener"');
  }
  return attributes.length ? ` ${attributes.join(" ")}` : "";
}

function isAllowedAttribute(tagName, name) {
  return Boolean(ALLOWED_ATTRIBUTES[tagName]?.has(name));
}

function isSafeNumericAttribute(name, value) {
  if (!["colspan", "rowspan", "start"].includes(name)) {
    return true;
  }
  return /^[1-9][0-9]{0,2}$/.test(String(value).trim());
}

function isSafeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return false;
  }
  if (/^(#|\/(?!\/)|\.{1,2}\/)/.test(raw)) {
    return true;
  }
  try {
    return ["http:", "https:", "mailto:", "tel:"].includes(
      new URL(raw, "https://deepagents.local").protocol,
    );
  } catch {
    return false;
  }
}

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function renderMarkdownFragmentToHtml(markdown) {
  const rendered = marked.parse(String(markdown || ""), {
    breaks: true,
    gfm: true,
  });
  return sanitizeHtml(rendered);
}

export async function renderMermaidSvg(id, source) {
  const mermaid = await loadMermaid();
  const result = await mermaid.render(id, source);
  return typeof result === "string" ? result : result.svg;
}

async function loadMermaid() {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import("mermaid").then((module) => {
      const mermaid = module.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "neutral",
      });
      return mermaid;
    });
  }
  return mermaidModulePromise;
}
