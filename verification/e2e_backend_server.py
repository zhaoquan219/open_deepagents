from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app import agent as runtime_agent  # noqa: E402
from app.main import create_app  # noqa: E402

THREAD_PROMPTS: dict[str, list[str]] = {}


class BrowserE2EFakeGraph:
    async def astream_events(
        self,
        agent_input: Any,
        *,
        config: Any,
        context: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        thread_id = str(config.get("configurable", {}).get("thread_id") or context.thread_id)
        prompt = _latest_prompt(agent_input)
        prior_prompts = THREAD_PROMPTS.setdefault(thread_id, [])
        attachments = list(
            getattr(context, "current_attachments", ())
            or getattr(context, "session_attachments", ())
            or ()
        )
        upload_path = str(attachments[0].get("path") if attachments else "")
        if prior_prompts:
            text = f"Browser E2E prior prompt: {prior_prompts[-1]}"
        else:
            text = f"Browser E2E read upload BROWSER_UPLOAD_MARKER at {upload_path}"
        yield {
            "event": "on_chain_start",
            "name": "deepagents-web",
            "metadata": {"langgraph_node": "deepagents-web"},
            "data": {"input": agent_input},
        }
        if upload_path:
            yield {
                "event": "on_tool_start",
                "name": "read_file",
                "data": {"input": {"path": upload_path}},
            }
            yield {
                "event": "on_tool_end",
                "name": "read_file",
                "data": {"output": {"content": "BROWSER_UPLOAD_MARKER"}},
            }
        yield {
            "event": "on_chat_model_stream",
            "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"chunk": {"content": text}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"content": text}},
        }
        yield {
            "event": "on_chain_end",
            "name": "deepagents-web",
            "metadata": {"langgraph_node": "deepagents-web"},
            "data": {"output": {"messages": [{"role": "assistant", "content": text}]}},
        }
        prior_prompts.append(prompt)


def _latest_prompt(agent_input: Any) -> str:
    messages = agent_input.get("messages") if isinstance(agent_input, dict) else None
    if not isinstance(messages, list) or not messages:
        return ""
    message = messages[-1]
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    runtime_agent.build_deep_agent = lambda settings, model_id=None: BrowserE2EFakeGraph()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
