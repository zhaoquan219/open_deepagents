import json
from pathlib import Path, PureWindowsPath

import app.core.config as config_module
from app.core.config import (
    BACKEND_ROOT,
    DEEPAGENTS_SYSTEM_PROMPT_PATH,
    DEFAULT_SANDBOX_READ_PATHS,
    Settings,
    normalize_sandbox_permission_path,
)


def test_runtime_config_loads_system_prompt_from_project_file() -> None:
    settings = Settings(
        deepagents_model="openai:gpt-5.4",
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.system_prompt == DEEPAGENTS_SYSTEM_PROMPT_PATH.read_text(
        encoding="utf-8"
    ).strip()


def test_runtime_config_uses_default_read_only_permissions_for_data_and_skills() -> None:
    settings = Settings(
        deepagents_model="openai:gpt-5.4",
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


def test_custom_api_default_headers_accepts_json_and_legacy_pairs() -> None:
    settings = Settings(
        custom_api_default_headers='{"HTTP-Referer":"https://app.example.com","X-Title":"open_deepagents"}'
    )
    legacy_settings = Settings(custom_api_default_headers="X-One=1, X-Two=2")

    assert settings.custom_api_default_headers == {
        "HTTP-Referer": "https://app.example.com",
        "X-Title": "open_deepagents",
    }
    assert legacy_settings.custom_api_default_headers == {"X-One": "1", "X-Two": "2"}


def test_resolve_model_omits_optional_custom_model_kwargs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(config_module, "ChatOpenAI", FakeChatOpenAI)

    settings = Settings(
        custom_api_key="secret",
        custom_api_url="https://example.com/inference",
        custom_api_model="demo-model",
        custom_api_temperature=None,
        custom_api_default_headers="",
    )

    model = settings.resolve_model()

    assert isinstance(model, FakeChatOpenAI)
    assert captured["model"] == "demo-model"
    assert captured["base_url"] == "https://example.com/inference/v1"
    assert "temperature" not in captured
    assert "default_headers" not in captured


def test_resolve_model_passes_configured_temperature_and_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(config_module, "ChatOpenAI", FakeChatOpenAI)

    settings = Settings(
        custom_api_key="secret",
        custom_api_url="https://example.com/chat/completions",
        custom_api_model="demo-model",
        custom_api_temperature=0.35,
        custom_api_default_headers='{"HTTP-Referer":"https://app.example.com","X-Title":"open_deepagents"}',
    )

    model = settings.resolve_model()

    assert isinstance(model, FakeChatOpenAI)
    assert captured["base_url"] == "https://example.com"
    assert captured["temperature"] == 0.35
    assert captured["default_headers"] == {
        "HTTP-Referer": "https://app.example.com",
        "X-Title": "open_deepagents",
    }


def test_runtime_config_logging_summary_is_safe_and_concise() -> None:
    settings = Settings(
        custom_api_key="secret-key",
        custom_api_url="https://example.com/chat/completions",
        custom_api_model="demo-model",
        deepagents_tool_specs="pkg.tools:ONE,pkg.tools:TWO",
        deepagents_middleware_specs="pkg.middleware:AUDIT",
        deepagents_skills="/skills/project",
        deepagents_memory="/memory/AGENTS.md",
        deepagents_sandbox_kind="custom",
        deepagents_sandbox_backend_spec="pkg.backend:factory",
        deepagents_sandbox_root_dir="/tmp/workspace",
    )

    summary = settings.to_runtime_config().logging_summary()
    summary_text = json.dumps(summary, sort_keys=True)

    assert summary["model_kind"] == "custom_api"
    assert summary["model_name"] == "demo-model"
    assert summary["tool_count"] == 2
    assert summary["middleware_count"] == 1
    assert summary["skill_count"] == 1
    assert summary["memory_count"] == 1
    assert summary["sandbox_backend_spec_configured"] is True
    assert summary["sandbox_root_dir_configured"] is True
    assert "secret-key" not in summary_text
    assert "example.com" not in summary_text


def test_runtime_model_logging_summary_is_safe_and_descriptive() -> None:
    settings = Settings(
        custom_api_key="secret-key",
        custom_api_url="https://user:pass@example.com/chat/completions?token=abc",
        custom_api_model="demo-model",
        custom_api_temperature=0.2,
        custom_api_default_headers='{"HTTP-Referer":"https://app.example.com","X-Title":"open_deepagents"}',
    )

    summary = settings.runtime_model_logging_summary()
    summary_text = json.dumps(summary, sort_keys=True)

    assert summary["selected_model_source"] == "custom_api"
    assert summary["selected_model_provider"] == "custom_api"
    assert summary["selected_model_name"] == "demo-model"
    assert summary["custom_model_base_url"] == "https://example.com"
    assert summary["custom_model_headers_count"] == 2
    assert summary["custom_model_temperature_configured"] is True
    assert "secret-key" not in summary_text
    assert "user:pass" not in summary_text
    assert "token=abc" not in summary_text


def test_relative_upload_storage_dir_resolves_from_backend_root() -> None:
    settings = Settings(upload_storage_dir="data/custom-uploads")

    assert settings.upload_storage_dir == (BACKEND_ROOT / "data/custom-uploads").resolve()


def test_runtime_permissions_include_custom_upload_dir_outside_default_data_root() -> None:
    custom_upload_dir = Path("/tmp/open-deepagents-uploads").resolve()
    settings = Settings(
        deepagents_model="openai:gpt-5.4",
        upload_storage_dir=custom_upload_dir,
    )

    assert list(settings.to_runtime_config().permissions[0]["paths"]) == [
        normalize_sandbox_permission_path(path) for path in DEFAULT_SANDBOX_READ_PATHS
    ] + [normalize_sandbox_permission_path(custom_upload_dir)]
