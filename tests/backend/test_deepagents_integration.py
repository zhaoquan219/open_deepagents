from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import textwrap
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.backends import FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.skills import _list_skills

from deepagents_integration import (
    BuiltinToolSelectionMiddleware,
    DeepAgentsRunContext,
    DeepAgentsRuntimeConfig,
    RunInputHookContext,
    SandboxConfig,
    SkillSourceConfig,
    apply_run_input_hooks,
    apply_upload_hooks,
    build_deep_agent,
    build_upload_hook_context,
    load_middleware_extensions,
    load_object_from_spec,
    load_tool_extensions,
    normalize_runtime_event,
    resolve_backend,
    route_skill_sources,
    stream_sse_envelopes,
    validate_sse_event,
)


class DummyBackend(BackendProtocol):
    pass


class AsyncEventRuntime:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, agent_input, *, version="v2", config=None):
        for event in self._events:
            await asyncio.sleep(0)
            yield event


class DeepAgentsConfigTests(unittest.TestCase):
    def test_runtime_config_validation(self):
        config = DeepAgentsRuntimeConfig.from_mapping(
            {
                "model": "openai:gpt-5.4",
                "tool_specs": ["pkg.tools:SEARCH_TOOL"],
                "middleware_specs": ["pkg.middleware:AUDIT_MIDDLEWARE"],
                "skills": ["/skills/project"],
                "memory": ["/memory/AGENTS.md"],
                "permissions": [{"operations": ["read"], "paths": ["/workspace"]}],
                "sandbox": {"kind": "filesystem", "root_dir": "/tmp/workspace"},
            }
        )
        self.assertEqual(config.model, "openai:gpt-5.4")
        self.assertEqual(config.sandbox.kind, "filesystem")
        self.assertEqual(config.skills, ("/skills/project",))

        with self.assertRaises(ValueError):
            DeepAgentsRuntimeConfig.from_mapping({"tool_specs": [123]})

    def test_tool_and_middleware_specs_load_from_python_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "extensions.py"
            module_path.write_text(
                textwrap.dedent(
                    """
                    def sample_tool(query: str) -> str:
                        return query.upper()

                    class DemoMiddleware:
                        pass

                    TOOLS = [sample_tool]
                    MIDDLEWARE = [DemoMiddleware()]
                    """
                )
            )
            tools = load_tool_extensions((f"{module_path}:TOOLS",))
            middleware = load_middleware_extensions((f"{module_path}:MIDDLEWARE",))

            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]("hello"), "HELLO")
            self.assertEqual(len(middleware), 1)
            self.assertEqual(type(middleware[0]).__name__, "DemoMiddleware")

    def test_tool_and_middleware_specs_load_from_unified_init_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tools_dir = root / "extensions" / "tools"
            middleware_dir = root / "extensions" / "middleware"
            tools_dir.mkdir(parents=True)
            middleware_dir.mkdir(parents=True)

            (tools_dir / "echo_tool.py").write_text(
                textwrap.dedent(
                    """
                    def sample_tool(query: str) -> str:
                        return query.upper()

                    TOOLS = [sample_tool]
                    """
                )
            )
            (tools_dir / "__init__.py").write_text(
                "from extensions.tools.echo_tool import TOOLS as SAMPLE_TOOLS\n"
                "TOOLS = [*SAMPLE_TOOLS]\n"
            )
            (middleware_dir / "audit_middleware.py").write_text(
                textwrap.dedent(
                    """
                    class DemoMiddleware:
                        pass

                    MIDDLEWARE = [DemoMiddleware()]
                    """
                )
            )
            (middleware_dir / "__init__.py").write_text(
                "from extensions.middleware.audit_middleware import "
                "MIDDLEWARE as SAMPLE_MIDDLEWARE\n"
                "MIDDLEWARE = [*SAMPLE_MIDDLEWARE]\n"
            )

            saved_modules = {
                name: sys.modules.pop(name, None)
                for name in (
                    "extensions",
                    "extensions.tools",
                    "extensions.tools.echo_tool",
                    "extensions.middleware",
                    "extensions.middleware.audit_middleware",
                )
            }
            sys.path.insert(0, tmpdir)
            try:
                tools = load_tool_extensions((f"{tools_dir / '__init__.py'}:TOOLS",))
                middleware = load_middleware_extensions(
                    (f"{middleware_dir / '__init__.py'}:MIDDLEWARE",)
                )
            finally:
                sys.path.pop(0)
                for name, module in saved_modules.items():
                    if module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module

            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]("hello"), "HELLO")
            self.assertEqual(len(middleware), 1)
            self.assertEqual(type(middleware[0]).__name__, "DemoMiddleware")

    def test_runtime_hook_template_entrypoints_are_loadable(self):
        run_hooks = load_object_from_spec(
            "extensions.runtime_hooks:RUN_INPUT_HOOKS"
        )
        upload_hooks = load_object_from_spec("extensions.runtime_hooks:UPLOAD_HOOKS")

        self.assertTrue(run_hooks)
        self.assertTrue(upload_hooks)
        self.assertTrue(callable(run_hooks[0]))
        self.assertTrue(callable(upload_hooks[0]))

    def test_upload_hook_context_is_metadata_only(self):
        context = build_upload_hook_context(
            upload_id="upload-1",
            session_id="session-1",
            message_id=None,
            filename="archive.bin",
            content_type="application/octet-stream",
            size_bytes=4096,
            storage_key="session/archive.bin",
            sha256="abc123",
            upload_root=Path("/tmp/uploads"),
        )

        self.assertFalse(hasattr(context, "payload"))
        self.assertEqual(context.size_bytes, 4096)
        expected_path = str(Path("/tmp/uploads/session/archive.bin").resolve())
        self.assertEqual(context.upload_path, expected_path)

        def hook(hook_context):
            self.assertFalse(hasattr(hook_context, "payload"))
            return {"seen_size": hook_context.size_bytes}

        self.assertEqual(
            apply_upload_hooks(context=context, hook_specs=()),
            {},
        )
        with patch("deepagents_integration.run_hooks._load_hooks", return_value=(hook,)):
            self.assertEqual(
                apply_upload_hooks(context=context, hook_specs=("pkg:HOOK",)),
                {"seen_size": 4096},
            )

    def test_route_skill_sources_loads_disk_skills_with_state_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir) / "skills"
            skill_dir = skill_root / "web-research"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                textwrap.dedent(
                    """\
                    ---
                    name: web-research
                    description: Structured research workflow
                    ---

                    # Web Research
                    """
                ),
                encoding="utf-8",
            )

            backend, active_sources = route_skill_sources(
                StateBackend(),
                (
                    SkillSourceConfig(
                        source_path="/extensions/skills/",
                        disk_path=str(skill_root),
                    ),
                ),
            )

            self.assertEqual(active_sources, ("/extensions/skills/",))
            skills = _list_skills(backend, "/extensions/skills/")
            self.assertEqual(
                skills,
                [
                    {
                        "name": "web-research",
                        "description": "Structured research workflow",
                        "path": "/extensions/skills/web-research/SKILL.md",
                        "metadata": {},
                        "license": None,
                        "compatibility": None,
                        "allowed_tools": [],
                    }
                ],
            )

    def test_backend_resolution_supports_builtin_and_custom_backends(self):
        self.assertIsInstance(resolve_backend(SandboxConfig(kind="state")), StateBackend)
        self.assertIsInstance(
            resolve_backend(SandboxConfig(kind="filesystem", root_dir="/tmp/workspace")),
            FilesystemBackend,
        )
        self.assertIsInstance(resolve_backend(SandboxConfig(kind="local_shell")), LocalShellBackend)

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "custom_backend.py"
            module_path.write_text(
                textwrap.dedent(
                    """
                    from deepagents.backends.protocol import BackendProtocol

                    class DemoBackend(BackendProtocol):
                        pass

                    def build_backend():
                        return DemoBackend()

                    def backend_factory(runtime):
                        return DemoBackend()
                    """
                )
            )
            backend = resolve_backend(
                SandboxConfig(kind="custom", backend_spec=f"{module_path}:build_backend")
            )
            self.assertEqual(type(backend).__name__, "DemoBackend")

            backend_factory = resolve_backend(
                SandboxConfig(kind="custom", backend_spec=f"{module_path}:backend_factory")
            )
            self.assertTrue(callable(backend_factory))

    def test_build_deep_agent_wires_config_into_create_deep_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "extensions.py"
            module_path.write_text(
                textwrap.dedent(
                    """
                    def sample_tool(query: str) -> str:
                        return query.upper()

                    class DemoMiddleware:
                        pass

                    TOOL = sample_tool
                    MIDDLEWARE = DemoMiddleware()
                    """
                )
            )
            config = DeepAgentsRuntimeConfig.from_mapping(
                {
                    "model": "openai:gpt-5.4",
                    "system_prompt": "Use the official DeepAgents runtime.",
                    "agent_name": "deepagents-web",
                    "tool_specs": [f"{module_path}:TOOL"],
                    "middleware_specs": [f"{module_path}:MIDDLEWARE"],
                    "skills": ["/skills/project", "/skills/user"],
                    "memory": ["/memory/AGENTS.md"],
                    "permissions": [{"operations": ["read", "write"], "paths": ["/workspace"]}],
                    "sandbox": {"kind": "filesystem", "root_dir": "/workspace"},
                }
            )

            with patch("deepagents_integration.agent_factory.create_deep_agent") as mocked_create:
                mocked_create.return_value = object()
                result = build_deep_agent(config)

            self.assertIs(result, mocked_create.return_value)
            _, kwargs = mocked_create.call_args
            self.assertEqual(kwargs["model"], "openai:gpt-5.4")
            self.assertEqual(kwargs["system_prompt"], "Use the official DeepAgents runtime.")
            self.assertEqual(kwargs["name"], "deepagents-web")
            self.assertEqual(len(kwargs["tools"]), 1)
            self.assertEqual(kwargs["tools"][0]("ok"), "OK")
            self.assertEqual(len(kwargs["middleware"]), 1)
            self.assertEqual(kwargs["skills"], ["/skills/project", "/skills/user"])
            self.assertEqual(kwargs["memory"], ["/memory/AGENTS.md"])
            self.assertEqual(kwargs["permissions"][0].operations, ["read", "write"])
            self.assertIsInstance(kwargs["backend"], FilesystemBackend)
            self.assertIs(kwargs["context_schema"], DeepAgentsRunContext)

    def test_builtin_tool_selection_middleware_filters_only_deepagents_builtin_tools(self):
        middleware = BuiltinToolSelectionMiddleware(
            allowlist=frozenset({"ls"}),
            blocklist=frozenset({"execute"}),
        )

        filtered = middleware._filter_tools(
            [
                {"name": "ls"},
                {"name": "read_file"},
                {"name": "execute"},
                {"name": "custom_tool"},
            ]
        )

        self.assertEqual(filtered, [{"name": "ls"}, {"name": "custom_tool"}])

    def test_build_deep_agent_adds_builtin_tool_selection_middleware(self):
        config = DeepAgentsRuntimeConfig.from_mapping(
            {
                "model": "openai:gpt-5.4",
                "builtin_tool_allowlist": ["ls", "read_file"],
                "builtin_tool_blocklist": ["execute"],
            }
        )

        with patch("deepagents_integration.agent_factory.create_deep_agent") as mocked_create:
            mocked_create.return_value = object()
            build_deep_agent(config)

        _, kwargs = mocked_create.call_args
        self.assertTrue(
            any(
                isinstance(middleware, BuiltinToolSelectionMiddleware)
                for middleware in kwargs["middleware"]
            )
        )

    def test_functional_audit_middleware_can_access_runtime_context(self):
        before_agent_middleware, tool_middleware = load_middleware_extensions(
            ("extensions.middleware.audit_middleware:MIDDLEWARE",)
        )
        runtime = SimpleNamespace(
            context={
                "session_id": "session-1",
                "run_id": "run-1",
                "current_attachments": ({"id": "upload-1"},),
            }
        )
        request = SimpleNamespace(
            runtime=runtime,
            tool_call={"name": "read_file"},
        )

        def sync_handler(received_request):
            self.assertIs(received_request, request)
            return "sync-result"

        self.assertEqual(before_agent_middleware.before_agent({}, runtime), None)
        self.assertEqual(tool_middleware.wrap_tool_call(request, sync_handler), "sync-result")

    def test_blank_run_input_hook_specs_leave_upload_prompt_unchanged(self):
        content = apply_run_input_hooks(
            context=RunInputHookContext(
                session_id="session-1",
                run_id="run-1",
                role="user",
                content="Read the file",
                attachments=({"name": "notes.txt", "upload_path": "/tmp/notes.txt"},),
                is_current_run=True,
            ),
            hook_specs=(),
        )

        self.assertEqual(content, "Read the file")


class DeepAgentsSseBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_bridge_emits_versioned_monotonic_events(self):
        runtime = AsyncEventRuntime(
            [
                {
                    "event": "on_chain_start",
                    "name": "deep-agent",
                    "run_id": "runtime-1",
                    "metadata": {"langgraph_node": "agent"},
                    "data": {"input": {"messages": [{"role": "user", "content": "hello"}]}},
                },
                {
                    "event": "on_chat_model_stream",
                    "name": "model",
                    "run_id": "runtime-1",
                    "metadata": {"langgraph_node": "model"},
                    "data": {"chunk": {"content": [{"text": "Hel"}, {"text": "lo"}]}} ,
                },
                {
                    "event": "on_tool_start",
                    "name": "execute",
                    "run_id": "runtime-1",
                    "data": {"input": {"command": "ls"}},
                },
                {
                    "event": "on_tool_end",
                    "name": "task",
                    "run_id": "runtime-1",
                    "data": {"output": {"result": "done"}},
                },
                {
                    "event": "on_chat_model_end",
                    "name": "model",
                    "run_id": "runtime-1",
                    "metadata": {"langgraph_node": "model"},
                    "data": {"output": {"messages": [{"content": "Hello world"}]}},
                },
                {
                    "event": "on_chain_end",
                    "name": "deep-agent",
                    "run_id": "runtime-1",
                    "metadata": {"langgraph_node": "agent"},
                    "data": {"output": {"messages": [{"content": "Hello world"}]}},
                },
            ]
        )

        envelopes = [
            envelope
            async for envelope in stream_sse_envelopes(
                runtime,
                {"messages": []},
                bridge_run_id="app-run-7",
            )
        ]

        self.assertEqual(envelopes[0].event, "bridge.hello")
        self.assertEqual(envelopes[0].event_id, "app-run-7:000001")
        self.assertEqual(envelopes[1].event, "run.started")
        self.assertEqual(envelopes[2].event, "message.delta")
        self.assertEqual(envelopes[2].data["text"], "Hello")
        self.assertFalse(envelopes[2].data["canonical_transcript"])
        self.assertEqual(envelopes[3].event, "sandbox.started")
        self.assertEqual(envelopes[4].event, "skill.completed")
        self.assertEqual(envelopes[5].event, "message.completed")
        self.assertTrue(envelopes[5].data["canonical_transcript"])
        self.assertEqual(envelopes[6].event, "run.completed")

        for sequence, envelope in enumerate(envelopes, start=1):
            self.assertEqual(envelope.sequence, sequence)
            validate_sse_event(asdict(envelope))
            self.assertIn(f"app-run-7:{sequence:06d}", envelope.to_sse())

    def test_runtime_event_normalization_falls_back_to_progress(self):
        envelope = normalize_runtime_event(
            {"event": "on_custom_event", "name": "progress-writer", "data": {"pct": 50}},
            bridge_run_id="run-1",
            sequence=2,
        )
        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertEqual(envelope.event, "run.progress")
        self.assertEqual(envelope.data["payload"], {"pct": 50})

    def test_runtime_event_normalization_redacts_binary_payloads(self):
        raw = b"\xff" * 512
        encoded = base64.b64encode(raw).decode("ascii")
        envelope = normalize_runtime_event(
            {
                "event": "on_tool_end",
                "name": "execute",
                "run_id": "runtime-1",
                "data": {"output": {"raw": raw, "encoded": encoded}},
            },
            bridge_run_id="run-1",
            sequence=2,
        )

        self.assertIsNotNone(envelope)
        assert envelope is not None
        output = envelope.data["output"]
        self.assertEqual(output["raw"]["omitted"], "binary")
        self.assertEqual(output["raw"]["size_bytes"], 512)
        self.assertTrue(output["encoded"].startswith("[redacted base64-like runtime string"))
        self.assertNotIn(encoded, json.dumps(envelope.data))

    def test_validator_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            validate_sse_event(
                {
                    "schema_version": "wrong-version",
                    "event_id": "run:1",
                    "event": "message.delta",
                    "run_id": "run",
                    "sequence": 0,
                    "data": {},
                }
            )


if __name__ == "__main__":
    unittest.main()
