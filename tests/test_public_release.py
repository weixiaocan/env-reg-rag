from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseGateTests(unittest.TestCase):
    def test_public_release_gate_passes_for_repository_candidate_tree(self):
        result = subprocess.run(
            [sys.executable, "scripts/check_public_release.py", "--root", "."],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
