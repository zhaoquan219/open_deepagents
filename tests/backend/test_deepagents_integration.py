from __future__ import annotations

from unittest.mock import patch

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend, StateBackend
from deepagents.middleware.permissions import _check_fs_permission
from deepagents.middleware.skills import _list_skills

from app.agent import DeepAgentsRunContext, build_deep_agent
from app.runtime.extensions import (
    BUILTIN_TOOL_ORDER,
    BuiltinToolSelectionMiddleware,
    SandboxConfig,
    build_permissions,
    builtin_tool_allowlist_from_permissions,
    load_object_from_spec,
    resolve_backend,
)
from app.runtime.sse_bridge import normalize_runtime_event
from app.settings import Settings


def test_runtime_normalizes_current_event_stream_contract() -> None:
    envelope = normalize_runtime_event(
        {
            "event": "on_tool_end",
            "name": "execute",
            "data": {"output": {"stdout": "PROCESS_LOG_OK\n"}},
        },
        bridge_run_id="run-1",
        sequence=10,
    )

    assert envelope is not None
    assert envelope.type == "sandbox"
    assert envelope.label == "sandbox.completed"
    assert envelope.data["tool_name"] == "execute"


def test_runtime_normalizes_skill_and_final_message_events() -> None:
    skill_event = normalize_runtime_event(
        {
            "event": "on_chain_end",
            "name": "SkillsMiddleware.before_agent",
            "data": {
                "output": {
                    "skills_metadata": [
                        {"name": "skill-creator", "path": "/skills/skill-creator/SKILL.md"}
                    ]
                }
            },
        },
        bridge_run_id="run-1",
        sequence=6,
    )
    message_event = normalize_runtime_event(
        {
            "event": "on_chat_model_end",
            "name": "model",
            "data": {"output": {"messages": [{"content": "完成"}]}},
        },
        bridge_run_id="run-1",
        sequence=20,
    )

    assert skill_event is not None
    assert skill_event.type == "skill"
    assert skill_event.detail == "1 skills ready"
    assert skill_event.data["skills"][0]["name"] == "skill-creator"
    assert message_event is not None
    assert message_event.type == "message.final"
    assert message_event.data["message"]["content"] == "完成"


def test_builtin_tool_selection_filters_only_deepagents_builtin_tools() -> None:
    middleware = BuiltinToolSelectionMiddleware(
        allowlist=frozenset({"ls"}),
        blocklist=frozenset({"execute"}),
    )

    assert middleware._filter_tools(
        [
            {"name": "ls"},
            {"name": "read_file"},
            {"name": "execute"},
            {"name": "custom_tool"},
        ]
    ) == [{"name": "ls"}, {"name": "custom_tool"}]


def test_permissions_make_configured_filesystem_rules_restrictive() -> None:
    rules = build_permissions(
        (
            {"builtin_tools": ["read_file"], "paths": ["/workspace"]},
            {"builtin_tools": ["write_file"], "paths": ["/workspace/out"]},
        )
    )

    assert _check_fs_permission(rules, "read", "/workspace/input.txt") == "allow"
    assert _check_fs_permission(rules, "read", "/etc/passwd") == "deny"
    assert _check_fs_permission(rules, "write", "/workspace/out/result.txt") == "allow"
    assert _check_fs_permission(rules, "write", "/workspace/input.txt") == "deny"


def test_permissions_builtin_tools_star_expands_all_builtins() -> None:
    specs = ({"builtin_tools": ["*"], "paths": ["/workspace"]},)

    assert builtin_tool_allowlist_from_permissions(specs) == BUILTIN_TOOL_ORDER
    rules = build_permissions(specs)
    assert _check_fs_permission(rules, "read", "/workspace/input.txt") == "allow"
    assert _check_fs_permission(rules, "write", "/workspace/out.txt") == "allow"


def test_sandbox_profiles_load_local_skill_source_from_virtual_skills_root() -> None:
    for profile, expected_default in (
        ("safe", StateBackend),
        ("files", FilesystemBackend),
        ("shell", LocalShellBackend),
    ):
        settings = Settings(deepagents_sandbox_profile=profile)
        backend = resolve_backend(SandboxConfig.from_mapping(settings.sandbox_settings()))

        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, expected_default)
        assert [skill["name"] for skill in _list_skills(backend, "/skills")] == ["skill-creator"]


def test_backend_resolution_supports_state_shell_and_custom_specs(tmp_path) -> None:
    module = tmp_path / "backend_mod.py"
    module.write_text(
        "from deepagents.backends import StateBackend\n\n"
        "def build_backend():\n    return StateBackend()\n",
        encoding="utf-8",
    )

    assert isinstance(resolve_backend(SandboxConfig(kind="state")), StateBackend)
    assert isinstance(
        resolve_backend(SandboxConfig(kind="local_shell", virtual_mode=True)),
        LocalShellBackend,
    )
    assert isinstance(
        resolve_backend(SandboxConfig(kind="custom", backend_spec=f"{module}:build_backend")),
        StateBackend,
    )
    assert load_object_from_spec(f"{module}:build_backend")().__class__.__name__ == "StateBackend"


def test_build_deep_agent_omits_permissions_for_shell_backends() -> None:
    captured = {}
    settings = Settings(deepagents_sandbox_profile="shell")

    with (
        patch("app.agent.build_model", lambda settings, model_id=None: object()),
        patch("app.agent.create_deep_agent", lambda **kwargs: captured.update(kwargs) or object()),
    ):
        build_deep_agent(settings)

    assert captured["permissions"] is None
    assert isinstance(captured["backend"], CompositeBackend)
    assert isinstance(captured["backend"].default, LocalShellBackend)
    assert captured["context_schema"] is DeepAgentsRunContext
    assert all(
        "permissions" not in subagent or subagent["permissions"] is None
        for subagent in captured["subagents"]
        if isinstance(subagent, dict)
    )
