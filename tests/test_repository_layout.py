from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicRepositoryLayoutTests(unittest.TestCase):
    def test_dependency_entry_points_are_unambiguous(self):
        requirement_files = {
            path.name for path in ROOT.glob("requirements*.txt") if path.is_file()
        }
        self.assertEqual(
            requirement_files,
            {"requirements.txt", "requirements-ocr.txt"},
        )

    def test_only_product_facing_documents_remain(self):
        self.assertEqual(
            {path.name for path in (ROOT / "docs").iterdir() if path.is_file()},
            {"ARCHITECTURE.md", "CLEANUP_PLAN.md", "SCOPE_AND_DEFERRED.md",
             "v2-canonical-schema-design.md"},
        )

    def test_readme_is_product_facing(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotRegex(
            readme,
            r"(?<![A-Za-z0-9])(?:M[1-6]|P7)(?![A-Za-z0-9])",
        )
        self.assertNotIn("当前阶段", readme)
        for required_heading in ("## 核心能力", "## 快速开始", "## 测试", "## 项目结构", "## 已知限制"):
            self.assertIn(required_heading, readme)

    def test_container_files_use_public_project_name_and_default_requirements(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("requirements.txt", dockerfile)
        self.assertNotIn("requirements-m", dockerfile)
        self.assertNotIn("requirements-runtime", dockerfile)
        self.assertNotIn("drainage-rag", compose)
        self.assertNotIn("drainage_rag", compose)


if __name__ == "__main__":
    unittest.main()
