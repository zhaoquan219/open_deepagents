from pathlib import Path, PureWindowsPath

import pytest
from deepagents.middleware.permissions import _check_fs_permission

from app.core.config import (
    BACKEND_ROOT,
    DEFAULT_SANDBOX_READ_PATHS,
    DEFAULT_SANDBOX_ROOT,
    Settings,
    normalize_runtime_backend_path,
    normalize_sandbox_permission_path,
)
from deepagents_integration.extensions import build_permissions, resolve_backend


def test_runtime_config_loads_system_prompt_from_project_file() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.system_prompt == (BACKEND_ROOT / "agents/prompts/system.md").read_text(
        encoding="utf-8"
    ).strip()


def test_runtime_config_uses_default_read_only_permissions_for_data_and_skills() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.permissions == (
        {
            "operations": ["read"],
            "paths": [
                normalize_sandbox_permission_path(path) for path in DEFAULT_SANDBOX_READ_PATHS
            ],
        },
    )


def test_normalize_sandbox_permission_path_supports_windows_drive_paths() -> None:
    path = PureWindowsPath(r"C:\repo\backend\data")

    assert normalize_sandbox_permission_path(path) == "/C:/repo/backend/data"


def test_runtime_config_logging_summary_is_safe_and_concise() -> None:
    settings = Settings(
        deepagents_sandbox_kind="custom",
        deepagents_sandbox_backend_spec="pkg.backend:factory",
        deepagents_sandbox_root_dir="/tmp/workspace",
    )

    summary = settings.to_runtime_config().logging_summary()

    assert summary["model_id"] == "openai/gpt-5-4"
    assert summary["tool_object_count"] >= 1
    assert summary["middleware_object_count"] >= 1
    assert summary["skill_count"] >= 1
    assert summary["memory_count"] >= 1
    assert summary["sandbox_backend_spec_configured"] is True
    assert summary["sandbox_root_dir_configured"] is True


def test_runtime_config_loads_agent_skill_sources_for_deepagents() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.skills
    assert runtime_config.skill_sources
    assert all(source.source_path.startswith("/skills/") for source in runtime_config.skill_sources)


def test_normalize_runtime_backend_path_prefers_repo_relative_sources() -> None:
    path = normalize_runtime_backend_path(
        "agents/skills",
        base_dir=BACKEND_ROOT,
        trailing_slash=True,
    )

    assert path == "/agents/skills/"


def test_runtime_model_logging_summary_is_safe_and_descriptive() -> None:
    summary = Settings().runtime_model_logging_summary()

    assert summary["selected_model_source"] == "model_catalog"
    assert summary["selected_model_provider"] == "openai"
    assert summary["selected_model_name"] == "gpt-5-4"


def test_relative_upload_storage_dir_resolves_from_backend_root() -> None:
    settings = Settings(upload_storage_dir="data/custom-uploads")

    assert settings.upload_storage_dir == (BACKEND_ROOT / "data/custom-uploads").resolve()


def test_relative_sandbox_root_dir_resolves_from_backend_root() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir="./data",
    )

    assert settings.deepagents_sandbox_root_dir == str((BACKEND_ROOT / "data").resolve())
    assert settings.to_runtime_config().sandbox.root_dir == str((BACKEND_ROOT / "data").resolve())


def test_filesystem_sandbox_defaults_to_backend_data_root() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir="",
        deepagents_sandbox_virtual_mode=False,
    )

    runtime_config = settings.to_runtime_config()

    assert settings.deepagents_sandbox_root_dir is None
    assert runtime_config.sandbox.root_dir == str(DEFAULT_SANDBOX_ROOT)
    assert runtime_config.sandbox.virtual_mode is True
    assert settings.logging_summary()["sandbox_root_dir_configured"] is False
    assert settings.logging_summary()["sandbox_root_dir_effective"] is True
    assert runtime_config.logging_summary()["sandbox_root_dir_configured"] is True


