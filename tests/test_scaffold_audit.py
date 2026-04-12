from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verification.scaffold_audit import audit_repo


class ScaffoldAuditTests(unittest.TestCase):
    def _write(self, root: Path, relative_path: str, content: str = "") -> None:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def test_audit_reports_missing_scaffold_elements(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = audit_repo(Path(tmpdir))
            self.assertFalse(report.ok)
            self.assertTrue(any(check.name == "backend-scaffold" and not check.ok for check in report.checks))

    def test_audit_passes_when_expected_layout_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root, ".omx/plans/prd-deepagents-agent-platform.md", "prd")
            self._write(root, ".omx/plans/test-spec-deepagents-agent-platform.md", "spec")
            self._write(root, "packages/contracts/deepagents-sse-event-v1.json", "{}")
            self._write(
                root,
                ".env.example",
                "\n".join(
                    [
                        "DEEPAGENTS_MODEL=openai:gpt-5.4",
                        "OPENAI_API_KEY=test",
                        "MYSQL_HOST=localhost",
                        "MYSQL_PORT=3306",
                        "MYSQL_DATABASE=deepagents",
                        "MYSQL_USER=test",
                        "MYSQL_PASSWORD=test",
                        "ADMIN_EMAIL=admin@example.com",
                        "ADMIN_PASSWORD=secret",
                        "UPLOAD_ROOT=./data/uploads",
                    ]
                ),
            )
            for relative_dir in (
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
                "frontend/src/layouts",
                "frontend/src/pages",
                "frontend/src/components/chat",
                "frontend/src/components/session",
                "frontend/src/components/progress",
                "frontend/src/components/renderers",
                "frontend/src/stores",
                "frontend/src/services",
                "frontend/src/composables",
            ):
                (root / relative_dir).mkdir(parents=True, exist_ok=True)

            report = audit_repo(root)
            self.assertTrue(report.ok)
            self.assertTrue(all(check.ok for check in report.checks))


if __name__ == "__main__":
    unittest.main()
