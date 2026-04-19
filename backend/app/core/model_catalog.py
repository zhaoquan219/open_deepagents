from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

CHAT_OPENAI_OPTION_KEYS = {
    "api_key",
    "base_url",
    "default_headers",
    "extra_body",
    "max_retries",
    "temperature",
    "timeout",
}


@dataclass(frozen=True)
class ModelMetadata:
    id: str
    name: str
    provider: str
    provider_name: str


class ModelCatalog:
    def __init__(self, raw: dict[str, Any], *, path: Path | None = None) -> None:
        self.raw = raw
        self.path = path
        self.default_model_id = _expect_string(raw.get("model"), "model")
        providers = raw.get("provider")
        if not isinstance(providers, dict) or not providers:
            raise ValueError("models.json must define a non-empty provider object")
        self.providers: dict[str, dict[str, Any]] = {}
        self.models: dict[str, tuple[str, dict[str, Any]]] = {}
        for provider_id, provider in providers.items():
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("provider ids must be non-empty strings")
            if not isinstance(provider, dict):
                raise ValueError(f"provider {provider_id!r} must be an object")
            options = provider.get("options", {})
            if options is None:
                options = {}
            if not isinstance(options, dict):
                raise ValueError(f"provider {provider_id!r}.options must be an object")
            _validate_options(options, f"provider {provider_id!r}.options")
            provider_models = provider.get("models")
            if not isinstance(provider_models, dict) or not provider_models:
                raise ValueError(f"provider {provider_id!r} must define a non-empty models object")
            self.providers[provider_id] = provider
            for model_alias, model in provider_models.items():
                if not isinstance(model_alias, str) or not model_alias:
                    raise ValueError(f"model ids for provider {provider_id!r} must be strings")
                if not isinstance(model, dict):
                    raise ValueError(f"model {provider_id}/{model_alias} must be an object")
                if "options" in model:
                    raise ValueError(
                        f"model {provider_id}/{model_alias} must use flat fields, not options"
                    )
                _expect_string(model.get("model"), f"model {provider_id}/{model_alias}.model")
                model_options = {
                    key: value
                    for key, value in model.items()
                    if key in CHAT_OPENAI_OPTION_KEYS
                }
                _validate_options(model_options, f"model {provider_id}/{model_alias}")
                self.models[f"{provider_id}/{model_alias}"] = (provider_id, model)
        if self.default_model_id not in self.models:
            raise ValueError(
                f"default model {self.default_model_id!r} is not defined. "
                f"Available models: {', '.join(sorted(self.models))}"
            )

    @classmethod
    def from_file(cls, path: Path) -> ModelCatalog:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} contains invalid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return cls(raw, path=path)

    def resolve(self, model_id: str | None = None) -> ChatOpenAI:
        selected_id = model_id or self.default_model_id
        provider_id, model = self._model_entry(selected_id)
        provider = self.providers[provider_id]
        options = dict(provider.get("options") or {})
        for key in CHAT_OPENAI_OPTION_KEYS:
            if key in model:
                options[key] = model[key]
        options = {key: value for key, value in options.items() if value is not None}
        options["model"] = model["model"]
        if "api_key" in options:
            options["api_key"] = SecretStr(_resolve_env_placeholder(str(options["api_key"])))
        return ChatOpenAI(**options)

    def metadata(self) -> list[ModelMetadata]:
        items: list[ModelMetadata] = []
        for model_id in sorted(self.models):
            provider_id, model = self.models[model_id]
            provider = self.providers[provider_id]
            items.append(
                ModelMetadata(
                    id=model_id,
                    name=str(model.get("name") or model.get("model") or model_id),
                    provider=provider_id,
                    provider_name=str(provider.get("name") or provider_id),
                )
            )
        return items

    def safe_options(self) -> dict[str, Any]:
        return {
            "default_model_id": self.default_model_id,
            "models": [
                {
                    "id": item.id,
                    "name": item.name,
                    "provider": item.provider,
                    "provider_name": item.provider_name,
                }
                for item in self.metadata()
            ],
        }

    def _model_entry(self, model_id: str) -> tuple[str, dict[str, Any]]:
        if model_id not in self.models:
            raise ValueError(
                f"Unknown model id: {model_id}. Available models: {', '.join(sorted(self.models))}"
            )
        return self.models[model_id]


def load_model_catalog(
    path: Path | None,
    *,
    fallback_path: Path | None = None,
) -> ModelCatalog | None:
    if path is None:
        return None
    if not path.is_file():
        if fallback_path is not None and fallback_path.is_file():
            return ModelCatalog.from_file(fallback_path)
        return None
    return ModelCatalog.from_file(path)


def _expect_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _resolve_env_placeholder(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value


def _validate_options(options: dict[str, Any], field_name: str) -> None:
    unknown = sorted(set(options) - CHAT_OPENAI_OPTION_KEYS)
    if unknown:
        raise ValueError(f"{field_name} contains unsupported option(s): {', '.join(unknown)}")
