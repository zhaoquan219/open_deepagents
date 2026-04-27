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


def _default_model_id() -> str:
    catalog = Settings().load_model_catalog()
    assert catalog is not None
    return str(catalog.default_model_id)


def test_runtime_config_loads_system_prompt_from_project_file() -> None:
    settings = Settings()

    runtime_config = settings.to_runtime_config()

    assert runtime_config.system_prompt == (BACKEND_ROOT / "agents/prompts/system.md").read_text(
        encoding="utf-8"
    ).strip()


def test_runtime_config_uses_default_read_only_permissions_for_data_and_skills() -> None:
    settings = Settings()

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

    assert summary["model_id"] == _default_model_id()
    assert summary["tool_object_count"] >= 1
    assert summary["middleware_object_count"] >= 1
    assert summary["skill_count"] >= 1
    assert summary["memory_count"] >= 1
    assert summary["sandbox_backend_spec_configured"] is True
    assert summary["sandbox_root_dir_configured"] is True


def test_runtime_config_loads_agent_skill_sources_for_deepagents() -> None:
    settings = Settings()

    runtime_config = settings.to_runtime_config()

    assert runtime_config.skills
    assert runtime_config.skill_sources
    bundled = runtime_config.skill_sources[0]
    assert bundled.source_path == "/skills/"
    assert bundled.disk_path == str((BACKEND_ROOT / "agents" / "skills").resolve())
    assert bundled.include == ("skill-creator",)


def test_runtime_config_exposes_default_subagent_and_logging_details() -> None:
    settings = Settings()

    runtime_config = settings.to_runtime_config()
    summary = runtime_config.logging_summary()

    assert summary["skills"] == ("/skills/",)
    assert summary["subagent_names"] == ("code-reviewer",)
    assert "task" in summary["builtin_tool_allowlist"]
    assert "execute" in summary["builtin_tool_blocklist"]
    assert runtime_config.subagents[0]["name"] == "code-reviewer"


def test_runtime_config_resolves_agent_run_and_upload_hooks() -> None:
    settings = Settings()

    runtime_config = settings.to_runtime_config()

    assert runtime_config.run_input_hooks
    assert runtime_config.upload_hooks
    assert callable(runtime_config.run_input_hooks[0])
    assert callable(runtime_config.upload_hooks[0])


