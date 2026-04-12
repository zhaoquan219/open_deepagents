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
    "backend/app/db",
    "backend/app/schemas",
    "backend/app/services",
    "backend/app/storage",
    "backend/deepagents_integration",
)
REQUIRED_FRONTEND_DIRS = (
    "frontend/src/api",
    "frontend/src/components",
    "frontend/src/lib",
    "frontend/src/store",
)
REQUIRED_CONTRACT_FILES = (
    "packages/contracts/deepagents-sse-event-v1.json",
    "packages/extension-manifest.template.json",
)
REQUIRED_EXTENSION_TEMPLATES = (
    "backend/extensions/tools/echo_tool.py",
    "backend/extensions/middleware/audit_middleware.py",
    "backend/extensions/skills/README.md",
    "backend/extensions/sandboxes/README.md",
    "backend/app/storage/minio.py",
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


def audit_repo(root: Path) -> AuditReport:
    checks = (
        _check_paths(root, "plan-artifacts", REQUIRED_PLAN_FILES),
        _check_paths(root, "backend-scaffold", REQUIRED_BACKEND_DIRS),
        _check_paths(root, "frontend-scaffold", REQUIRED_FRONTEND_DIRS),
        _check_paths(root, "contract-files", REQUIRED_CONTRACT_FILES),
        _check_paths(root, "extension-templates", REQUIRED_EXTENSION_TEMPLATES),
    )
    return AuditReport(ok=all(check.ok for check in checks), checks=checks)


def main() -> int:
    report = audit_repo(Path.cwd())
    print(report.to_json())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
