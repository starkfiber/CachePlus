"""Bounded local CachePlus interface latency sample (not a capacity test)."""

import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/cache/scripts"
SAMPLES = 30


def summarize(values):
    ordered = sorted(values)
    return {"samples": len(values), "median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3),
            "max_ms": round(ordered[-1], 3)}


def cli():
    times = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        result = subprocess.run([sys.executable, str(SCRIPTS / "cacheplus.py"), "status"],
                                capture_output=True, text=True, check=True, timeout=10)
        if "free_gib" not in json.loads(result.stdout):
            raise RuntimeError("CLI status missing free_gib")
        times.append((time.perf_counter() - start) * 1000)
    return summarize(times)


def mcp():
    process = subprocess.Popen([sys.executable, str(SCRIPTS / "cacheplus_mcp.py")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    def exchange(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())
    try:
        initialized = exchange({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2025-11-25"}})
        if "result" not in initialized:
            raise RuntimeError("MCP initialize failed")
        times = []
        for index in range(SAMPLES):
            start = time.perf_counter()
            reply = exchange({"jsonrpc": "2.0", "id": index + 1, "method": "tools/call",
                              "params": {"name": "cache_status", "arguments": {}}})
            if "free_gib" not in reply["result"]["structuredContent"]:
                raise RuntimeError("MCP status missing free_gib")
            times.append((time.perf_counter() - start) * 1000)
        return summarize(times)
    finally:
        process.stdin.close()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def api():
    times = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        with urllib.request.urlopen("http://127.0.0.1:8767/v1/status", timeout=5) as response:
            if "free_gib" not in json.load(response):
                raise RuntimeError("API status missing free_gib")
        times.append((time.perf_counter() - start) * 1000)
    return summarize(times)


if __name__ == "__main__":
    print(json.dumps({"cli_fresh_process": cli(), "mcp_warm_process": mcp(),
                      "api_loopback": api()}, indent=2))
