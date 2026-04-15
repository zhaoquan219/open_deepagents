from __future__ import annotations

from typing import Any


def inject_attachment_brief(context: Any) -> str | None:
    """Example run-input hook for attachment prompt shaping.

    Customize this function when your product wants a different upload
    instruction style than the built-in default. Returning None leaves the
    current content unchanged.
    """

    if context.role != "user" or not context.attachments:
        return None

    lines = [context.content, "", "Project attachment context:"]
    for attachment in context.attachments:
        preferred_path = attachment.get("sandbox_path") or attachment.get("upload_path")
        name = attachment.get("name") or attachment.get("id") or "attachment"
        path_hint = preferred_path or attachment.get("storage_key") or "path unavailable"
        lines.append(f"- {name}: {path_hint}")
    lines.append("Use the provided path when the task needs file access.")
    return "\n".join(lines)


def tag_uploaded_file(context: Any) -> dict[str, Any]:
    """Example upload hook for adding searchable metadata to UploadRecord.extra."""

    return {
        "runtime_hook": "extensions.runtime_hooks.tag_uploaded_file",
        "original_filename": context.filename,
        "upload_path": context.upload_path,
    }


RUN_INPUT_HOOKS = [inject_attachment_brief]
UPLOAD_HOOKS = [tag_uploaded_file]
