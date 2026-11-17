import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "plugins/cache/scripts/cachectl.py"
ROOT = SCRIPT.parent


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

    def test_profiles_and_mcp_protocol(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            env = os.environ.copy()
            env["HOME"] = str(home)
            for key in ("CACHE_VOLUME", "CACHE_LIMIT_GB", "CACHE_MIN_FREE_GIB", "CACHE_RECOVER_TO_GIB"):
                env.pop(key, None)
            mount = Path("/Volumes/Mac Work Cache")
            if mount.is_mount():
                configured = self.call(home, "configure", str(mount), "--name", "work", "--kind", "internal")
                self.assertEqual(configured.returncode, 0, configured.stderr)
                selected = self.call(home, "use", "work")
                self.assertEqual(selected.returncode, 0, selected.stderr)
                listed = json.loads(self.call(home, "backends").stdout)
                self.assertEqual(listed["backends"][0]["name"], "work")
                self.assertTrue(listed["backends"][0]["mounted"])
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "cache_assess", "arguments": {"path": str(home / ".codex/sessions")}}},
            ]
            process = subprocess.run([sys.executable, str(ROOT / "cacheplus_mcp.py")], env=env,
                                     input="\n".join(map(json.dumps, messages)) + "\n",
                                     text=True, capture_output=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            replies = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(replies), 3)
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-11-25")
            self.assertEqual(len(replies[1]["result"]["tools"]), 3)
            self.assertEqual(replies[2]["result"]["structuredContent"]["decision"], "keep")

    def test_api_read_only(self):
        sys.path.insert(0, str(ROOT))
        import cacheplus_api
        server = ThreadingHTTPServer(("127.0.0.1", 0), cacheplus_api.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/v1/status", timeout=3) as response:
                payload = json.load(response)
                self.assertIn("free_gib", payload)
            with urlopen(base + "/v1/backends", timeout=3) as response:
                self.assertIn("backends", json.load(response))
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(base + "/v1/status", data=b"{}"), timeout=3)
            self.assertEqual(rejected.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
