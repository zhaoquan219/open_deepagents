import { uiCopy } from "./copy.js";
import { parseMarkdownSegments } from "./markdownSegments.js";

const PROCESS_TYPES = new Set(["tool", "subagent", "sandbox"]);
const INTERMEDIATE_KINDS = new Set(["thinking", "reasoning", "tool", "subagent", "sandbox"]);
const INTERNAL_LABELS = new Set([
  "runtime.event",
  "step.started",
  "step.completed",
]);
const LOW_SIGNAL_PROCESS_LABELS = new Set([
  "tool.started",
  "subagent.started",
  "sandbox.started",
]);

export function showInternalLogs() {
  return import.meta.env.VITE_DEEPAGENTS_SHOW_INTERNAL_EVENTS === "true";
}

function text(value) {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => text(item))
      .filter(Boolean)
      .join("\n");
  }
  if (typeof value === "object") {
    for (const key of [
      "content",
      "text",
      "result",
      "stdout",
      "stderr",
      "output",
    ]) {
      if (key in value) {
        const result = text(value[key]);
        if (result) {
          return result;
        }
      }
    }
    const compact = Object.fromEntries(
      Object.entries(value)
        .filter(
          ([, child]) => child !== undefined && child !== null && child !== "",
        )
        .slice(0, 6),
    );
    return Object.keys(compact).length ? JSON.stringify(compact, null, 2) : "";
  }
  return String(value);
}

function titleFor(envelope) {
  const data =
    envelope.data && typeof envelope.data === "object" ? envelope.data : {};
  const input = data.input && typeof data.input === "object" ? data.input : {};
  if (envelope.type === "tool") {
    return (
      data.tool_name ||
      data.name ||
      envelope.detail ||
      uiCopy.processLog.kinds.tool
    );
  }
  if (envelope.type === "skill") {
    const count = skillList(data).length;
    return count > 0
      ? uiCopy.processLog.loadedSkills(count)
      : envelope.detail || uiCopy.processLog.kinds.skill;
  }
  if (envelope.type === "subagent") {
    return (
      input.subagent_type ||
      input.subagent ||
      input.agent ||
      data.subagent_type ||
      envelope.detail ||
      uiCopy.processLog.kinds.subagent
    );
  }
  if (envelope.type === "sandbox") {
    return (
      input.command ||
      input.cmd ||
      data.command ||
      envelope.detail ||
      uiCopy.processLog.kinds.sandbox
    );
  }
  if (envelope.type === "error") {
    return uiCopy.processLog.kinds.error;
  }
  return envelope.detail || envelope.label || uiCopy.processLog.kinds.step;
}

function skillList(data) {
  if (Array.isArray(data.skills)) {
    return data.skills;
  }
  if (Array.isArray(data.output?.skills_metadata)) {
    return data.output.skills_metadata;
  }
  if (Array.isArray(data.skills_metadata)) {
    return data.skills_metadata;
  }
  return [];
}

