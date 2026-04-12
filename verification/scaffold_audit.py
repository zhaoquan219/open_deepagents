from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REQUIRED_PLAN_FILES = (
    ".omx/plans/prd-deepagents-agent-platform.md",
    ".omx/plans/test-spec-deepagents-agent-platform.md",
)
REQUIRED_BACKEND_DIRS = (
    "backend/app/api",
    "backend/app/core",
    "backend/app/auth",
    "backend/app/sessions",
    "backend/app/chat",
    "backend/app/uploads",
    "backend/app/agents",
    "backend/app/extensions",
    "backend/app/storage",
    "backend/app/db",
)
REQUIRED_FRONTEND_DIRS = (
    "frontend/src/layouts",
    "frontend/src/pages",
    "frontend/src/components/chat",
    "frontend/src/components/session",
    "frontend/src/components/progress",
    "frontend/src/components/renderers",
    "frontend/src/stores",
    "frontend/src/services",
    "frontend/src/composables",
)
REQUIRED_CONTRACT_FILES = (
    "packages/contracts/deepagents-sse-event-v1.json",
    "packages/extension-manifest.example.json",
)
REQUIRED_EXTENSION_EXAMPLES = (
    "backend/examples/tools/echo_tool.py",
    "backend/examples/middleware/audit_middleware.py",
    "backend/examples/skills/README.md",
    "backend/examples/sandboxes/README.md",
    "backend/app/storage/minio.py",
)
REQUIRED_ENV_KEYS = (
    "DEEPAGENTS_MODEL",
    "OPENAI_API_KEY",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "UPLOAD_ROOT",
)


@dataclass(frozen=True)
class AuditCheck:
    name: str
    ok: bool
    details: str


@dataclass(frozen=True)
class AuditReport:
    ok: bool
    checks: tuple[AuditCheck, ...]

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, "checks": [asdict(check) for check in self.checks]}, indent=2)


def _missing_paths(root: Path, relative_paths: Iterable[str]) -> list[str]:
    return [path for path in relative_paths if not (root / path).exists()]


def _check_paths(root: Path, name: str, relative_paths: Iterable[str]) -> AuditCheck:
    missing = _missing_paths(root, relative_paths)
    if missing:
        return AuditCheck(name=name, ok=False, details=f"missing: {', '.join(missing)}")
    return AuditCheck(name=name, ok=True, details="all required paths exist")


def _check_env_example(root: Path) -> AuditCheck:
    env_example = root / ".env.example"
    if not env_example.exists():
        return AuditCheck(name="env-example", ok=False, details="missing .env.example")

    contents = env_example.read_text()
    missing_keys = [key for key in REQUIRED_ENV_KEYS if f"{key}=" not in contents]
    if missing_keys:
        return AuditCheck(
            name="env-example",
            ok=False,
            details=f"missing keys: {', '.join(missing_keys)}",
        )

    return AuditCheck(name="env-example", ok=True, details="contains required scaffold configuration keys")


def audit_repo(root: Path) -> AuditReport:
    checks = (
        _check_paths(root, "plan-artifacts", REQUIRED_PLAN_FILES),
        _check_paths(root, "backend-scaffold", REQUIRED_BACKEND_DIRS),
        _check_paths(root, "frontend-scaffold", REQUIRED_FRONTEND_DIRS),
        _check_paths(root, "contract-files", REQUIRED_CONTRACT_FILES),
        _check_paths(root, "extension-examples", REQUIRED_EXTENSION_EXAMPLES),
        _check_env_example(root),
    )
    return AuditReport(ok=all(check.ok for check in checks), checks=checks)


def main() -> int:
    report = audit_repo(Path.cwd())
    print(report.to_json())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
