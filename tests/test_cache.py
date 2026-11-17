import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "plugins/cache/scripts/cachectl.py"
ROOT = SCRIPT.parent
POLICY_OVERRIDES = ("CACHE_VOLUME", "CACHE_LIMIT_GB", "CACHE_MIN_FREE_GIB", "CACHE_RECOVER_TO_GIB")


def fixture_environment():
    return {key: value for key, value in os.environ.items() if key not in POLICY_OVERRIDES}


class CacheSafetyTests(unittest.TestCase):
    def call(self, home, *args):
        env = fixture_environment()
        env["HOME"] = str(home)
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              text=True, capture_output=True)

    def test_unconfigured_status_and_persistent_codex(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            status = self.call(home, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            data = json.loads(status.stdout)
            self.assertFalse(data["volume_mounted"])
            self.assertEqual(data["usable_after_cache_reclaim_gib"], data["free_gib"])
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
            self.assertEqual(len(replies[1]["result"]["tools"]), 4)
            self.assertEqual(replies[2]["result"]["structuredContent"]["decision"], "keep")

    def test_mcp_run_requires_explicit_disposable_and_keeps_protocol_clean(self):
        sys.path.insert(0, str(ROOT))
        import cacheplus_mcp
        request = {"method": "tools/call", "params": {"name": "cache_run",
                   "arguments": {"label": "demo", "command": ["/usr/bin/true"], "disposable": False}}}
        with self.assertRaises(ValueError):
            cacheplus_mcp.dispatch(request)
        request["params"]["arguments"]["disposable"] = True
        with mock.patch.object(cacheplus_mcp.subprocess, "run") as called:
            called.return_value.returncode = 0
            result = cacheplus_mcp.dispatch(request)
            self.assertEqual(result["structuredContent"]["exit_code"], 0)
            self.assertEqual(called.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_long_lived_mcp_sees_profile_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / ".config/cache/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"volume": str(home / "drive-a"), "limit_gb": 10}))
            env = os.environ.copy()
            env["HOME"] = str(home)
            for key in ("CACHE_VOLUME", "CACHE_LIMIT_GB", "CACHE_MIN_FREE_GIB", "CACHE_RECOVER_TO_GIB"):
                env.pop(key, None)
            process = subprocess.Popen([sys.executable, str(ROOT / "cacheplus_mcp.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=env)
            def exchange(index, method, params=None):
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": index,
                                                "method": method, "params": params or {}}) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())
            try:
                exchange(1, "initialize", {"protocolVersion": "2025-11-25"})
                request = {"name": "cache_status", "arguments": {}}
                first = exchange(2, "tools/call", request)
                config.write_text(json.dumps({"volume": str(home / "drive-b"), "limit_gb": 20}))
                second = exchange(3, "tools/call", request)
                self.assertEqual(first["result"]["structuredContent"]["configured_volume"], str(home / "drive-a"))
                self.assertEqual(second["result"]["structuredContent"]["configured_volume"], str(home / "drive-b"))
                self.assertEqual(second["result"]["structuredContent"]["configured_bank_limit_gb"], 20)
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_long_lived_api_sees_profile_switch(self):
        sys.path.insert(0, str(ROOT))
        import bank
        import cachectl
        import cacheplus_api
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"volume": str(Path(temp) / "one"), "limit_gb": 10}))
            with (mock.patch.object(bank, "CONFIG_PATH", config),
                  mock.patch.object(cachectl, "CONFIG_PATH", config),
                  mock.patch.dict(os.environ, fixture_environment(), clear=True)):
                server = ThreadingHTTPServer(("127.0.0.1", 0), cacheplus_api.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}/v1/status"
                    with urlopen(url, timeout=3) as response:
                        first = json.load(response)
                    config.write_text(json.dumps({"volume": str(Path(temp) / "two"), "limit_gb": 20}))
                    with urlopen(url, timeout=3) as response:
                        second = json.load(response)
                    self.assertEqual(first["configured_volume"], str(Path(temp) / "one"))
                    self.assertEqual(second["configured_volume"], str(Path(temp) / "two"))
                    self.assertEqual(second["configured_bank_limit_gb"], 20)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)
        bank.refresh_settings()

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
            rejected.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_status_does_not_walk_completed_folders(self):
        sys.path.insert(0, str(ROOT))
        import bank
        with mock.patch.object(bank, "completed_jobs", return_value=[(1, Path("/tmp/old"), None),
                                                                  (2, Path("/tmp/new"), 1024)]), \
             mock.patch.object(bank, "free_gib", return_value=42.0), \
             mock.patch.object(bank, "folder_bytes", side_effect=AssertionError("folder walk")):
            data = bank.status()
        self.assertEqual(data["free_gib"], 42.0)
        self.assertIsNone(data["completed_cache_gib"])
        self.assertFalse(data["completed_cache_size_complete"])


if __name__ == "__main__":
    unittest.main()