function summaryFor(envelope) {
  const data =
    envelope.data && typeof envelope.data === "object" ? envelope.data : {};
  if (envelope.type === "skill") {
    const skills = skillList(data);
    if (Array.isArray(skills) && skills.length > 0) {
      return skills
        .map((skill) =>
          [skill?.name, skill?.description].filter(Boolean).join(": "),
        )
        .filter(Boolean)
        .join("\n");
    }
  }
  if (envelope.type === "sandbox") {
    const command = text(
      data.input?.command ?? data.input?.cmd ?? data.command,
    ).trim();
    const output = text(data.output ?? data.result).trim();
    return [
      command ? `${uiCopy.processLog.input}: ${command}` : "",
      output ? `${uiCopy.processLog.output}: ${output}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
  const input = text(
    data.input?.description ??
      data.input?.prompt ??
      data.input?.task ??
      data.input,
  ).trim();
  const output = text(data.output ?? data.result).trim();
  const parts = [
    input ? `${uiCopy.processLog.input}: ${input}` : "",
    output ? `${uiCopy.processLog.output}: ${output}` : "",
  ].filter(Boolean);
  return parts.join("\n") || text(envelope.detail).trim();
}

export function logEntryFromEnvelope(envelope) {
  if (!envelope) {
    return null;
  }
  if (
    !showInternalLogs() &&
    LOW_SIGNAL_PROCESS_LABELS.has(envelope.label)
  ) {
    return null;
  }
  if (!PROCESS_TYPES.has(envelope.type) && envelope.type !== "error") {
    if (!showInternalLogs() || INTERNAL_LABELS.has(envelope.label)) {
      return null;
    }
  }
  const entry = {
    id:
      envelope.eventId ||
      `${envelope.runId}:${envelope.type}:${envelope.timestamp}`,
    kind: PROCESS_TYPES.has(envelope.type)
      ? envelope.type
      : envelope.type === "error"
        ? "error"
        : "step",
    title: String(titleFor(envelope) || uiCopy.processLog.kinds.step),
    summary: summaryFor(envelope),
    status:
      envelope.status || (envelope.type === "error" ? "failed" : "in_progress"),
    timestamp: envelope.timestamp,
  };
  return processEntryHasContent(entry) ? entry : null;
}

export function thinkingEntriesFromContent(content, startedAt) {
  return parseMarkdownSegments(content)
    .filter((segment) => segment.type === "thinking")
    .filter((segment) => String(segment.content || "").trim())
    .map((segment, index) => ({
      id: `thinking-${index}-${segment.key}`,
      kind: segment.kind === "reasoning" ? "reasoning" : "thinking",
      title:
        segment.kind === "reasoning"
          ? uiCopy.common.reasoning
          : uiCopy.common.thinking,
      summary: segment.content,
      status: "completed",
      timestamp: startedAt,
    }));
}

export function visibleAssistantContent(content) {
  return parseMarkdownSegments(content)
    .filter((segment) => segment.type !== "thinking")
    .map((segment) =>
      segment.type === "mermaid"
        ? `\`\`\`mermaid\n${segment.source}\n\`\`\``
        : segment.content,
    )
    .join("\n\n")
    .trim();
}

function processEntryHasContent(entry) {
  return Boolean(
    String(entry?.title || "").trim() || String(entry?.summary || "").trim(),
  );
}

function aggregateStatus(entries) {
  if (entries.some((entry) => entry.status === "failed")) {
    return "failed";
  }
  if (entries.some((entry) => entry.status === "cancelled")) {
    return "cancelled";
  }
  if (
    entries.some(
      (entry) => !["completed", "done"].includes(String(entry.status || "")),
    )
  ) {
    return "in_progress";
  }
  return "completed";
}

export function groupProcessLogs(entries) {
  const ordered = entries.filter(processEntryHasContent);

  if (ordered.length === 0) {
    return [];
  }

  const groups = [];
  let currentGroup = null;

  function completeGroup() {
    if (!currentGroup) {
      return;
    }
    currentGroup.status = aggregateStatus(currentGroup.items);
    groups.push(currentGroup);
    currentGroup = null;
  }

  function startGroup(entry) {
    const kind = ["thinking", "reasoning"].includes(entry.kind)
      ? "thinking"
      : entry.kind;
    const title = kind === "thinking" ? uiCopy.common.thinking : entry.title;
    currentGroup = {
      id: `group:${entry.id}`,
      kind,
      title,
      status: aggregateStatus([entry]),
      startedAt: entry.timestamp,
      endedAt: entry.timestamp,
      items: [entry],
    };
  }

  function trackEntry(entry) {
    if (!currentGroup) {
      startGroup(entry);
      return;
    }

    const entryIsIntermediate = INTERMEDIATE_KINDS.has(entry.kind);
    const groupIsIntermediate =
      INTERMEDIATE_KINDS.has(currentGroup.kind) ||
      currentGroup.kind === "process";

    if (entryIsIntermediate && groupIsIntermediate) {
      if (currentGroup.kind !== "process") {
        currentGroup.kind = "process";
        currentGroup.title = uiCopy.messageThread.process.intermediateTitle;
      }
      currentGroup.items.push(entry);
      currentGroup.endedAt = entry.timestamp;
      return;
    }

    completeGroup();
    startGroup(entry);
  }

  for (const entry of ordered) {
    trackEntry(entry);
  }
  completeGroup();

  return groups;
}