def test_filesystem_sandbox_root_is_virtual_root_for_file_tools(tmp_path) -> None:
    (tmp_path / "inside.txt").write_text("sandboxed", encoding="utf-8")
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir=str(tmp_path),
        deepagents_sandbox_virtual_mode=False,
    )

    backend = resolve_backend(settings.to_runtime_config().sandbox)
    result = backend.ls("/")

    assert result.error is None
    entries = result.entries or []
    assert len(entries) == 1
    assert entries[0]["path"] == "/inside.txt"
    assert entries[0]["is_dir"] is False
    assert entries[0]["size"] == len("sandboxed")


def test_local_shell_sandbox_defaults_to_backend_data_root() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_sandbox_kind="local_shell",
        deepagents_sandbox_root_dir="",
        deepagents_sandbox_virtual_mode=False,
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.sandbox.root_dir == str(DEFAULT_SANDBOX_ROOT)
    assert runtime_config.sandbox.virtual_mode is True


def test_runtime_permissions_include_custom_upload_dir_outside_default_data_root() -> None:
    custom_upload_dir = Path("/tmp/open-deepagents-uploads").resolve()
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        upload_storage_dir=custom_upload_dir,
    )

    assert list(settings.to_runtime_config().permissions[0]["paths"]) == [
        normalize_sandbox_permission_path(path) for path in DEFAULT_SANDBOX_READ_PATHS
    ] + [normalize_sandbox_permission_path(custom_upload_dir)]


def test_runtime_config_uses_agent_builtin_tool_allowlist_without_env() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_builtin_tools=None,
        deepagents_disabled_builtin_tools=None,
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.builtin_tool_allowlist == (
        "write_todos",
        "ls",
        "read_file",
        "glob",
        "grep",
        "task",
    )
    assert runtime_config.builtin_tool_blocklist == ("execute", "write_file", "edit_file")


def test_env_builtin_tool_allowlist_overrides_agent_default() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_builtin_tools="ls,read_file",
        deepagents_disabled_builtin_tools="execute",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.builtin_tool_allowlist == ("ls", "read_file")
    assert runtime_config.builtin_tool_blocklist == (
        "execute",
        "write_file",
        "edit_file",
    )


def test_runtime_config_rejects_invalid_agent_permissions(monkeypatch, tmp_path) -> None:
    package = tmp_path / "invalid_permissions_agent"
    package.mkdir()
    (package / "__init__.py").write_text(
        "AGENT = {'id': 'demo', 'system_prompt': 'demo', 'permissions': {'bad': True}}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_main_agent="invalid_permissions_agent:AGENT",
    )

    with pytest.raises(ValueError, match="Expected a list of mappings"):
        settings.to_runtime_config()


def test_runtime_permissions_default_deny_unmatched_write_paths() -> None:
    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir="/tmp/sandbox",
    )

    permissions = settings.to_runtime_config().permissions
    built_permissions = build_permissions(permissions)

    assert _check_fs_permission(built_permissions, "write", "/uploads/out.txt") == "deny"
    assert _check_fs_permission(
        built_permissions,
        "read",
        normalize_sandbox_permission_path(DEFAULT_SANDBOX_ROOT / "uploads" / "in.txt"),
    ) == "allow"


def test_runtime_config_preserves_agent_native_lifecycle_fields(monkeypatch, tmp_path) -> None:
    package = tmp_path / "native_runtime_agent"
    package.mkdir()
    (package / "system.md").write_text("Native runtime agent", encoding="utf-8")
    (package / "__init__.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "CHECKPOINTER = object()",
                "STORE = object()",
                "CACHE = object()",
                "AGENT = {",
                "    'id': 'native-runtime',",
                "    'system_prompt': Path(__file__).with_name('system.md'),",
                "    'checkpointer': CHECKPOINTER,",
                "    'store': STORE,",
                "    'cache': CACHE,",
                "    'interrupt_on': {'execute': True},",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = Settings(
        deepagents_default_model="openai/gpt-5-4",
        deepagents_main_agent="native_runtime_agent:AGENT",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.checkpointer is not None
    assert runtime_config.store is not None
    assert runtime_config.cache is not None
    assert runtime_config.interrupt_on == {"execute": True}
