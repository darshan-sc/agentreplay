from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ExampleScriptTests(unittest.TestCase):
    def test_record_smoke_imports_local_package_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            fake_openai = Path(tempdir) / "openai.py"
            fake_openai.write_text("class OpenAI:\n    pass\n", encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = tempdir

            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import runpy; runpy.run_path('python/examples/openai_record_smoke.py')",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
