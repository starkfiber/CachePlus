import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "plugins/cache/scripts/cachectl.py"


class CacheSafetyTests(unittest.TestCase):
    def call(self, home, *args):
        env = os.environ.copy()
        env["HOME"] = str(home)
        for key in ("CACHE_VOLUME", "CACHE_LIMIT_GB", "CACHE_MIN_FREE_GIB", "CACHE_RECOVER_TO_GIB"):
            env.pop(key, None)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              text=True, capture_output=True)

    def test_unconfigured_status_and_persistent_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            status = self.call(home, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertFalse(json.loads(status.stdout)["volume_mounted"])
            review = self.call(home, "assess", str(home / ".codex/sessions"))
            self.assertEqual(review.returncode, 0, review.stderr)
            self.assertEqual(json.loads(review.stdout)["decision"], "keep")

    def test_unmounted_path_cannot_run_or_configure(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            candidate = home / "not-a-volume"
            candidate.mkdir()
            configured = self.call(home, "configure", str(candidate))
            self.assertNotEqual(configured.returncode, 0)
            self.assertFalse((home / ".config/cache/config.json").exists())
            run = self.call(home, "run", "--disposable", "test", "--", "/usr/bin/true")
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((candidate / "Disposable Jobs").exists())


if __name__ == "__main__":
    unittest.main()