def test_runtime_config_skips_bad_subagent_model_when_agent_has_no_subagents(
    monkeypatch,
    tmp_path,
) -> None:
    package = tmp_path / "profile_agent"
    package.mkdir()
    (package / "system.md").write_text("main", encoding="utf-8")
    (package / "__init__.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "ROOT = Path(__file__).parent",
                "AGENT = {",
                "    'id': 'main',",
                "    'system_prompt': ROOT / 'system.md',",
                "    'subagents': [],",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = Settings(
        deepagents_main_agent="profile_agent:AGENT",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.subagents == ()


def test_default_timezone_is_validated_and_configured() -> None:
    settings = Settings(deepagents_default_timezone="Asia/Shanghai")

    assert settings.runtime_timezone().key == "Asia/Shanghai"

    with pytest.raises(ValueError, match="Unknown timezone"):
        Settings(deepagents_default_timezone="Mars/Base")


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
    provider, _, name = _default_model_id().partition("/")
    assert summary["selected_model_provider"] == provider
    assert summary["selected_model_name"] == name


def test_relative_upload_storage_dir_resolves_from_backend_root() -> None:
    settings = Settings(upload_storage_dir="data/custom-uploads")

    assert settings.upload_storage_dir == (BACKEND_ROOT / "data/custom-uploads").resolve()


def test_relative_sandbox_root_dir_resolves_from_backend_root() -> None:
    settings = Settings(
        deepagents_sandbox_kind="filesystem",
        deepagents_sandbox_root_dir="./data",
    )

    assert settings.deepagents_sandbox_root_dir == str((BACKEND_ROOT / "data").resolve())
    assert settings.to_runtime_config().sandbox.root_dir == str((BACKEND_ROOT / "data").resolve())


def test_filesystem_sandbox_defaults_to_backend_data_root() -> None:
    settings = Settings(
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
        upload_storage_dir=custom_upload_dir,
    )

    assert list(settings.to_runtime_config().permissions[0]["paths"]) == [
        normalize_sandbox_permission_path(path) for path in DEFAULT_SANDBOX_READ_PATHS
    ] + [normalize_sandbox_permission_path(custom_upload_dir)]


def test_runtime_config_uses_agent_builtin_tool_allowlist_without_env() -> None:
    settings = Settings()

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


def test_runtime_config_hides_task_builtin_tool_when_agent_has_no_subagents(
    monkeypatch,
    tmp_path,
) -> None:
    package = tmp_path / "no_subagents_agent"
    package.mkdir()
    (package / "__init__.py").write_text(
        "AGENT = {\n"
        "    'id': 'main',\n"
        "    'system_prompt': 'main',\n"
        "    'builtin_tools': ['ls', 'read_file', 'task'],\n"
        "    'subagents': [],\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    settings = Settings(
        deepagents_main_agent="no_subagents_agent:AGENT",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.builtin_tool_allowlist == ("ls", "read_file", "task")
    assert "task" in runtime_config.builtin_tool_blocklist


def test_runtime_config_rejects_invalid_agent_permissions(monkeypatch, tmp_path) -> None:
    package = tmp_path / "invalid_permissions_agent"
    package.mkdir()
    (package / "__init__.py").write_text(
        "AGENT = {'id': 'demo', 'system_prompt': 'demo', 'permissions': {'bad': True}}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = Settings(
        deepagents_main_agent="invalid_permissions_agent:AGENT",
    )

    with pytest.raises(ValueError, match="Expected a list of mappings"):
        settings.to_runtime_config()


def test_runtime_permissions_default_deny_unmatched_write_paths() -> None:
    settings = Settings(
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
        deepagents_main_agent="native_runtime_agent:AGENT",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.checkpointer is not None
    assert runtime_config.store is not None
    assert runtime_config.cache is not None
    assert runtime_config.interrupt_on == {"execute": True}


def test_runtime_config_does_not_expose_native_lifecycle_env_defaults() -> None:
    runtime_config = Settings().to_runtime_config()

    assert runtime_config.checkpointer is None
    assert runtime_config.store is None
    assert runtime_config.cache is None


def test_agent_native_lifecycle_strings_resolve_like_env(monkeypatch, tmp_path) -> None:
    package = tmp_path / "native_string_agent"
    package.mkdir()
    (package / "system.md").write_text("Native strings", encoding="utf-8")
    (package / "__init__.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "AGENT = {",
                "    'id': 'native-strings',",
                "    'system_prompt': Path(__file__).with_name('system.md'),",
                "    'checkpointer': 'memory',",
                "    'store': 'memory',",
                "    'cache': 'memory',",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    runtime_config = Settings(
        deepagents_main_agent="native_string_agent:AGENT",
    ).to_runtime_config()

    assert type(runtime_config.checkpointer).__name__ == "InMemorySaver"
    assert type(runtime_config.store).__name__ == "InMemoryStore"
    assert type(runtime_config.cache).__name__ == "InMemoryCache"


def test_agent_native_lifecycle_none_disables_default_checkpointer(monkeypatch, tmp_path) -> None:
    package = tmp_path / "native_none_agent"
    package.mkdir()
    (package / "system.md").write_text("Native none", encoding="utf-8")
    (package / "__init__.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "AGENT = {",
                "    'id': 'native-none',",
                "    'system_prompt': Path(__file__).with_name('system.md'),",
                "    'checkpointer': None,",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    runtime_config = Settings(
        deepagents_main_agent="native_none_agent:AGENT",
    ).to_runtime_config()

    assert runtime_config.checkpointer is None
