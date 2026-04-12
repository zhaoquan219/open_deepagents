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
            self._write(root, "packages/extension-manifest.template.json", "{}")
            self._write(root, "backend/extensions/tools/echo_tool.py", "TOOLS = []")
            self._write(root, "backend/extensions/middleware/audit_middleware.py", "MIDDLEWARE = []")
            self._write(root, "backend/extensions/skills/README.md", "skills")
            self._write(root, "backend/extensions/sandboxes/README.md", "sandboxes")
            self._write(root, "backend/app/storage/minio.py", "class MinioStoragePlaceholder: ...")
            for relative_dir in (
                "backend/app/api",
                "backend/app/core",
                "backend/app/db",
                "backend/app/schemas",
                "backend/app/services",
                "backend/app/storage",
                "backend/deepagents_integration",
                "frontend/src/api",
                "frontend/src/components",
                "frontend/src/lib",
                "frontend/src/store",
            ):
                (root / relative_dir).mkdir(parents=True, exist_ok=True)

            report = audit_repo(root)
            self.assertTrue(report.ok)
            self.assertTrue(all(check.ok for check in report.checks))


if __name__ == "__main__":
    unittest.main()
