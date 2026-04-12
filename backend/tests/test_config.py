from app.core.config import DEEPAGENTS_SYSTEM_PROMPT_PATH, DEFAULT_SANDBOX_READ_PATHS, Settings


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
            "paths": [str(path) for path in DEFAULT_SANDBOX_READ_PATHS],
        },
    )
