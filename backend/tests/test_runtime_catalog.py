import json

import pytest

import app.core.model_catalog as model_catalog_module
from app.core.model_catalog import ModelCatalog
from app.core.runtime_catalog import RuntimeSelection, resolve_runtime
from deepagents_integration.extensions import SelectablePathRegistry, discover_components


def test_model_catalog_resolves_flat_model_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setattr(model_catalog_module, "ChatOpenAI", FakeChatOpenAI)
    catalog = ModelCatalog(
        {
            "model": "openai/gpt-5-4",
            "provider": {
                "openai": {
                    "name": "OpenAI",
                    "options": {
                        "api_key": "${OPENAI_API_KEY}",
                        "base_url": "https://api.openai.com/v1",
                        "default_headers": {"X-App": "open_deepagents"},
                        "timeout": 30,
                        "max_retries": 3,
                    },
                    "models": {
                        "gpt-5-4": {
                            "name": "GPT-5.4",
                            "model": "gpt-5.4",
                            "temperature": 0.1,
                            "extra_body": {"reasoning": {"effort": "medium"}},
                        }
                    },
                }
            },
        }
    )

    model = catalog.resolve()

    assert isinstance(model, FakeChatOpenAI)
    assert captured["model"] == "gpt-5.4"
    assert str(captured["api_key"]) == "**********"
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert captured["default_headers"] == {"X-App": "open_deepagents"}
    assert captured["temperature"] == 0.1
    assert captured["extra_body"] == {"reasoning": {"effort": "medium"}}
    assert captured["timeout"] == 30
    assert captured["max_retries"] == 3


def test_model_catalog_rejects_nested_model_options() -> None:
    with pytest.raises(ValueError, match="flat fields"):
        ModelCatalog(
            {
                "model": "openai/demo",
                "provider": {
                    "openai": {
                        "options": {"api_key": "${OPENAI_API_KEY}"},
                        "models": {"demo": {"model": "demo", "options": {"temperature": 0.1}}},
                    }
                },
            }
        )


def test_model_catalog_safe_options_omit_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    catalog = ModelCatalog(
        {
            "model": "openai/demo",
            "provider": {
                "openai": {
                    "name": "OpenAI",
                    "options": {"api_key": "${OPENAI_API_KEY}"},
                    "models": {"demo": {"name": "Demo", "model": "demo"}},
                }
            },
        }
    )

    safe_text = json.dumps(catalog.safe_options(), sort_keys=True)

    assert "secret" not in safe_text
    assert "OPENAI_API_KEY" not in safe_text
    assert catalog.safe_options()["default_model_id"] == "openai/demo"


def test_discover_components_supports_explicit_and_auto_exports(tmp_path, monkeypatch) -> None:
    package = tmp_path / "demo_agents" / "tools"
    package.mkdir(parents=True)
    (tmp_path / "demo_agents" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "one.py").write_text("TOOL = 'one'\n", encoding="utf-8")
    (package / "two.py").write_text("TOOLS = ['two-a', 'two-b']\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert discover_components("demo_agents.tools", names=("TOOLS", "TOOL")) == [
        "one",
        "two-a",
        "two-b",
    ]


def test_selectable_skill_registry_materializes_selected_ids(tmp_path) -> None:
    skill_root = tmp_path / "skills"
    (skill_root / "alpha").mkdir(parents=True)
    (skill_root / "alpha" / "SKILL.md").write_text("# Alpha", encoding="utf-8")
    (skill_root / "beta").mkdir()
    (skill_root / "beta" / "SKILL.md").write_text("# Beta", encoding="utf-8")

    selected = SelectablePathRegistry.from_directory(
        skill_root,
        source_prefix="/skills",
    ).select(["beta"])

    assert len(selected) == 1
    assert selected[0].include == ("beta",)
    assert selected[0].source_path.startswith("/skills/")


def test_agent_package_supports_star_single_and_list_selections(tmp_path, monkeypatch) -> None:
    package = tmp_path / "demo_agent"
    for directory in (
        "prompts",
        "tools",
        "skills/alpha",
        "skills/beta",
        "memory",
        "subagents/child",
    ):
        (package / directory).mkdir(parents=True)
    (package / "prompts" / "system.md").write_text("Main", encoding="utf-8")
    (package / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (package / "tools" / "one.py").write_text("TOOL = 'tool-one'\n", encoding="utf-8")
    (package / "skills" / "alpha" / "SKILL.md").write_text("# Alpha", encoding="utf-8")
    (package / "skills" / "beta" / "SKILL.md").write_text("# Beta", encoding="utf-8")
    (package / "memory" / "__init__.py").write_text("", encoding="utf-8")
    (package / "memory" / "project.md").write_text("# Project", encoding="utf-8")
    (package / "subagents" / "__init__.py").write_text("", encoding="utf-8")
    (package / "subagents" / "child" / "__init__.py").write_text(
        "SUBAGENT = {\n"
        "  'id': 'child', 'name': 'child', 'description': 'child',\n"
        "  'system_prompt': 'child'\n"
        "}\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        "ROOT = Path(__file__).parent\n"
        "AGENT = {\n"
        "  'id': 'main',\n"
        "  'system_prompt': ROOT / 'prompts' / 'system.md',\n"
        "  'tools': '*',\n"
        "  'skills': ['alpha'],\n"
        "  'memory': 'project',\n"
        "  'subagents': ['child'],\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    resolution = resolve_runtime(
        agent_spec="demo_agent:AGENT",
        model_catalog=None,
        default_model_id="openai:gpt-5.4",
        selection=RuntimeSelection(),
    )

    assert resolution.agent["tools"] == ["tool-one"]
    assert resolution.agent["skill_sources"][0].include == ("alpha",)
    assert resolution.agent["memory"] == (str(package / "memory" / "project.md"),)
    assert resolution.subagents[0]["name"] == "child"


def test_runtime_resolution_rejects_agent_level_sandbox(tmp_path, monkeypatch) -> None:
    package = tmp_path / "demo_sandbox_agent"
    prompts = package / "prompts"
    prompts.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        "ROOT = Path(__file__).parent\n"
        "AGENT = {\n"
        "  'id': 'main',\n"
        "  'system_prompt_path': ROOT / 'prompts' / 'system.md',\n"
        "  'subagents': [{\n"
        "    'id': 'bad', 'name': 'bad', 'description': 'bad',\n"
        "    'system_prompt': 'bad', 'sandbox': {'kind': 'state'}\n"
        "  }]\n"
        "}\n",
        encoding="utf-8",
    )
    (prompts / "system.md").write_text("Main", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ValueError, match="sandbox"):
        resolve_runtime(
            agent_spec="demo_sandbox_agent:AGENT",
            model_catalog=None,
            default_model_id="openai:gpt-5.4",
            selection=RuntimeSelection(),
        )
