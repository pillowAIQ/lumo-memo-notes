"""Fast installer configuration checks; the real lifecycle is verified separately."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lumo_build_installer", ROOT / "build_installer.py")
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


class InstallerTests(unittest.TestCase):
    def test_version_and_stable_identity(self):
        import uuid
        self.assertEqual(str(uuid.UUID(builder.APP_ID)).upper(), builder.APP_ID)
        self.assertRegex(builder.app_version(), r"^\d+\.\d+\.\d+$")

    def test_incomplete_bundle_is_rejected_before_compiler(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp, patch.object(builder.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "bundle missing"):
                builder.build(Path(temp) / "missing", temp, Path("not-compiler.exe"))
            run.assert_not_called()


if __name__ == "__main__": unittest.main()
